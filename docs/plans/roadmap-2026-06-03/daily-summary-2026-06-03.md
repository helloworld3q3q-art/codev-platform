# 迭代日报 2026-06-03(roadmap-2026-06-03 首日)

> 本迭代主题:**统一图谱 store 做成连通全链路 → 解锁影响分析报告核心商业价值**(见 [`next-plan-2026-06-03.md`](next-plan-2026-06-03.md))。
> 同日 roadmap-2026-06-01 迭代的工作(web-ui / 插件 / 血缘 / deep-audit 整改 / SQLAlchemy)见 [`../roadmap-2026-06-01/daily-summary-2026-06-03.md`](../roadmap-2026-06-01/daily-summary-2026-06-03.md)。
> 测试基线 759 → **766 passed**。提交区间本迭代 `48cb0e1 … 3068af5`。

---

## Track A 影响分析 spearhead — 核心完整交付 ✅

README 核心卖点 **"改一处 → 跨层影响清单"** 打通,数据地基(统一图谱节点 + A1 桥接的跨层边)+ 引擎 + 两个消费面(web API + agent 工具)全落地。

### A1 codegraph 桥接(`48cb0e1`)— 让 store 自成连通
- 问题:端点本身不在 codegraph(路由装饰器非符号),store 里 `backend_endpoint` 出边 = 0,前端链到端点就断。
- 做法:新增 `graph/bridge_codegraph.py` —— 加载 codegraph `calls` 调用图,从每个端点 handler **深度受限 BFS**(_MAX_DEPTH=6/_VISIT=400),把可达且在 store 里碰表的 `backend_function` 物化成 `backend_endpoint --calls--> backend_function` 边;接进 `ingest.py` 末段;fail-soft(codegraph 缺失空跑)。
- join key = **(归一化 file, name)**(codegraph callee 的 startLine 是定义行、store function 的 line 是 SQL 行,不可比)。
- **真实数据**:endpoint 出边 0→>0,**codev 3 边 / openclaw 155 边**(`/login→selectRoleCodesByUserId`、`/backtest/attribution→selectAttribution`)。store 连通:前端→端点→函数→表。

### A4 影响引擎(`a5aa587`)+ agent 工具(`3068af5`)
- `graph/impact.py`:`ImpactGraph`(正/反向邻接)+ 方向感知 BFS。**"改 X 影响谁"= 反向**(谁依赖)、**"X 依赖什么"= 正向**,按层(frontend/backend/database)分组。
- 4 入口:`find_impact` / `find_table_usage` / `find_page_dependencies` / `find_api_callers` + `generate_impact_report`(风险等级 high/medium/low + 可读 summary)。
- **agent 工具** `agent/tools/impact.py`:`impact_analysis`/`table_usage`/`page_dependencies`/`api_callers`,注册进 `build_default_registry`,prompt 工具选型优先统一图谱(替代旧 cross_link 工具,更全含 endpoint→函数桥接)。
- 真实:openclaw 122 表 / 143 端点 / 2451 边,**56/122 表反向可达前端**。

### A5 报告 + web API(`0c5be0a`)
- `web/routes/reports.py` 4 路由:`/api/v1/reports/impact` + table-usage / page-dependencies / api-callers,经 `require_project_access` 鉴权,只读 store,缺失 graceful-empty。注册进 web app。
- **已部署上线**:重启 codev-web(18088),`reportImpact` 在 live openapi。

### 对抗式审计 + 修复(`c623455`)
- 派审计兄弟:无 BLOCKER;真实数据证桥接方向正确、join 0 碰撞、反向语义正确。
- 查出并修:**HIGH** 重名歧义(11 同名端点 → `find_by_name` 多命中回 `ambiguous` 候选用 id 消歧)、**HIGH** 桥接同名跨文件盲取首个(419 跨文件同名 → 改唯一才挂、歧义放弃,宁缺毋滥)、**LOW** store sqlite 故障 → try/except graceful 不 500。补歧义回归测试。

### 验证
- **766 passed / 5 skipped**,Track A 专项 33 测试全绿(bridge 7 / impact 6 / web-reports 6 / agent-tools 7 + 7)。
- codev store 桥接边已物化(ingest 6 插件含 `builtin.codegraph_bridge`)。

---

## 运维踩坑(记下)
- **systemd 服务用 WSL checkout 而非 /mnt/d**:`python -m uvicorn`(WorkingDirectory=WSL repo 根)→ `-m` 把 cwd 放 sys.path 首位 → `import codev_platform` 解析到 `/home/helloworld/work`,不是 venv editable 的 /mnt/d。**每次 push 后要 `git -C /home/helloworld/work pull` + 重启服务**才更新;我的验证脚本走 /mnt/d 所以一直最新(易误判服务已更新)。

---

## 剩余(Track A 退役 cross-link,纯清理,价值低)
- **A2** parity 工具(store 连通数 vs cross_layer,证 store ≥ 再退役)
- **A3** 停 `reindex --cross-link` / `build_index.py`
- **A6** cross-link MCP(`find_endpoint_link`/`find_table_refs`)改读 store
- agent 工具在 **codev-agent 服务**生效需 pull WSL checkout + 重启该服务(web 已重启)

**spearhead 已完整交付 + 审计 + 上线;关键路径 A1→A4→A5 全绿。** 下一步可退役 cross-link(A2-A6)或转 Track B(web-backend 收尾)。

---

## roadmap-2026-06-01 收尾批(本会话续)

应"先把 06-01 都结束"的要求,核实后发现**大部分已完成**(refactor 三大文件早拆达标、B 错误码主面、pluginized Phase 5 影响分析=本会话 Track A)。把剩余**有界项**逐个清掉:

| 项 | commit | 内容 |
|---|---|---|
| **D2** file-size 预算断言 | `7f130dd` | `test_file_size_budget.py` 断言 `codev_platform/**/*.py` ≤600 行 + 防僵化白名单(当前 5 个已超:cli/sql/_stack_scan/cross_link.server/ops.reindex)。原 3 大文件 chroma 486 / mcp_serve 407 / health 目录化均达标 |
| **D3** agent 路由错误码 | `3c7542a` | chat/memory 路由 `str(e)` → 机器可读 code(UPSTREAM/INVALID/ACCESS/DEPENDENCY),str(e) 只进日志;playground 兼容读 error/detail。additive 不破 detail 依赖。9 测试 |
| **A2** parity 工具 | `be8b5bf` | `tools/audit_graph_parity.py` 核 store vs cross_layer(endpoint/table/前端链接/端点→表可达)。openclaw 实测 3/4 已超,**端点→表可达 store 0 vs cross 185 = GAP**(openclaw store 未 re-ingest 无桥接边;工具正确检测)。6 测试 |
| **Phase 8** 契约测试 | `a48dff9` | `test_web_contract.py`:全 web app operationId 无重复(防 `pnpm run api` 生成错乱)+ OpenAPI 可生成 + 关键路由在册 + 每操作有显式 operationId |
| **web serve CLI**(B5)| `a48dff9` | `codev-platform web serve [--host --port]` 起 uvicorn(与 codev-web.service 同 app)|

**测试基线 → 786 passed / 5 skipped**,全程零回归。

### 06-01 真实剩余(两块)
1. **agent-memory M1-M7** —— 大特性(plan 自定义 6 周);M1 任务记忆要动 `memory_entries` schema(加 task_id/kind)+ store/recall/chat/routes 全链。值得开专门一轮做。
2. **A6/A3 cross-link 退役** —— 低价值清理(新影响分析面已存在),且 blocked on openclaw re-ingest 达 parity。
3. connectors / Demo / POC —— 用户明确靠后。

### 待办坑(记下)
- **openclaw store 需 reindex --ingest** 补桥接边(A2 检出端点→表可达=0;补后 parity 达标可退役 cross-link)。
- **WSL+PG 测试隔离**:D3 兄弟报 WSL 下 `test_web_projects`/`test_web_filtering` 8 个 fail(Windows 全绿)—— 疑 PG backend 激活时账户 store 全局态跨测试泄漏,待查(env-specific,非代码逻辑 bug)。
