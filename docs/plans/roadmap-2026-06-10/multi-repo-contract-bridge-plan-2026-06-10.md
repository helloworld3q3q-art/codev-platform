# Plan — 多仓项目跨层链路:operationId 契约桥(2026-06-10)

> **本文件是 plan, 不是实现。** 承 2026-06-10 4 视角专家面板(图谱/IR · 平台多租户 · 微服务契约 · ROI)
> 对"前后端分离多仓 + 一逻辑项目 N 前端 M 后端"的讨论收敛。
> **核心节奏: validate-first + 先做不分仓也受益的那块, 重型多根索引冻结到真有项目卡住。**

---

## 一、问题 / 目标

**问题**: 平台硬假设"1 个 `project_id` = 1 个 repo(单源根)"(`graph/ingest.py:run(repo_path)` 单根 / `codegraph/server.py` 按 pid 取单 repo_path / chroma 单根)。跨层图谱(前端页→api_call→endpoint→表)只在 monorepo 内工作。真实项目常是**前后端分离多仓**, 且**一逻辑项目可能 N 前端 + M 后端微服务**。

**目标(本轮)**: 把"前端调哪个后端接口"这条桥**做对**, 让它**不依赖前后端同仓**, 顺带修当前多服务下就潜伏的错配 bug + 白送契约漂移检测。

**非目标(本轮明确不做)**: 多根索引进一图(A 变体)、codegraph 多仓 fan-out、后端 RPC/MQ 互调建图、跨 org 多仓、自动服务发现 —— 冻结到 Gate(§五)。

## 二、专家面板收敛(摘要)

**4 人共识**:
1. **纯 URL 匹配是真痛点**: `_stack_scan/_link.py:link_api_calls` 的 `by_url[url]` 扁平字典 + `candidates[0]`, 多服务下同 path(`/list`)**静默乱连**到错服务, method 容差还把错连标 conf=0.7。**当前 openclaw-stock 这类多模块后端就潜伏此 bug。**
2. **operationId 才是正确跨仓主键**: 前端本就靠后端 OpenAPI 生成接口(`web-ui/scripts/swaggerauth.ts` 的 `sources[]` = M 个后端各一份), operationId 全局唯一、稳定、网关无关、天然带服务身份。
3. **不碰三库隔离红线**: `graph/audit.py` 的 `cross_project` 判的是 **pid 维度不是 repo 维度** → 多仓同 pid 天然不触红线; 没人主张放宽。
4. **落点一致**: 在 `graph/ingest.py:_link_pass` 加一条**比 URL 桥优先级高的 operationId resolver, winner-by-confidence, URL 兜底保留**。

**分歧**: ROI 派主张"0 真实多仓 → 重型 A 先别开工"(登记的 4 项目全单仓); 其余三人在设计多根方案。**采纳 ROI 派红线**: 重型多根冻结, 先做 operationId 桥(它本就不需多仓基础设施)。

## 三、分期

### Phase 1 —— operationId 契约桥 + 契约漂移(**立即做**, 不分仓也受益)

落点全在 `_stack_scan` 插件 + `graph/ingest.py:_link_pass` + `graph/impact.py`/`audit.py`, **零碰三库隔离 / 无需多根索引**。

| 子任务 | 内容 | 落点文件 |
|---|---|---|
| **P1.1** 前端节点带契约锚 | `react.py`/`vue.py` 的 `frontend_api_call` 节点 `meta` 补 `operation_id` + `service`(从生成代码来源 namespace/outputSubDir 推 service, operationId 生成时已知) | `plugins/builtin/_stack_scan/react.py` `vue.py` |
| **P1.2** 后端端点带 operationId | `fastapi.py` 已有 `operation_id`(scan_fastapi); **`spring.py` 补 operationId 派生**(对齐 springdoc 默认规则: `<method><Handler>` 等) | `_stack_scan/spring.py`(主改) `fastapi.py`(核对) |
| **P1.3** operationId 高优先桥 | `_link_pass` 加 resolver: 按 `(service, operation_id)` 精确 join `api_call→endpoint` 建 `calls_api` conf=1.0; 命中即用; 未命中 fallback 现有 URL 桥(降 conf)。复用 `_calls_pass` 已有"多 resolver 产同边、conf 最高者赢" winner 范式 | `graph/ingest.py:_link_pass` + `_stack_scan/_link.py` |
| **P1.4** URL 桥多服务消歧加固(顺手修潜伏 bug) | `_link.py` bridge key `url` → `(service, normalized_url, method)`; candidates 跨多 service 时**降 conf + evidence `ambiguous_service`**; `audit.py` 加 warning `unbridged_api_calls`(api_call 没出 calls_api 的占比)+ `ambiguous_service_bridge` | `_stack_scan/_link.py` + `graph/audit.py` |
| **P1.5** 契约漂移查询 | 前端引用 operationId 集 ➖ 后端 OpenAPI 暴露集 = **悬空调用**(前端调了已删/改签名的接口)。新 graph 查询/工具 `find_contract_drift(project_id)` | `graph/impact.py` + `graph/mcp_server.py`(暴露工具) |

**Phase 1 验收(两服务 fixture, 不需真多仓)**:
- 造 fixture: 用户服务 + 订单服务**各有** `POST /api/list`(operationId 不同), 前端各按对应 base_url/operationId 调。
- 跑 `ingest` → `audit` clean(无 cross_project)→ `find_api_callers(用户服务端点)` **只**回调用户服务那个前端页, **不串**到订单页(证 P1.3 消歧)。
- 删掉后端一个 operationId → `find_contract_drift` 抓到前端悬空调用(证 P1.5)。
- 现有 graph/plugin 回归全绿; 真机两仓(若手头有)抽样核对。

### Phase 2 —— 多根索引 A 变体(**冻结**, Gate 触发才开工)

内容(届时按平台派 + 架构师方案): `meta.repos[]` 数组 + `core` 加 `iter_repos(meta)` 归一兼容垫(旧 `repo_path` 标量 → 单元素, 现有 4 项目零改)→ worker/`ops/codegraph._iter_projects`/proxy 全改走它 → 多根 ingest(node id 加 `repo_slug` 防碰撞)→ `codegraph_index_dir` 加 per-repo 子目录 → codegraph proxy 多后端 fan-out → Job 加 `repo` 维度。**去险**: `cross_project` 是 pid 维度故多仓同 pid 不破隔离红线(架构师证); operationId 桥 repo 无关, **两仓进一 pid 时 Phase 1 的桥自动跨仓, C→A 无返工**。

**🔒 Gate(开工条件, 采纳 ROI 派红线)**: **≥2 个真实"前后端分离多仓"项目存在, 且 ≥1 个被"跨仓 impact 查不到"实际卡住。** 未满足前只立项不开工。

## 四、纪律红线 + 冻结清单

1. **validate-first**: Phase 1 两服务 fixture 证伪/证实"桥连对 + 漂移抓得到"再扩。
2. **不碰三库隔离**: `cross_project` 红线一行不放宽; pid 仍是隔离边界。
3. **operationId 桥 repo 无关**: 当下修 monorepo 多服务 bug, 未来多仓零返工 —— 这是选它的根因。
4. **明确冻结(0 真实用例不建)**: 多根索引进一图 / codegraph fan-out / 后端 RPC·MQ 互调建图 / 跨 org 多仓 / 自动服务发现 / N+M 通用调度。撞 Gate 再解冻。
5. **no-OpenAPI / 非 REST 服务**: 桥连不上就**不假连**(守 `impact.py` 确定 vs 候选拆分), 退 URL 兜底 + 降 conf。

## 五、工作量

| Phase | 估时 | 备注 |
|---|---|---|
| P1.1-P1.3 operationId 桥 | 1.5-2 天 | spring operationId 派生是主要新代码 |
| P1.4 URL 消歧加固 + audit | 0.5-1 天 | 顺手修潜伏 bug |
| P1.5 契约漂移 | 0.5 天 | 集合差集 + 一个查询/工具 |
| Phase 2 多根 A 变体 | 5-8 天 | **冻结**, Gate 触发才估 |

Phase 1 合计 **2.5-3.5 天**, 不分仓也立刻有用。

## 六、验收

- Phase 1 全程 fixture + 真机验证, 现有回归不降; operationId 桥命中率 + URL fallback 占比 + 漂移检出数有数。
- 终态: 多服务/多仓下 `find_api_callers` 连对服务、`find_contract_drift` 抓悬空调用; 重型多根**按 Gate 冻结**, 不为假想需求建地基。

## 七、本 plan 不含

实现代码、spring operationId 派生的具体规则细节、fixture 用例内容、Phase 2 的 meta schema 定稿 —— 留 Phase 1 实现轮 / Gate 触发轮。
