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

---

## 影响分析前端页 + 三份审计收尾(本会话续 2)

### 影响分析前端页(`fbb1b79`)— Track A 的消费面补齐
- `pnpm run api` 生成 `reportsapi.ts`(reportImpact/tableUsage/pageDependencies/apiCallers)后,建 `web-ui/src/pages/impact/` 单页接它,补齐 README 核心卖点"改一处→跨层影响清单"的**前端入口**。
- 结构:`index.tsx` 轻聚合(查询类型 Radio + Input.Search + 调 reportsapi 直取 `.data`)+ `utils.ts` 归一化 4 种响应壳 + `components/{LayerGroups,ResultPanel}.tsx`(按层分组 + 风险徽章 high/medium/low + 可读 summary + 同名歧义候选)。
- 规则遵循:不碰 `src/services`、`useState/useCallback/useEffect`(无 useRequest)、antd6(`Card.classNames.root` 加 `i:`)、UnoCSS(baseFontSize=4)、组件命名。路由 `/codegraph/impact` 归代码图谱组。tsc=0 / eslint=0。

### deep-audit-2026-06-03-review §五 收尾(`0439879`)— P0 web rebuild 空壳接线
- 逐项核到代码底层:§五 12 项中 **10 项早已落地**(双轨收口/projects 授权/logout 撤 access/register 原子写 `open "x"`/PG fallback fail-fast/graph store 三向 project_id/runner timeout/SQLAlchemy/TS 解 @ts-nocheck/日志迁 logs_dir + gitignore)。
- **本轮新做唯一真实代码项**:`index_service.make_reindex_dispatch_trigger` 把 `index_rebuild:<kind>` job 派进真实 `FileSpoolQueue`(与 webhook 同队列,worker 消费),修原 `_noop_trigger` 空壳(净新增②);'all' 展开 `runners.kinds()`;`jobs.py` 注入;`submit()` 派发失败释放锁防悬挂。5 测试。
- 剩 webhook.secret(运营决断)+ broad except(设计保留)。复核文档补 §六。

### codev-platform-audit-report 插件性能(`5efef01`)— CI 超时加固
- **P1** `_stack_scan.py` rglob 进大目录:核实**早已修**(全面改 `_walk_pruned` os.walk 剪枝),回归测试 `test_sql_detect_skips_build_dirs` 在册;实测全量 124s 超时→**25s** 已解。
- **P2** CI 无超时保护:**本轮修** —— `pytest-timeout` 进 dev extras + CI `--timeout=120 --timeout-method=thread`(跨平台一致)。
- P3 README/pyproject 乱码 = **误报**(文件无 BOM UTF-8 正常解码,PowerShell 显示问题);.venv 缺 pytest = 环境;lint = 更大改造另立。

**测试基线 → 791 passed / 5 skipped**(786→791,+5 dispatch trigger 测试),全程零回归。

### 两个待决断项的落地(本会话续 3)

用户"依次做吧",两项均闭环:

#### 1. webhook.secret — 实查发现**早已在跑**(无需配置)
- 经 WSL 实地核(非看陈旧文档):secret 已配(config 6/2 改)+ 3 项目 webhook_repo 映射齐 + `codev-webhook` 服务 active/enabled(19099 healthz 200)+ Gitea(同在 WSL)webhook id=2 active、投递全 succeed。
- **本会话多次 push 实测被自动 enqueue reindex**(碰索引文件→`enqueue ['chroma']`,只碰非索引→正确跳过)。review 文档"secret 未配/链瘫痪"**陈旧结论已更正**。
- 唯一小噪音:>8MB 超大 push 被 413(web-ui 重生成类大 commit,本无 reindex scope,无害)。
- 教训:复核文档(deep-audit-review)写于陈旧 webhook.log 时点,据它判"未配"是误判;**配置类结论必须实查 live 态**。

#### 2. CI 引入 ruff lint(Phase 1,`56f0fc3`/`db1f0bb`)
- 配置 `[tool.ruff]`:select F/E/W/UP/B;ignore E501(行长 1356)+ E402(本仓刻意晚 import);FastAPI `Depends` 豁免 B008;`chroma.server`/`mcp_serve` 两个 re-export hub `per-file-ignores` 保护(防 F401 删穿业务仓 shim)。
- **清零**:50 自动修 + 16 手动修 —— `orgs.py` 5 处 `Depends(require_org_role("admin"))` 提模块级单例(对齐 projects.py)、`gateway/auth` %-format→f-string、`dotnet` 取末匹配改 list、清 unused import/var/loop。`codev_platform` **0 findings**。
- CI 独立 `lint` job gate `codev_platform`,ruff **钉死 0.15.15** 保证可复现。
- **逐步收敛**:tests/ + tools/(~42 项)+ E501 行长留 Phase 2;mypy 另立。

**测试基线维持 791 passed / 5 skipped**,ruff 自动+手动改 + orgs 单例重构 + re-export 删减零回归。三份审计代码侧全部闭环。

---

## cross-link 彻底退役 → 统一图谱 store 单一真值源(本会话续 4）

把"找前端API↔端点↔表 跨层链路"这件事的旧数据底座（cross_layer.sqlite + build_index.py）整套退役，所有查询收敛到统一图谱 store。三个 MCP（platform-docs/codegraph/cross-link）格局不变，cross-link 只换了背后的数据来源，业务仓 `.mcp.json` 无感。

### 前置：parity 对账达标
- 旧库 cross_layer 退役的红线是「store 没覆盖全前不许退」。为达标：clone openclaw 业务仓到 WSL（`~/WorkSpace/platform`）→ 跑 `reindex --ingest` 把桥接边从 **0 补到 155** → 跑 `tools/audit_graph_parity.py`：端点 143≥132 / 表 122≥69 / 前端→端点 143≥132 / **端点→表可达 187≥185**，四项 store 全 ≥ cross_layer，**达标**，红线解除。
- 副产物：openclaw 影响分析从此真完整（端点→函数→表全通）。

### 两条战线
- **战线 B（`39b0889`）** codev-agent 工具去重：agent 工具集里 `cross_link`（读旧库）与 `impact`（读 store）重复，退役 `cross_link`，留 store 原生 `impact`（table_usage/api_callers/page_dependencies）。删 `agent/tools/cross_link.py` + 改 prompt/测试。830 passed。
- **战线 A（`64817e8`，审计 PASS 无 BLOCKER）** cross-link MCP 4 工具全收敛 store：`find_table_refs`/`find_endpoint_link`（A6 已 store-first）+ 新写 `search_nodes`/`cross_link_stats` 的 store 版 + `find_table_refs` 补 definers（从 db_table 源文件）+ dispatch 改纯 store（store 缺失→INDEX_MISSING，**删 cross_layer fallback**）+ 删旧库死码 + kind 词汇换 store。服务名/端口 18086/4 工具名/required 全不变 → 业务仓兼容。净删 337 行旧码。835 passed。

### A3 退役 cross-link 自动重建（`c3f9b83`，前置）
- parity 达标 + web 确认不读 cross_layer（只 codegraph.db + store）→ 停 cross_layer 自动重建：`auto_reindex_kinds` 滤掉退役 scope，post-commit/webhook 两个自动入队方共用；手动 `--cross-link` + runner 保留。health `cross_layer` lag>1d 由 WARN 降 INFO（落后是预期）。审计 PASS。

### 服务同步（替换后让运行态用上新码）
- systemd 系统单元（无 sudo）→ kill MainPID + Restart=always 自动重起新码。
- cross-link MCP（19086）7016→7846 store-only 代码 live；webhook（19099）A3 生效；web 后端（18088）latest；全部 healthz/openapi 验证通过。

**整条链闭环**：A1 桥接 → A2 parity 达标 → A6（2 工具 store-first）→ A3（停自动重建）→ 战线 B（agent 去重）→ 战线 A（MCP 4 工具纯 store）。cross_layer.sqlite + build_index.py 彻底退役，跨层链路单一真值源 = 统一图谱 store。测试基线 791 → **835 passed**，全程零回归。
