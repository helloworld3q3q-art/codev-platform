# Design: AI 协作工具栈多项目多团队部署

> 状态:📋 **草案,待用户拍板**
> 性质:`team-deploy-2026-05-27.md` 的 Phase 0 展开稿
> 作用:把单项目多人原 plan 升级为**多项目多团队**架构,锁住关键决策点

---

## 一、架构定位:单项目多人 → 多项目多团队

原 plan(`team-deploy-2026-05-27.md`)默认场景 = 1 个 platform/stock 仓库 + 5-20 人。本 design 升级目标:

| 维度 | 原 plan(单项目多人) | 本 design(多项目多团队) |
|---|---|---|
| 仓库数 | 1 | N(stock / 未来项目 A / 未来项目 B) |
| 团队数 | 1 个隐式默认 team | M 个 team,team 与 project 多对多 |
| 索引隔离 | 单 chroma collection + 单 codegraph db | 每 project 独立 collection / db / cross-link slice |
| Memory / rules | platform 单层 + 个人 personal/ | 4 层:platform / project / team / personal |
| Auth | 单 Bearer token | user × project 权限矩阵 + token scope |
| 部署 | 单 server 单 daemon | 单 server 多 project(默认)或多 server(规模化后) |

**核心差异点**:
- 索引产物("索引即知识")必须 **per-project 物理隔离**(防止 stock 业务关键词污染未来 project A 的检索)。
- Rules/memory 出现**跨项目可复用层**(如 git-commit phasing / no-absolute-paths)和**项目专属层**(如 pit-redline / shadow-isolation 是 stock 业务红线),不分层会让新项目背 stock 包袱。
- GPU daemon 是**跨项目共享单例**(模型与 project 无关),但 collection lookup 必须按 project_id 切。

---

## 二、Project namespace 模型

### 2.1 project_id 定义

```
project_id = <org>/<slug>     # 例:openclaw/stock, openclaw/playground
```

- `org`:团队/组织短码(小写,无空格)
- `slug`:项目短码(对应 git remote 仓库名)
- 唯一约束:全局唯一,大小写不敏感
- 真值源:server 端 `projects` 表(见 §5.1)

### 2.2 索引隔离边界

| 工具 | namespace 策略 | 物理体现 |
|---|---|---|
| chroma | 每 project 独立 collection 集合 | `<project_id>__rules` / `<project_id>__memory` / `<project_id>__docs` |
| codegraph | 每 project 独立 SQLite db | `data/codegraph/<project_id>/codegraph.db` |
| cross-link | 每 project 独立 build slice | `data/cross_link/<project_id>/index.db` |
| GPU 模型 | **不隔离**(embedding + reranker 共享) | daemon 进程单例,模型常驻 |

### 2.3 client 怎么指定 project

会话起点确定 project,后续所有 MCP 调用透传:

1. **`.mcp.json` 或 launcher 环境变量** `PLATFORM_PROJECT_ID=openclaw/stock`(client 注入)
2. launcher 把 project_id 加进 MCP 请求 header `X-Project-Id` 或 query string
3. daemon 根据 header 选 collection / db
4. 缺 project_id → 返回 400,不允许 fallback 到"全局"(防错配)

---

## 三、四层 Memory + Rules 分层

### 3.1 分层定义

| 层 | 路径(server) | 路径(client) | autoload 时机 | 内容 |
|---|---|---|---|---|
| **platform** | `platform/memory/*.md` `platform/rules/*.md` | 通过 MCP `search_docs(scope='platform')` | 全员、全 project 默认拉取 | 工具栈使用约定、git commit 纪律、windows-powershell、no-absolute-paths |
| **project** | `<project_id>/rules/*.md` `<project_id>/CLAUDE.md` | 仓库内 `.claude/rules/` + `CLAUDE.md`(原现状) | 仅本 project 会话 autoload | pit-redline、shadow-isolation、cross-layer-enum-consistency(stock 业务) |
| **team** | `<project_id>/teams/<team>/*.md` | MCP 按 user.team 拉取 | team 成员会话 autoload | 团队代码风格变体、内部 review 习惯、值班 SOP |
| **personal** | `<project_id>/users/<user>/*.md` | client 本地 `~/.claude/personal/<project>/*.md`(gitignored) | 仅本人 autoload | 个人语言偏好(中/英)、个人快捷工作流、私人 TODO |

### 3.2 autoload 拼接顺序

```
CLAUDE.md (project 根) + 0.x 核心协议
        ↓
[platform] 跨项目纪律(精简核心,目标 ≤ 30 行)
        ↓
[project] 当前项目专属 rules 索引(不展开正文,按需 search_docs)
        ↓
[team] 当前 user 所属 team 的覆盖项
        ↓
[personal] 个人偏好(语言、终端样式、tone)
```

冲突解析:**后者覆盖前者**(personal > team > project > platform)。但**红线规则(security / pit-redline / shadow-isolation 等带 HIGHEST PRIORITY 标记)不可被下层覆盖**,server 端校验。

### 3.3 文件路径规范

- platform 层:server 仓库 `platform-meta/` 单一真值源,所有 project 通过 MCP 拉取
- project 层:跟仓库走,`<repo>/.claude/rules/` + `<repo>/CLAUDE.md`(现状不变)
- team 层:server `platform-meta/projects/<project_id>/teams/<team>/`(gitops 管理)
- personal 层:client 本地 `~/.claude/personal/<project_id>/`(gitignored,永不上传 server)

---

## 四、24 rules 初步分类草案

> 用于 Phase 5 之前的决策,**逐条需用户拍板**。

| 现 rule | 拟分类 | 一句话理由 |
|---|---|---|
| workflow.md | platform | 任务分级 / MCP 选型,跨项目通用工作流 |
| roles-5-perspectives.md | project(stock) | 5 视角含投研/合规,绑定金融业务 |
| verification-checklist.md | platform | 改后验证矩阵,通用工程纪律 |
| commit-pr-conventions.md | platform | 禁 AI 痕迹,所有项目通用 |
| ai-tools-mcp.md | platform | 工具栈使用规范,本身就是 platform |
| windows-powershell.md | platform | 跨项目通用,只要 Windows 就要 |
| file-discipline.md | platform(§1-3) + project(§4 docs/) | 行数/判重通用;docs 8 子目录是 stock 当前结构 |
| weekly-iteration-cadence.md | platform | 节奏 SOP,可跨项目复用 |
| security.md | platform | 通用安全;具体禁区(免责声明)走 project |
| project-structure.md | project | stock 三层架构图,纯业务 |
| api-contracts.md | project | POST+JSON+枚举来源,stock 选型 |
| model-field-consistency.md | platform(§通用思路) + project(§具体表) | 跨层一致性思路通用;具体字段 stock 专属 |
| cross-layer-enum-consistency.md | platform | 跨语言枚举铁律,新项目大概率复用 |
| frontend-backend-handoff.md | platform | pnpm run api/enums 模式通用 |
| java-layering.md | project | Spring Boot + MyBatis stack 专属 |
| same-day-rerun.md | project | 跑批幂等性,业务语义强 |
| not-null-write-guard.md | platform | DB NOT NULL 三防线模板通用 |
| pit-redline-and-tracks.md | project | 金融 PIT 红线,严格业务红线 |
| shadow-isolation.md | project | shadow_mode 列业务专属 |
| snapshot-trio-write.md | project | 推荐三件套合规,业务专属 |
| capital-amount-semantics.md | project | 4 套金额口径,业务专属 |
| pct-sign-convention.md | project | 止损止盈符号,业务专属 |
| business-sanity-alerts.md | project | 业务闭环 sanity,业务专属 |
| stock-name-display.md | project | 股票上下文展示,业务专属 |

**比例**:11 platform / 13 project / 0 team(team 层后续 organic 长出)。

---

## 五、Auth 模型

### 5.1 数据模型

```
users(id, email, display_name, status)
projects(id, project_id_str, display_name, owner_user_id, status)
project_members(project_id, user_id, team, role)   -- role: OWNER / MAINTAINER / DEV / VIEWER
tokens(id, user_id, project_scope, hash, expires_at, last_used_at)
audit_log(user_id, project_id, action, resource, ts)
```

- `project_scope`:JSON 数组 `["openclaw/stock", "openclaw/playground"]` 或 `["*"]`(超管)
- token 不存明文,只存 SHA256

### 5.2 token 颁发

- CLI:`claude-platform login` 跳浏览器 OAuth → 服务端颁发 token → 写 `~/.claude/credentials.json`(0600)
- token 30 天过期 + refresh
- 撤销:server 黑名单(`tokens.status='REVOKED'`)

### 5.3 `.claude/` 配置怎么引用

```jsonc
// ~/.claude/credentials.json (gitignored)
{
  "platform_url": "https://example.invalid/reference",
  "token": "...",
  "current_project": "openclaw/stock"
}

// <repo>/.mcp.json (committed)
{
  "mcpServers": {
    "platform-docs": {
      "command": "claude-platform-mcp",
      "args": ["docs", "--project", "openclaw/stock"]
    }
  }
}
```

client wrapper 启动时合并 `current_project` 与 `--project` 参数,project 严格匹配仓库归属(防错配)。

---

## 六、工具栈多租户改造

| 工具 | namespace 策略 | 改造工作量 | 关键点 |
|---|---|---|---|
| chroma | collection 前缀 `<project_id>__` | ~3-5 天 | `mcp_server.py` query path 加 project lookup;`index_docs.py` 写入时分桶 |
| codegraph | 每 project 独立 db 文件 + db pool | ~5-7 天 | 现 `.codegraph/codegraph.db` 改 server 端 `data/codegraph/<pid>/`;client 携带 project header;watcher 按 project 分进程 |
| cross-link | 每 project 独立 slice | ~3-4 天 | `build_index.py` 已用 SQLite,加 `--project` 参数 + 输出目录分桶 |
| GPU 模型 | 不隔离 | 0 | embedding / reranker 与 project 无关 |
| daemon launcher | 跨 project 单例 + project header 转发 | ~2-3 天 | `platform_docs_launcher.py` 已有 spawn 串行化锁,扩 project 透传即可 |

**总估**:13-19 天(全职),与原 plan Phase 1-3 部分重叠;不是新增成本,是把"单项目"假设拆开。

---

## 七、部署拓扑(三选项)

| 选项 | 描述 | 优点 | 缺点 | 适用 |
|---|---|---|---|---|
| **A 单 server 多 project**(默认) | 1 台机器 / 1 GPU / 多 collection-db | 成本低、模型共享 GPU、运维简单 | 单点;GPU 跨项目排队 | 1-3 个 project / ≤ 20 人 |
| **B 多 server 每项目独立** | 每 project 一台机 | 项目级故障隔离;GPU 不抢 | 成本翻倍;同步索引脚本复杂 | 大项目独立部署需求 |
| **C hybrid** | 公共 chroma/cross-link 单 server,codegraph per-project server | codegraph 写并发隔离,文档检索共享 | 拓扑复杂,client 路由难 | 中期规模化(5+ project) |

**初期建议 A**;触发切 B/C 的信号:GPU P95 > 5s 持续 / project 间 codegraph rebuild 互相阻塞 / 某 project 合规要求物理隔离。

---

## 八、关键决策点清单

> 每条标 **拍板** = Phase 0 必须用户决定;**可后续迭代** = 先选默认值,跑起来再调。

| # | 决策点 | 性质 | 默认/建议 |
|---|---|---|---|
| D1 | project_id 命名规范(`<org>/<slug>` vs 纯 slug) | 拍板 | `<org>/<slug>`(防未来重名) |
| D2 | personal 层是否上传 server(协作 vs 隐私) | 拍板 | 不上传,client 本地 only |
| D3 | 24 rules 分类草案是否接受(§4 表) | 拍板 | 见 §4 |
| D4 | 红线规则不可下层覆盖,server 端硬校验 | 拍板 | 是 |
| D5 | 部署拓扑选 A / B / C | 拍板 | A |
| D6 | GPU 单 daemon 跨 project 共享是否可接受(项目间会排队) | 拍板 | 可接受;P95 触发再切 hybrid |
| D7 | 索引是否服务端 master 单点 vs 客户端各自跑 | 可后续迭代 | 服务端 master + git webhook |
| D8 | auth 用自建 OAuth vs GitHub OAuth | 可后续迭代 | GitHub OAuth(零运维) |
| D9 | team 层何时引入(organic vs 提前设计) | 可后续迭代 | organic;首版只 platform+project+personal |
| D10 | dirty workspace 处理:server 索引视角 vs client 真实文件 | 拍板 | client 真实文件优先,MCP 仅参考 |
| D11 | 红线 rules 跨项目复用时是否允许 project 端"扩展但不削弱" | 拍板 | 允许扩展,不允许削弱 |
| D12 | 新 project 接入的 onboard 时间预算(目标 < 1 小时) | 可后续迭代 | 目标 30 分钟 |
| D13 | platform-meta 仓库是否公开 / 谁有 push 权 | 拍板 | 私有;owner + 1 maintainer |
| D14 | 启动条件之外的 kill switch(什么情况停 plan) | 拍板 | < 3 人持续用 ≥ 2 周 / Anthropic 推官方版 |
| D15 | 多项目场景下 ai-health / pre-push gate 是否仍单仓库内跑 | 可后续迭代 | 是,gate 留仓库 |

---

## 九、与原 plan 的衔接

- 本 design = 原 plan Phase 0 "写完整 design doc" 的产物
- 原 plan **Phase 1-2(chroma/codegraph HTTP 化)需追加 project header 透传**(§6)
- 原 plan **Phase 5(memory 分层)替换为本 design §3 四层方案**(平台/项目/团队/个人)
- 原 plan **启动条件不变**(CLOSED ≥100、≥1 协作者、GPU 16GB+、用户拍板)
- 本 design 不改时间预算,只把"单项目假设"打散;Phase 1-2 工作量含 §6 估时

---

## 十、状态追踪

| 日期 | 状态 | 备注 |
|---|---|---|
| 2026-05-27 | 草案 v1 | 多项目多团队架构 design,等用户逐条 review §8 决策点 |
| 2026-05-27 | 决策定稿 + Phase a-d 落地 | 见 §十一 |

---

## 十一、决策定稿(用户拍板)

| # | 决策点 | 决策 |
|---|---|---|
| D1 | project_id 命名规范 | 扁平 `<slug>`(连字符分隔, 不带 org/斜杠);当前 = `openclaw-stock`;缺失时硬失败 + 安装指引 |
| D2 | personal 层是否上传 server | **本地 only**(无 server)|
| D3 | 24 rules + 所有 skills 分类 | **全部留 project**, platform 层暂空, 后续 organic 抽 |
| D4 | 红线规则不可下层覆盖 | 同意(暂无 server 端校验, 文档约定)|
| D5 | 部署拓扑 A / B / C | **A 单 server 多 project** |
| D6 | GPU 单 daemon 跨 project 共享 | 暂为单租户(同时只服务一个 project_id), daemon 启动锁定 |
| D7 | server master vs client | 本地原型阶段:client 各自跑;server 化时切 master |
| D8 | auth | 本地 internal.example.invalid 信任, 不做 auth;server 化时上 GitHub OAuth |
| D9 | team 层何时引入 | organic, 首版 platform/project/personal 三层骨架, team 不预建 |
| D10 | dirty workspace | client 真实文件优先, MCP 仅参考 |
| D11 | 红线 rules 扩展但不削弱 | 同意 |
| D12 | 新 project onboard 时间 | 目标 30 分钟(`claude-platform init` + 改 `.mcp.json` env)|
| D13 | platform-meta 仓库 | 当前内嵌当前仓 `platform-meta/`, 未来抽独立私有仓 |
| D14 | kill switch | 本地原型, 无 kill;server 化时设"持续用 ≥ 2 周"门槛 |
| D15 | ai-health / pre-push gate 仓库内跑 | 是, gate 留仓库 |

### scope 收口

- **本地原型,server 部署能力留接口不实现**
- resolver 双模式: `resolve_local()` (本地) + `resolve_from_request(headers)` (server, 已写未启用)
- paths 通过 `PLATFORM_DATA_DIR` 环境变量可整体改基目录, 迁移就是打包 `data/` + `platform-meta/`

---

## 十二、Phase a-d 实施进展(2026-05-27 落地)

| Phase | 内容 | 状态 |
|---|---|---|
| a | launcher + daemon project_id namespace | ✅ `tools/_platform/` 共享模块, chroma 三件套接入(launcher + mcp_server + index_docs), daemon /health 暴露 project_id, 单租户锁定 |
| b | `.claude/project.json` + `platform-meta/` 骨架 | ✅ 当前仓 project_id=openclaw-stock, platform-meta/projects/openclaw-stock/meta.json 注册, platform/ 三子目录(rules/memory/skills)空 .gitkeep |
| c | `claude-platform` CLI 雏形 | ✅ `tools/claude-platform/cli.py` + `.cmd` wrapper, 命令: init / current / list-projects / validate |
| d | codegraph + cross-link namespace | ✅ codegraph 天然 per-repo 不动;cross-link build_index + mcp_server + schema 改 per-project DB 路径 + legacy fallback |
| e | smoke test + ai-health 集成 | ✅ `tools/_platform/_smoke_test.py` 全绿;ai-health.ps1 集成留待需要时增量加 |

### 已知后续动作(用户手动触发)

1. **重启 chroma daemon** 让新代码生效(可选, 不重启 daemon 继续跑旧 collection)
   ```powershell
   # 看 daemon pid
   Get-NetTCPConnection -LocalPort 18083 | Select-Object OwningProcess
   # kill, 下次 Claude Code 会话起来自动 spawn 新 daemon
   ```

2. **重跑 chroma 索引**(可选)写入带 project_id 前缀的新 collection;不重跑会自动 fallback 到 legacy
   ```powershell
   tools/chroma/.venv/Scripts/python.exe tools/chroma/index_docs.py
   ```

3. **重跑 cross-link build_index** 写入 `data/codegraph_ext/openclaw-stock/cross_layer.sqlite`(同样不跑会 fallback)
   ```powershell
   tools/chroma/.venv/Scripts/python.exe -m tools.cross_link.build_index
   ```

### 接入第二个项目时

1. 在新 project 仓库根跑: `python <platform>/tools/claude-platform/cli.py init <new-pid>`
2. 改 `.mcp.json` 让 launcher 拿到新 pid(默认从 `.claude/project.json` 读, 通常无需手动设)
3. `claude-platform list-projects` 验证已注册
4. 新 project 第一次 search_docs / cross-link 查询会触发各自 collection / DB 的首次建立


