# roadmap-2026-06-01 完成度核实报告(2026-06-04)

> 对 `roadmap-2026-06-01`(模块化核心 + 插件化扩展)整轮完成度的**代码级核实**。
> 结论:基于 plan 文档/记忆的盘点**系统性高估了"未做"** —— 约 30 项"未做"中,
> 至少 9 项真实代码已落地。真实剩余 ≈ 15-18 项,集中在 Agent Memory M1+ 与 Connector。

---

## 一、背景与动机

口径盘点(读 plan + 日报)得出"roadmap-2026-06-01 约 25 做 / 30 未做"。但本仓近一个月演进极快
(358 commits),**文档盘点滞后于代码真实状态**已被本轮多次证实(如 `java_endpoint` 早在
`006a139` 改名、crosslink 页 `c0ed297` 退役、web `_noop_trigger` 已接线)。故对该盘点做一次
Bash 直读磁盘的对账,把"plan 说没做"逐项核到代码是否真没做。

---

## 二、核实方法

- **只读磁盘真值**:`ls` / `grep` 直读工作树(git HEAD 干净),不信 plan 文档、不信工具缓存。
- **存在性维度**:核实模块/路由/工具/测试文件是否真实存在 + 关键符号是否落地。
- **区分两层**:本报告核的是**代码存在性**;"端到端功能完整性"(数据能否跑通)是另一层,
  对受影响项单独标注(见 §四 缺口 3)。

---

## 三、对账结果

### 3.1 False Negative —— 盘点说"未做",代码里已做(9 项)

| # | 盘点条目 | 代码真实状态(证据) |
|---|---|---|
| 1 | **缺口#1 影响分析链路"完全没做"** | **三层全在**:`web/routes/reports.py` 4 端点(`/reports/impact` `table_usage` `page_dependencies` `api_callers`)+ `graph/impact.py:generate_impact_report` 引擎 + `agent/tools/impact.py` 4 工具(`impact_analysis`/`table_usage`/`api_callers`/...) |
| 2 | A-Reports 模块整体未做 | `reports.py` 完整, 吃统一图谱 store 出跨层影响报告(README 核心卖点) |
| 3 | A-Phase7 agent/memory/audit/reports 四路由 | `web/routes/` 下 `agent.py memory.py audit.py reports.py` 全在 |
| 4 | A-Phase8 契约测试(operationId 唯一/快照) | `tests/test_web_contract.py` 在 |
| 5 | A-web serve CLI "未注册" | 已注册:`cli.py` `sp_web` + `cmd_web`, `web serve` 端到端 parse 实测通过 |
| 6 | A-integrations 只有 codegraph_client | 还有 `agent_client.py` |
| 7 | B-M3 生命周期(压缩/forget/摘要) | `memory_maintenance.py`(compress_topic/archive_expired)+ `memory_store_pg.py`(forget/archive/supersede)全在 |
| 8 | C-Phase5 Agent 影响工具"完全没做" | `impact.py` 已暴露 4 工具(命名未用 `find_` 前缀, 能力等价) |
| 9 | C-Phase7/8 Demo + POC "未启动" | `demo/` 目录 + 仓根 `docker-compose.yml` 已存在(已起步) |
| + | D-test_file_size_budget 静态断言"未做" | 早已存在 + 防僵化测试; 2026-06-04 本轮又扩展(拆 4 大文件后收紧白名单) |
| + | C-Phase6/M6 Connector 的 **Git 接入** | `webhook/`(server + providers)接 gitea/gitlab **push→reindex 写队列**完整链路 + 多 provider 抽象 —— **Git connector 已做**(盘点把整个 Connector 列空, 漏看 Git) |

### 3.2 True Negative —— 核实确认未做(7 项)

| # | 项 | 证据 | 归属 |
|---|---|---|---|
| 1 | **B-M1 任务记忆(task_id/task_memory/task_state)** | 全仓 grep `task_id` **零命中** | Memory 最大空白, 大特性开专轮 |
| 2 | B-M2 Context Engineering(budget/context_plan/分组) | 无 `context_plan`/`context_budget` | Memory |
| 3 | B-M5 memory benchmark(10 万条/P95) | 无 benchmark 测试 | Memory |
| 4 | B-M6 / C-Phase6 Connector — **Jira/飞书/Wiki**(+CI) | 除 schema 节点类型定义(`wiki_page`/`jira_issue`)外**零实现**。**注**:同列的 **Git 接入已做**(webhook push→reindex), 不在此; CI 未做 | 文档/任务接入空 |
| 5 | B-M7 多模态/行为记忆 | 无 | Memory |
| 6 | A-退役 Java codegraph-api(:18082)切流 | ~~代码仍 5 处引用 18082~~ **2026-06-04 复核纠正:5 处全是注释/docstring 历史说明("已退役"/"从 :18082 迁移"/"替代"),运行时 0 依赖 —— 无 HTTP 调用(grep requests/httpx/api_url→空)、`_query/_check_codegraph_api` 已移除。本条系把注释当依赖的误判** → **实为已完成** |
| 7 | B-M4 四层权限闭环 | audit 路由在, "四层闭环"完整度需细核(本报告未深核) | 待细核 |

---

## 四、修正后的三大真实缺口

盘点原列的"三个最大缺口"有一个判反(缺口#1 实为已做)。修正后:

1. **Agent Memory M1 任务记忆** —— `task_id` 全空。这是 Memory 平台化的核心大特性
   (M0 doctor / M3 生命周期已做, M1 任务记忆闭环是大头)。**2026-06-04 纠正**:此前据
   `agent_sessions=0` 判"agent 从未被用"系**误判** —— 那是 `session_backend=memory` 默认
   (inmemory 不落 PG)所致;journalctl 实证 agent 端到端跑通过(deepseek LLM + MCP 工具)。
   真缺口是**写侧闭环空**:agent loop 无 `remember` 工具, 对话不沉淀 memory(唯一写入人工 HTTP)
   → Memory 当前"读侧建完、写侧空转", M1 正该补这一环。
2. **影响分析的数据桥接(endpoint→表)** —— **代码链路完整(缺口#1 已做), 但 codev dogfood 数据断**:
   `service→store` 的 Python DI(`self._store.xxx()`)在 codegraph 的 calls 图里缺边, BFS 从
   route 经 service 到不了 PG store 碰表函数 → endpoint→表可达 = 0(2026-06-04 实测:
   `_find_handler` 59/59 成功, 但 **BFS join 0/59**;断点纯在 service→store 这一跳, 非匹配问题)。
   **2026-06-04 核实(端到端冒烟, 真实 store)**:
   - codev-platform store 309 节点/310 边, 四跳三通一断: calls_api 51 ✅ / **calls 0 ❌** /
     reads+writes_table 114 ✅。
   - 业务仓 openclaw-stock 同链路 **calls=155 边**, table_usage 多表反向可达前端
     (edges 表→6 前端 / covered→4 / files / latest...)。
   - **判定**:卖点对**真实客户(业务仓)端到端可用**, 断点是 codev **纯 Python 严格 DI 架构**特有。
     商业优先级低 → **确认放 backlog(P3+)**。修法(扫 service `__init__` 的 `self._store` DI 字段
     类型 → 物化 service→store calls 边)脆弱, 待 dogfood 价值真需要时再做。

**⏸ 优先级靠后(2026-06-04 定)**:**文档/任务类 Connector —— Jira / 飞书 / Wiki**(M6/Phase6)
**暂不排期**(同 Connector 列的 **Git 接入已做**=webhook push→reindex, CI 未做归其他 backlog);
M2 Context Engineering / M4 权限闭环 / M5 benchmark / M7 多模态 / Java api 退役切流 同列靠后。
**当前焦点 = Memory M1 + 写侧闭环、影响分析数据桥接两项。**

> **全栈血缘 P1/P2 子专题(本会话已完成)**:SQLAlchemy Core 检测 gap 已彻底闭环 ——
> P1(`3d0e446`)Core Table 节点 + 排除幽灵表; P2(`3454dac`)Core DML 读写血缘
> (account_store_pg 13 + rbac_store_pg 12 = 25 条碰表边, 之前 0)。`find_table_refs` /
> `table_usage` 现可查到函数→表血缘。缺口 3 的 endpoint→表是独立的 DI 桥接问题。

---

## 五、本轮(2026-06-04)交付

**大文件按 file-discipline 600 行预算拆包**(commit `45e8144`):

| 原文件 | 行数 | 拆成 |
|---|---|---|
| `cli.py` | 872→372 | `cli_cmds/`(_shared/config_cmd/sync/setup_cmd/mcp) |
| `ops/reindex.py` | 661 | `reindex/`(logs/proc/dispatch/commands) |
| `plugins/builtin/_stack_scan.py` | 863 | 包(react/vue/fastapi/node/spring) |
| `plugins/builtin/sql.py` | 1254 | 包(ddl/orm/core) |

- 纯结构移动零逻辑改动; 各包 `__init__` re-export 保兼容。
- 质量门:3 兄弟并行拆 + 独立审计兄弟(逐符号 + 20 正则 byte-identical)+ 独立 review 兄弟
  (MD5 逐函数比对)+ 全套 pytest **857 passed / 0 failed**。
- 过程暴露并修复 1 个回归(cli 漏 re-export `_RULES_SRC`)。
- `test_file_size_budget` 白名单从 5 条收紧到 1 条(仅留退役中的 `cross_link/server.py`)。

---

## 六、元教训:文档盘点滞后于代码

本轮再次印证(继 deep-audit-2026-06-03-review 之后):**plan/日报口径的"未做"清单不可直接信**,
因代码推进快于文档更新。约 30 项"未做"中 9 项实为已做,且把"最大缺口"判反。

**建议沉淀机制**:
- 盘点完成度时**先 Bash 直读磁盘核实存在性**, 再下"未做"结论;
- 关键卖点(影响分析链路等)配**端到端冒烟测试**, 让"做没做"从"读 plan"变成"跑断言";
- plan 文档的任务项标 ✅ 时附 commit hash / 文件路径, 便于反查。

---

## 七、下一步建议(按 ROI)

1. **影响分析端到端冒烟** —— 缺口#1 代码在, 先验证 `/reports/impact` 实际能否出正确报告
   (受 endpoint→表数据 gap 影响), 把"代码存在"升级到"端到端可信"。最高 ROI(已有地基)。
2. **Agent Memory 写侧闭环** —— 给 agent 加 remember 工具 / 对话后提炼, 让 Memory 不空转
   (前提是 agent 对话被用起来)。
3. **M1 任务记忆** —— 大特性, 开专轮。
4. **⏸ 靠后(2026-06-04 定)**:Connector / Java api 退役切流 / M2/M4/M5/M7 —— **暂不排期**。

---

## 附录:核实命令(可复现)

```bash
ls codev_platform/web/routes/*.py            # agent/memory/audit/reports 四路由
ls codev_platform/web/integrations/*.py      # codegraph + agent client
ls codev_platform/agent/tools/*.py           # impact.py 4 工具
grep -rn "task_id" codev_platform/ --include=*.py   # M1: 零命中
grep -rln "jira|feishu|wiki" codev_platform/ --include=*.py  # Connector: 仅 schema 节点定义
grep -rln "18082" codev_platform/ --include=*.py    # Java api 退役: 仍 5 处引用
grep -nE "report_impact|generate_impact_report" codev_platform/web/routes/reports.py
```
