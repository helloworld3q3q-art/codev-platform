# 迭代日报 2026-06-04(roadmap-2026-06-03 续)

> 本迭代主题延续:**统一图谱连通全链路 → 影响分析**。本日两大交付都是"补 codegraph 结构盲区":
> ① 调用边 resolver 框架(endpoint→碰表函数, Python DI 盲区)② 前端组件依赖图(改组件→影响哪些页面, JSX/SFC 盲区)。
> 测试基线 **897 → 908 passed**。提交区间 `907de4b … 195df1a`。

---

## Track 1:调用边 resolver 框架 — 按「只补盲区」收敛 ✅

把原 `ingest._bridge_pass`(单一 codegraph 桥接)升格为**按语言栈可扩展**的 `graph/call_resolvers/` 框架,核心是一条用数据校准的原则。

- **Phase 1 框架**(`907de4b`):`CallResolver` 协议(applies/resolve)+ registry,零 if-else(同 plugins/agent-provider 铁律)。codegraph 桥接升格为第一个 resolver(跨语言兜底)。
- **去重 confidence 优先**(`9058abf`):同 (source,target,kind) 边保留置信最高者,注册顺序仅作并列 tiebreak。精确 resolver 盖过兜底,而非"先跑者赢"误丢高置信边。
- **FastAPI/Python DI resolver(范本)**(`a73bad9`):codegraph 追不动 `self._store.x()`(要先解析注入类型),用**方法名 BFS**(AST 提被调方法名,不解析类型)绕过 DI。降噪三件套:通用名黑名单 + `_MAX_DEPTH=3` + `conf=0.65`(< codegraph 0.7)。**codev 自身 63 边补上,codegraph 兜底 0 边**;chat→agent_sessions 表穿透 3 跳 DI 真阳性。
- **抽 `_bfs.py` 公共层**(`4a58d63`):方法名 BFS + 连边 + 去重抽出复用,加语言零成本。
- **Spring/Java 撤回 + 盲区原则**(`7138a72`):**数据证伪**了"Spring 价值最高"的判断 —— platform 仓 Java 方法名 BFS 387 边与 codegraph 90 边**零重叠** + 串台严重(assignMenus→backtest mapper),增量为负,撤回。**判据钉进 base.py docstring**:"codegraph 即便索引完整也追不到这条边吗?" 答否 → 交 codegraph(强类型它更准),唯结构盲区(Python DI)才做 resolver。当前 registered 仅 `codegraph` + `fastapi`。

> **元教训**:易解析 = codegraph 已解析,resolver 再做就是重复 + 加噪。Node/.NET/前端组件 calls 同理(codegraph 覆盖或无业务仓)→ 不做。

---

## Track 2:前端组件依赖图 — 接 dependency-cruiser 补盲区 ✅

需求「改这个公共组件影响哪些页面」是 codegraph **真盲区**(import 停模块级、JSX/Vue-SFC 不建引用边,实测公共组件 incoming 仅 contains)。

- **三条路验证**:codegraph 硬限制(改第三方解析器不可行)/ 手写正则劣解(前端模块解析 alias/barrel/index 坑太多)/ **dependency-cruiser**(成熟工具,读 tsconfig paths 解 `@/` alias)→ 选第三条,不造轮子。
- **数据端**(`f941368`):`scan_frontend_deps` 接 dependency-cruiser → `FRONTEND_MODULE` 节点(独立 kind, 不碰 vue 的 frontend_component)+ `IMPORTS` 边,独立 ingest post-pass,框架无关(**react .tsx + vue .vue 都吃**),fail-soft。🔑 关键配置:`--config` 文件的 `tsConfig` 才启用 tsconfig-paths 解 alias(实测依赖边 7→524)。
- **查询端**(`117d101`):`impact.find_impacted_pages`(反向 BFS imports,含传递 组件→barrel→页面,只取 is_page)+ cross_link MCP 工具 **`find_component_pages`**。agent 现在经 MCP 能查组件影响面。
- **真实双仓验证**:react(stock-admin-web)`PermissionButton`→**12 页面**;vue(scl-www-10, vue3+vite)1687 .vue,`AiMenuComponent`→1277 页面。完整 ingest e2e + 真实 MCP call_tool 层全验证。

### 端到端验证抓到 3 个真 bug(单元测试 mock 覆盖不到)
1. **pin @16 回归**(`c4c3865` 修):完整 ingest 时边 524→254、反向链路全断 —— dependency-cruiser 16.10.4 解析不全,17.4.3 完整,改 pin **@17**。
2. **owner 契约假绿**(审计抓):owner 测试 fixture 无 tsconfig → frontend_deps no-op 掩盖 kind 冲突 → 独立 `FRONTEND_MODULE` + mock 测试真覆盖。
3. **节点 name 带扩展**(`117d101` 修):MCP 端查 `PermissionButton` 不命中(name 是 `.tsx`)→ 去扩展 + index 用父目录名。

> **元教训**:补盲区**先评估成熟工具,别急着手写**;关键卖点配端到端验证(这次"都验证过了么"一问抓出 3 个潜伏 bug)。

---

## 收尾:Java codegraph-api :18082 退役确认(`195df1a`)

completion-audit 列"5 处引用 18082 = 切流未完成"系**误判**:5 处全是注释/docstring 历史说明,运行时 **0 依赖**(无 HTTP 调用、`_query/_check_codegraph_api` helper 已移除)。codegraph 自身走 SSE :18091(mcp-proxy),与 :18082 无关。**退役实际早已完成**,audit 状态 ⚠️→✅ 已纠正。

---

## 验证
- **908 passed / 5 skipped**;新增 resolver/frontend_deps/impact 专项测试(call_resolvers 框架 + fastapi DI + frontend_deps react/vue + find_impacted_pages + owner 契约覆盖)。
- 真实验证:codev(fastapi 63 边)/ platform(spring 撤回数据 + react 12 页面)/ scl-www-10(vue 1277 页面)/ MCP call_tool 层(find_component_pages → 12 页面)。
- memory 沉淀:`call-resolver-blind-spot-principle`(更新)+ `frontend-component-dep-graph`(新增)。

## Track 2b:前端依赖图性能优化(`7de34c3`)✅

Track 2 交付后的优化收尾(用户挑做),实测又**推翻两个想当然**:
- **fingerprint 缓存**:src 文件 mtime/size 没变 → 跳过整个 depcruise spawn,**platform 47s→0.11s(442x)**。dependency-cruiser 内置 `--cache` 实测**不省 spawn**(第二次反更慢)→ 改自管 `data/frontend_deps_cache/<pid>.json`。
- **npx 预装优先**:`_depcruise_base` 优先前端 `node_modules/.bin/depcruise`(快+离线),fallback `npx --yes`。生产应预装避免每次联网。
- **is_page 扩展**:加 Next.js app router(`app/.../page.*`)。⚠️ Next 分支**无真实项目验证**(手头仅 umi/vue 仓)。
- **连带修真 bug**:`_frontend_roots` 用 `rglob` 会遍历进 node_modules,monorepo 每次 scan ~22s → os.walk 原地剪枝秒级。**这才是 scan 慢的真因** —— fingerprint 缓存命中(0.06s)后才暴露瓶颈在 _frontend_roots、不在 depcruise。

## Track 3:codegraph「节点图谱」稀疏 bug(`6ba71b6`)✅

用户发现 `/codegraph/graph` 页面同一项目(codev-platform)边数忽多忽少(2289 vs 274)、图谱一盘散沙。
- **根因**:节点取 top 2000 按 **kind** 排序(`file>class>...>method>function`),file+class 就塞满
  名额、把 method/function 全挤出;而 `calls` 边都在 method 之间 + "边两端须都在节点集"过滤 →
  method 间边**全被砍光**。忽多忽少 = codegraph.db 节点数随 reindex 变动,file 一旦跨过 2000
  阈值就把 method 挤光、边骤降。**该按 degree 排序却按了 kind。**
- **修复**(`codegraph_client.py`):节点改按 **degree(连接数)** 取 top,调用主体进图。openclaw 实测
  可见边 **669→4169(6.2x)**,method/function 从 **0→626** 进图。不需 reindex(只改查询采样),web 重启即生效。

## 运维复盘:一个 `systemctl restart` 绕成大弯

把 codegraph fix 推上 WSL 平台,**实际只需 `sudo systemctl restart codev-web`** —— 代码早 push
到 gitea、WSL 早 pull(`6ba71b6` 是 WSL HEAD 祖先),web 进程没重启加载新代码而已。但我连错三步:
① 先以为要 reindex `frontend_module`(那是**另一套**统一图谱,跟 /codegraph/graph 无关)②又以为
WSL 没我代码,折腾 remote / pull / cherry-pick(还撞上 WSL 无 remote、Win/WSL 分离仓的假象)。
**教训**:运维问题先查清"代码在哪 / 服务跑哪版 / 数据在哪套图谱"再动手,别顺第一直觉一路改。

## 剩余 backlog(非阻断)
- **is_page**:Next app router 逻辑实现但未真实验证;umi `config/routes.ts` 显式注册路由仍盲区。
- **vue 业务仓 scl-www-10** 未 `codev-platform init` 登记进平台(运维,需确认接入意愿)。
- **endpoint→表 codev 自身 DI**:codegraph calls 图层面 service→store 边仍 0(audit 定 P3+;fastapi resolver 另一条路已绕过)。

## 元教训补充(本日 3 次同款)
"实测推翻想当然"本日出现 3 次:① pin @16 假设是最新(实际 17)② dependency-cruiser `--cache` 假设能省 spawn(实际不省)③ scan 慢假设在缓存(实际在 _frontend_roots 遍历 node_modules)。**性能/版本优化必配真实计时验证,别信直觉**。
