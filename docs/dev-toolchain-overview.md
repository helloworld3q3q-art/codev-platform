# 开发工具栈总览

> 一份 md 看完平台所有开发工具:三层架构 / 规则 / skill / 数据库 / MCP / 大模型 / 开发流程。
> 细节深入见 `docs/ai-toolchain-guide.md`(AI 工具栈)+ 各子模块 `CLAUDE.md` + `.claude/rules/`。
> 本文档**只列入口和速查**,不重复正文规则。

---

## 一、三层架构(端到端数据流)

```
外部行情 API (AkShare / Baostock / EastMoney / Sina / Sohu / Tencent / Netease)
   │
   ▼
Python 数据管道 (python/stock-pipeline)           ← 数据采集 / 算法 / 信号 / 推荐
   │  写库: stock_quote_daily / stock_valuation_daily / stock_recommend_result ...
   ▼
PostgreSQL (stock_sync_job 任务总线 + 时序 + 分析快照)
   ▲
   │  读查
Java API 服务 (apps/stock-admin-api, 端口 18081)   ← 业务接口 / 鉴权 / 缓存
   │  Controller / Facade / MyBatis-Plus / Mapper / Flyway / 29+ 业务枚举
   ▼ HTTP (POST + JSON, result===0 表示成功)
React 管理后台 (apps/stock-admin-web, 端口 8000)   ← UI / 配置 / 看板
   │  Umi Max 4 + Ant Design 6 + UnoCSS + @jlogi/ui
   │  20+ 业务模块: dashboard / recommendresults / tradeplans / backtest ...
```

**铁律**:Python 不直接给前端调,Java 不直接调外部行情 API。任何跨外部 API 抓取通过 `stock_sync_job` 表派发给 Python。

---

## 二、技术栈一览

| 层 | 语言 / 框架 | 关键依赖 | 入口 |
|---|---|---|---|
| Java API | Java 17 + Spring Boot 3.3 | MyBatis-Plus 3.5 + Flyway 9 + Knife4j + Caffeine | `apps/stock-admin-api/` |
| React 前端 | TypeScript + Umi Max 4 | Antd 6 + UnoCSS + @jlogi/ui + dayjs + ECharts | `apps/stock-admin-web/` |
| Python pipeline | Python 3.13 (nvm uv) | psycopg 3 + pandas + akshare + baostock + sentence-transformers | `python/stock-pipeline/` |
| 数据库 | PostgreSQL 15+ | Flyway 自动管理 schema | `database/init/` (dump) + `apps/stock-admin-api/src/main/resources/db/migration/` (forward) |
| Node | nvm v20.19.0 | pnpm + npm | `.nvmrc` 实际由 nvm 全局管理 |

---

## 三、SQL / 数据库

### 表清单(常用,完整见 Flyway 历史)

| 类别 | 表 |
|---|---|
| 元数据 | `stock_info` / `stock_pool` / `stock_pool_item` / `sys_user` / `sys_menu` / `sys_role_menu` / `sys_audit_log` |
| 时序快照 (PIT 红线保护) | `stock_quote_daily` / `stock_valuation_daily` / `stock_fund_flow_daily` / `stock_lhb_event` / `stock_earnings_forecast` / `stock_financial_snapshot` / `stock_intraday_session_daily` / `stock_margin_trading_daily` / `stock_index_daily` / `stock_indicator_daily` |
| 分析产物 | `stock_analysis_run` / `stock_analysis_result` / `stock_recommend_result` (含 shadow_mode) / `stock_alert_event` / `stock_recommendation_track` |
| 决策 / 计划 | `stock_four_w_plan` / `stock_trade_plan` / `stock_trade_plan_four_w` |
| 任务总线 | `stock_sync_job` (PENDING → RUNNING → SUCCESS/FAILED/CANCELLED) |
| 诊断 | `stock_signal_validation` / `stock_factor_ic` / `stock_performance_report` / `stock_industry_score` |
| 回测 | `stock_backtest_run` / `stock_backtest_position` / `stock_backtest_result` |
| 治理 | `phase_transition_log` (Track 4/5 样本门控审计) |

### Flyway 规范

- **已发布的 migration 禁回改**,新需求只能加 forward migration (`V<日期><序号>__<描述>.sql`)
- 命名:`V20260524_NNNN__verb_what.sql`,4 位序号同日避免冲突
- skill `add-flyway-migration` 走 5 步流程(含本地预跑 + 重启校验)

### PostgreSQL 注意

- `#{param} is null` 必须 cast: `cast(#{param} as text) is null`
- jsonb 列写入禁用 `IService.save(Entity)`,必须走自定义 `@Insert + cast(?? as jsonb)`
- 详见 `apps/stock-admin-api/.claude/rules/sql-patterns.md`

---

## 四、Java 后端

### 分层

```
controller/                @Valid 校验入参,调 Facade,不写业务
facade/                    业务编排,@Transactional 在此层
domain/{entity,service}/   Entity (@Data + @TableName) + IService CRUD
infrastructure/mapper/     自定义 @Select / @Insert SQL
dto/                       请求 / 响应,@Schema(description, example) 完整
common/                    CommonResult / PageResult / GlobalExceptionHandler / RateLimit / Caffeine
enums/                     19+ 业务枚举,实现 BaseEnum,注册到 EnumMetadataService
```

### 启动命令

```bash
cd apps/stock-admin-api
mvn spring-boot:run           # 端口 18081
mvn compile                    # 编译
mvn test                       # 175 用例基线,不许下降
```

Swagger UI: <https://example.invalid/reference>

### 子模块规则 (`apps/stock-admin-api/.claude/rules/`)

`code-quality.md` / `comments.md` / `enum-patterns.md` / `pagination-patterns.md` / `sql-patterns.md`

### 关联 skill

| 场景 | skill |
|---|---|
| DTO / 接口改动 | `/backend-dto-change`(7 步含前端 typings 同步) |
| 新增业务枚举 | `/add-enum`(3 步走 + 前端 useModel 接入) |
| Flyway migration | `/add-flyway-migration`(5 步) |

---

## 五、Python pipeline

### 目录职责

```
stock_pipeline/
  indicators/    MA / MACD / RSI / 布林带 / KDJ / ATR
  rules/         规则引擎 (p0_rules / p1_rules / p2_rules)
  fusion/        信号融合 (加权平均置信度)
  recommenders/  推荐 + market_regime + 相关性过滤
  alerts/        告警生成 + severity 分级
  planning/      4W 计划
  analysis/      多周期 / 主力资金 / 回撤 / 追踪 / 交易日历
  backtest/      回测引擎 + walk-forward + Bayesian
  shadow/        影子规则平台 (禁止进入 buy_list)
  repositories/  postgres_{read,write,jobs,helpers}.py
  jobs/          后台任务模块 (DAILY_PIPELINE / QUOTE_SYNC / ...)
  diagnostics/   IC / 性能 / 换手率 / track_sanity
  fetchers/      多源调度 + 限速 + 熔断 + 质量评分
  governance/    Wilson 样本门控 + phase_manager
  performance.py @timed 装饰器统一性能埋点
```

### 启动命令

```bash
cd python/stock-pipeline
python -m pytest tests/                          # 1228 用例基线
python -m stock_pipeline.config.validate         # 启动前环境体检
python run_daily_pipeline.py                     # 每日完整分析
python optional/run_pending_jobs.py              # 消费任务总线
```

环境变量: `STOCK_DB_DSN` (必填) / `PIPELINE_DRY_RUN=true` (只算不写库)

### 子模块规则 (`python/stock-pipeline/.claude/rules/`)

`code-quality.md` / `comments.md` / `pipeline-patterns.md` / `testing.md`

### 关联 skill

| 场景 | skill |
|---|---|
| 新增 Python job | `/add-python-job`(6 步含 Java 枚举对齐) |
| 同日重跑 / 节假日重跑 | `/same-day-rerun`(PIT 红线保护) |
| 验证每日跑批 | `/verify-pipeline-run`(morning / eod / all 三档) |
| 回测分析 | `/analyze-backtest`(对比两个 run / 单 run 深挖) |

---

## 六、React 前端

### 关键约定

- 接口统一 **POST + JSON body**,无路径参数,无 query string
- 业务枚举走 `useModel('enum').getFormattedEnums('XxxEnum')`,**禁硬编码**
- 时间格式 `dayjs(value).format('YYYY/MM/DD HH:mm:ss')`,**禁单库 wrapper**
- 跨股票上下文必带 `stockName` (`stock-name-display.md`)
- 涨跌颜色按 A 股红涨绿跌 (`stock-color-convention.md`)

### 启动命令

```bash
cd apps/stock-admin-web
pnpm install                  # 装依赖 (用户手动跑)
pnpm dev                      # 端口 8000 (用户手动跑)
pnpm run api                  # 后端 DTO 改动后,重生成 typings.d.ts
pnpm run enums                # 后端枚举改动后,重生成 enumslocal.tsx
pnpm exec eslint <file>       # 单文件 lint (AI 可跑)
```

### 子模块规则 (`apps/stock-admin-web/.claude/rules/`)

`architecture.md` / `code-quality.md` / `react-patterns.md` / `component-patterns.md` / `api-service.md` / `stock-color-convention.md` / `styles.md`

### 关联 skill

`/add-frontend-page`(路由 + sys_menu Flyway 联动 + tab 标题)

---

## 七、Rules 体系(`.claude/rules/`)

平台 rules 26+ 份,**默认不 autoload**(避免占爆 context),按需通过 `search_docs(module='platform')` 拉取。

### 唯一 autoload 例外

`.claude/rules/roles-5-perspectives.md`(22 行,5 视角评审框架)

### 规则总索引

`.claude/rules/index.md`(26 份规则路径清单)

### 关键规则速查

| 主题 | 规则文件 |
|---|---|
| 全栈工作流 + 任务分级 (L1-L4) | `workflow.md` |
| MCP 工具栈 + 触发指南 | `ai-tools-mcp.md` |
| 提交规范(禁 AI 痕迹) | `commit-pr-conventions.md` |
| Windows PowerShell 红线 | `windows-powershell.md` |
| 接口契约 / 枚举来源 | `api-contracts.md` |
| 跨层字段一致性 | `model-field-consistency.md` |
| 跨语言枚举一致性 | `cross-layer-enum-consistency.md` |
| Java 分层 + PG SQL | `java-layering.md` |
| 任务总线幂等性 | `/add-python-job` skill |
| 新 fetcher 真实集成测试 | `/add-python-job` skill §🚨 fetcher 强制 |
| NOT NULL 写入护栏 | `not-null-write-guard.md` |
| 五轨并行 + 样本门控 + PIT 红线 | `pit-redline-and-tracks.md` |
| 影子规则数据隔离 (9 SQL 过滤点) | `shadow-isolation.md` |
| 推荐快照三件套 + policy_version | `snapshot-trio-write.md` |
| 资金 4 套口径 | `capital-amount-semantics.md` |
| 百分比符号约定 | `pct-sign-convention.md` |
| 业务闭环 sanity 告警 | `business-sanity-alerts.md` |

---

## 八、Skills 体系(`.claude/skills/`)

按场景触发的多步流程真值源:

| Skill | 用途 |
|---|---|
| `/backend-dto-change` | Java DTO/接口改动 7 步(含 typings 同步) |
| `/add-enum` | 新增业务枚举 6 步(Java + Python 白名单 + 前端 useModel) |
| `/add-flyway-migration` | 新 Flyway 5 步 |
| `/add-frontend-page` | 新前端页面 + 菜单 (sys_menu Flyway 联动) |
| `/add-python-job` | 新 Python job 6 步 (任务模块 + dispatch + JobTypeEnum) |
| `/same-day-rerun` | 同日重跑 (PIT 红线保护) |
| `/verify-pipeline-run` | 验证每日跑批 (morning / eod / all) |
| `/analyze-backtest` | 回测分析 (对比 / 深挖) |
| `/git-commit` | 标准 commit + 兜底 post-commit + 校验 reindex.log |
| `/ai-health` | AI 工具栈 12 项体检 + dirty-check |
| `/update-local-ai` | 手工重建本地 AI 索引 (all / chroma / codegraph) |

触发方式:用户输入 `/<skill-name>` 或自然语言匹配描述。

---

## 九、MCP 工具栈(三件套)

> 完整文档 `docs/ai-toolchain-guide.md`。本节只列入口 + 强制规则。

### MCP 服务清单(`.mcp.json`)

| Server | type | 端口 / 调用 | 用途 |
|---|---|---|---|
| codegraph | sse | 端口 18091(mcp-proxy 多租户代理) | 代码图谱(symbol / 调用链 / impact) |
| platform-docs | sse (内部 daemon HTTP) | 端口 18083(daemon) | 文档语义检索(Chroma + Qwen embedding + reranker) |
| graph(统一图谱) | sse | 端口 18092 | 跨层业务链 + 前端组件依赖(endpoint ↔ Mapper ↔ table ↔ Python ↔ 页面)+ A1 业务域 |

### 优先级铁律(`ai-tools-mcp.md`)

**MCP 优先于 Grep / Read**。允许兜底场景仅 4 种:
1. 未提交改动命中查询范围(索引最新到 HEAD)
2. 怀疑索引滞后(commit < 60s 内或 `ai-health` 报 stale)
3. MCP 工具不可用(server 崩 / db locked / 网络)
4. 需要确认最新源码(MCP 返回片段后核对具体行号)

### 触发速查

| 问题类型 | 工具 |
|---|---|
| 找符号定义 | `codegraph_search` / `codegraph_node` |
| 找调用链 / 影响面 | `codegraph_callers` / `codegraph_callees` / `codegraph_impact` |
| 想 "怎么实现 X" / 架构问题 | `codegraph_context`(PRIMARY,一次拿 search+node+callers+callees) |
| 找规则 / 设计文档 / 事故复盘 | `search_docs(query, category?, module?)` |
| 找跨层业务链路 | graph `find_table_usage` / `find_api_callers` / `find_impacted_pages` / `search_nodes` |

### 索引更新

```bash
# post-commit hook 自动触发(看改动文件类型决定 scope: chroma / codegraph / graph)
# 手动兜底
powershell -File tools/dev/post-commit.ps1
powershell -File scripts/codegraph/rebuild_index.ps1 -Full     # codegraph 全量
powershell -File tools/dev/update-local-ai.ps1                 # 三库一键
```

---

## 十、大模型 + GPU

### 模型清单

| 模型 | 路径 | 用途 | 显存 |
|---|---|---|---|
| `Qwen3-Embedding-0.6B` | `D:\models\Qwen3-Embedding-0.6B` | platform-docs 语义检索 embedding (1024 维, 32K context, query-prompt enabled) | ~1.5 GB |
| `Qwen3-Reranker-0.6B` | `D:\models\Qwen3-Reranker-0.6B` | 语义检索 rerank (yes/no token logits) | ~1.5 GB |

合计 daemon 占 GPU ~3 GB。

### GPU 配置(本机)

| 项 | 值 |
|---|---|
| GPU | NVIDIA RTX 5060 Laptop |
| VRAM | **8 GB**(WDDM 模式,Windows 系统共享) |
| CUDA | 12.8 (PyTorch 2.11 nightly cu128) |
| Driver | 573.66 |

### Daemon 模式(2026-05-24 起,多会话必需)

8 GB GPU 上**每个 stdio mcp_server 进程独立 load 模型 = 3 GB**,第 2 个 Claude Code 会话立即 CUDA OOM。解决方案:

```
[Claude Code session A] ─→ mcp-proxy.exe ─┐
[Claude Code session B] ─→ mcp-proxy.exe ─┤
[Claude Code session C] ─→ mcp-proxy.exe ─┴─→ daemon (1 份模型 ~3GB 端口 18083)
[Codex session]         ─→ mcp-proxy.exe ─┘
```

- **daemon**: `python.exe + CREATE_NO_WINDOW + mcp_server.py --http`(后台无窗口,prewarm 模型)
- **launcher**: 每会话 `tools/chroma/platform_docs_launcher.py` 检测 daemon + spawn(只第一次)+ exec `mcp-proxy.exe`
- **mcp-proxy**: 开源 `sparfenyuk/mcp-proxy`,stdio↔SSE 透明转发,**1 份 Qwen 模型 / N 个会话共享 GPU**

### 故障速查

| 现象 | 第一步处理 |
|---|---|
| platform-docs 第一次 search_docs 超时 | 冷启动模型 (30-60s)。已有 prewarm 应消除,若仍超时**别凭超时下结论** |
| daemon 起不来 | 看 `<PLATFORM_DATA_DIR>/logs/chroma_daemon.log`(端口被占 / 模型路径不对) |
| 多会话 CUDA OOM | 用 daemon 模式(默认开启)。`ai-health` 看 `platform-docs servers` 是否多个 stdio 进程 |
| `forrtl error 200 window-CLOSE` | daemon 用了 `pythonw.exe + DETACHED_PROCESS`(已 fix,改用 `python.exe + CREATE_NO_WINDOW`) |

---

## 十一、开发流程(workflow.md 任务分级)

### L1-L4 分级入口

| 等级 | 适用范围 | 必做动作 |
|---|---|---|
| **L1 小改** | 文案 / 注释 / 局部样式 / 单文件小 bug | `git status -s` + 定向读 + 最小验证 |
| **L2 单模块** | 单模块逻辑 / 组件 props / repository 实现 | 相关模块 rules + codegraph 调用方 + 定向验证 |
| **L3 跨层** | DTO / API / SQL / Python 写库 / Flyway / enum / 推荐 / 资金 / 百分比 / shadow / snapshot | platform-docs + codegraph + graph + 验证闭环 |
| **L4 高风险** | 生成文件 / 已发布 migration / AI 索引 / 依赖 / 全局 runtime / `.mcp.json` | 先说明影响范围;禁改项拒绝或走正确流程 |

### 跨层业务链 → 必查 graph

凡涉及 sql ↔ py / sql ↔ java / java ↔ 前端 / 跨语言枚举 → **改前必先 graph**(`find_table_usage` / `find_api_callers`),不许只改单层。

### 改前门禁声明

每次任务首次修改前必须说一句:**本次触及 \<层\> / 适用规则 \<文件\> / 关键约束 \<一句话\> / 验证方式 \<命令\>**

### Commit 规范

- conventional commits(`feat` / `fix` / `chore` / `docs` / `refactor` / `test` / `perf`)
- **禁 AI 痕迹**:不带 `Co-Authored-By: Claude` / 🤖 / `contact@example.invalid`
- skill `/git-commit` 自动走 commit + 兜底 post-commit + 校验 reindex.log

### Pre-push gate(3 项静态)

`tools/dev/pre-push-audit.ps1` 强制跑:
1. `check_entity_dataclass_parity.py`(Python ↔ Java ↔ Flyway 字段三层一致)
2. `audit-mapper-null-cast.ps1`(Mapper SQL `#{var} is null` cast)
3. `pytest tests/test_track_sanity_check.py`(业务闭环 sanity 回归)

---

## 十二、常用脚本入口

```bash
# AI 工具栈
powershell -File tools/dev/ai-health.ps1 -Mode Light        # 12 项体检
powershell -File tools/dev/post-commit.ps1                  # 手动触发 reindex
powershell -File tools/dev/update-local-ai.ps1              # 三库重建
powershell -File scripts/codegraph/rebuild_index.ps1 -Full  # codegraph 全量

# Java(用户手动跑)
mvn -f apps/stock-admin-api/pom.xml spring-boot:run
mvn -f apps/stock-admin-api/pom.xml test

# 前端(用户手动跑)
pnpm --dir apps/stock-admin-web dev
pnpm --dir apps/stock-admin-web run api
pnpm --dir apps/stock-admin-web run enums

# Python
cd python/stock-pipeline && python -m pytest tests/
python run_daily_pipeline.py
python optional/run_pending_jobs.py
```

---

## 十三、关键文档地图

| 主题 | 文件 |
|---|---|
| 入口 / 角色 | `CLAUDE.md`(根) |
| 规则总索引 | `.claude/rules/index.md` |
| 工作流真值源 | `.claude/rules/workflow.md` |
| AI 工具栈完整说明 | `docs/ai-toolchain-guide.md` |
| 项目结构 | `.claude/rules/project-structure.md` |
| 当前 roadmap | `docs/architecture/roadmap-<当前周>/README.md` |
| 累积期纪律真值源 | `.claude/rules/pit-redline-and-tracks.md` |
| 历史 changelog | `docs/architecture/changelog.md` |
| 5 视角评审框架 | `.claude/rules/roles-5-perspectives.md` |
| Java 子模块 | `apps/stock-admin-api/CLAUDE.md` + `.claude/rules/` |
| 前端子模块 | `apps/stock-admin-web/CLAUDE.md` + `.claude/rules/` |
| Python 子模块 | `python/stock-pipeline/CLAUDE.md` + `.claude/rules/` |
| Chroma daemon 运维 | `tools/chroma/README.md` |
| CodeGraph 运维 | `scripts/codegraph/README.md` |

---

## 十四、5 视角评审框架(强制)

任何改动从 5 个视角同时审视:

| 视角 | 关注点 |
|---|---|
| 量化交易专家 | 指标算法正确性 / 回测真实性 / 信号权重 |
| 证券投研总监 | 投研流程完整性 / 信号可解释性 / 报告标准 |
| 交易所合规专家 | 涨跌停 / T+1 / 风险提示 / 免责声明 / 审计 |
| 资深个人投资者 | 实战可用性 / 止损止盈合理性 / A 股特色 / UI 易懂性 |
| 金融系统架构师 | 性能 / 缓存 / 限流 / 监控 / 数据一致性 |

详见 `.claude/rules/roles-5-perspectives.md`(22 行,**唯一 autoload 规则**)。
