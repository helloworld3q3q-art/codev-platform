# 迭代日报 2026-06-03(承 06-02 晚)

> 本轮主题:**admin web-ui 接入打通 + 模块化核心/插件化扩展 plan Phase 0-3 落地 + WSL 全服务 systemd 化**。
> 提交区间 `cac309a … 6f65635`(dev),测试基线 591 → **680 passed**。

---

## 一、web-ui(前端)

- **顶栏用户菜单**:组织/项目选择器 + 登出收进 TabContainer 页签栏右侧单一下拉(`UserMenu`);`@jlogi/ui` 升 2.0.0-alpha.2.1,新增 `tabBarExtraContent` 插槽(jlogiui 仓)。
- **org/project 上下文收敛进 models**:`useModel('org'/'project')` 持有 current + useEffect 初始化默认(全局常驻,不依赖懒渲染下拉);选择器改 `@jlogi/ui` Select + `fetchOptions`(DemoSelect 模式);**级联默认第一个组织的第一个项目**;`getInitialState` 启动预置 `current_project` 保证首屏请求带 `X-Project-Id`。
- **切换响应式重拉**:dashboard / codegraph 5 子页 / projects 订阅 currentProjectId/currentOrgId,切换即原地重拉(非 reload)。
- **codegraph 模块移植**(workflow,8 agent):crosslink/filelist/files/graph 3D/table 5 子页移植自 stock-admin-web,接 codev 后端 `/api/v1/graph/codegraph|cross-link/*`;删冗余 `graph`/`enums` 旧页。
- **项目↔组织绑定**:注册项目选组织 + 列表加组织/名称过滤;修 `projects/list` 参数从 Query 改读 **POST body**(前端 ResizableTable 发 body,过滤之前永久落空)。

## 二、后端 / 运维(WSL)

- 后端实际跑在 WSL `/home/helloworld/work/codev-platform`;前端 proxy/api/enums 指向 WSL IP `172.31.216.170:18088`。
- **登录修复**:WSL config 缺 `web.bootstrap_admin`/`platform_admins` → 未 seed admin → 登录 403;补配置后 seed `root`(密码走 `web.bootstrap_password`)。
- **cross-link 跨租户污染根治**:openclaw 构建器 `REPO_ROOT` 硬编码 → 扫错仓写错库;reindex 钉 `PLATFORM_PROJECT_ID`+`CROSS_LINK_REPO_ROOT`+cwd 到目标项目,无 build_index.py 优雅跳过。
- **cross-link 缺索引**:概览(stats/tables/graph)返空,具体查询(table-refs/search/endpoint)仍 503 index_missing(对齐 test_web_graph 契约)。

## 三、模块化核心 + 插件化扩展 plan(Phase 0-3)

- **Phase 0 安全底座**:token 模式禁 cwd fallback 强制 project_id(`agent/tools/_project.py`);`/memory` org/team ACL 路由级集成测试;obslog 生产脱敏单测;webhook body 限 1MB→8MB(Gitea push payload 超 1MB 被 413 拒,故之前 webhook 不触发);部署文档骨架。
- **Phase 1 统一图谱**:`codev_platform/graph/schema.py`(GraphNode/Edge/Evidence/Finding/AnalyzerResult,dataclass + to/from_dict)+ cross-link 适配器(cross_layer.sqlite → 统一 AnalyzerResult)。
- **Phase 2 插件协议**:`plugins/`(AnalyzerPlugin + registry **自动发现** + executor 执行隔离 + CLI `plugins list`);**6 个栈适配器**(detect 基于 repo 内容,复用 `_stack_scan` JS/TS 基座):React / FastAPI / Vue / Node-Express / **SQL(方言 sqlite/mysql/pg + Python 源 内嵌 DDL/SQLAlchemy/Django)** / .NET;taxonomy 设计文档(语言基座 × 框架适配 × DB 方言三层);对抗审计(vue/node 直过,sql/dotnet 按审计修复后绿)。
- **Phase 3 图谱落库**:`graph/store.py`(per-project sqlite,幂等 upsert)+ `graph/ingest.py`(run_applicable → store)+ reindex 加 ingest stage + cross-link 路由优先读统一 store(空则 fallback cross_layer)。
- **codev-platform 自身 cross-link 数据**:Python-SQL scanner 扫出 23 表/136 列(表在 Python 运行时建,无 .sql 文件);前端→API→后端→table 链路就位。

## 四、WSL systemd 化(开机自起)

`serve-mcp install-systemd` + 自加 `codev-web.service`(18088 绑 0.0.0.0),`install.sh` 先释放手动端口再装。**8 个 unit 全 enabled + active**:MCP×3(chroma/cross-link/codegraph)、codev-reindex(worker)、codev-webhook、codev-agent、codev-web、clock-resync timer。`wsl --shutdown`/重启电脑后全自动拉起,崩溃 Restart=always。

## 五、统一图谱·全栈血缘收敛(晚,专题 [`unified-graph-lineage-2026-06-03/`](unified-graph-lineage-2026-06-03/))

发现"跨业务链路"支柱有**两套生产者重复灌库**(cross_link 适配器 + stack 插件各产同一批
endpoint/api/table)。收敛成单一实现,删 cross_link,全走统一 store:

- **java_endpoint→backend_endpoint 语言中性化**:cross-link 读边界翻译,纯 Python 端点不再误标 Java(`fix(graph)`)。
- **P1 Spring 端点插件**:扫 `@RestController`+`@*Mapping`→backend_endpoint(java);openclaw 真仓 143 端点(cross_link 132,近 parity)。
- **P2 sql 扩 Python DML**:AST 扫 raw SQL→backend_function + writes/reads_table(上游写库函数→表→读库函数);openclaw writes 172/reads 641。
- **P2b sql 扩 Java MyBatis 注解 SQL**:`@Select/@Insert` 等→表读写,补 Java 后端读。
- **P2c sql 解析 MyBatis-Plus BaseMapper**:`BaseMapper<Entity>`→实体 `@TableName`→表(隐式 CRUD,粗粒度 conf=0.6),闭合最后 Java 表读 gap;openclaw Java mapper reads 248(cross_link 旧 236)。
- **P3 核心 linker pass**:ingest 末尾跨所有后端插件产 calls_api(单一 owner `builtin.linker`),修前端链不到 Java/Spring 缺口。
- **P4 cross_link 适配器插件退场 + 生产者归属防复发闸**:`plugins/ownership.py`(kind→owner 单一真值)+ `test_plugin_owner_uniqueness`。
- **P4 收尾·跨层链路并入统一图谱**:KindFilter 改按层分组(前端/后端/数据库)+ 整层切换;节点详情面板加"关联节点"(用 store 边 reads/writes_table/calls_api/defines_column 还原表引用/端点关联);删 `/codegraph/crosslink` 路由+菜单+页。
- **死代码清理**:删 cross_link 适配器(`graph/adapters/cross_link.py`)+ `web/integrations/cross_link_client.py` + graph.py 的 6 个 `/cross-link` 路由及 helper + web/schemas cross-link DTO + 前端 services/types 残留。统一图谱(store)成跨业务链路唯一入口。(注:需跑 `pnpm run api` 重生成 typings)
- 坑:删插件后旧行成 orphan,须 `rm graph_store/<pid>.sqlite*` 重建(已对两库重建,cross_link rows=0)。openclaw 节点 ~6800→**2097 干净**,codev→361。
- **整体审计**:全量 pytest **688 passed** + 前端 tsc(src 0 错)+ 真实 store 完整性(dangling/orphan/归属违规/语义重复 全 0)+ 独立对抗 review;查出并修 2 个 Spring 边际 bug(`_spring_class_base` 用 `find("class ")` 脆弱 → 行首类型声明正则;方法级 `@RequestMapping` path==base 被误跳 → 改按注解位置区分)。
- 测试基线 680 → **688 passed**。提交区间 `8649722 … a8451be`。

## 六、待办

血缘专题已闭环(P1–P4 + 清理 + 审计全 ✅)。零碎收尾:
1. `pnpm run api` 重生成 typings(去掉已删的 cross-link 接口孤儿声明)。
2. Java/.NET 语言基座 `_java_scan` 抽象(Spring/MyBatis 扫描各自正则,可统一);MyBatis-Plus 按调用点细分读/写(现粗粒度 conf=0.6)。
3. 真实验证 webhook 自动链路(下次 push 看 `tail /tmp` 日志 + `reindex-queue status`)。
4. 统一图谱 db_column 过密(openclaw 1088)—— 已加按层筛选可关数据库层,后续可做 db_column 默认折叠。

## 七、整轮 roadmap 剩余盘点(2026-06-03 核实)

本轮(模块化核心+插件化扩展)粗略 **已做 ~25 项 / 未做 ~30 项**。血缘专题是唯一全完成子专题。剩余大块:

- **web-backend(Phase 0-6 骨架已搭,差收尾)**:Phase 7 agent/memory/reports/audit 路由 + integrations(cross_link/chroma/agent/reindex client)、Reports 模块、Phase 8 契约测试、退役 Java codegraph-api 切流、`web serve` CLI 子命令。
- **Agent Memory 平台化(M0 仅 doctor,M1-M7 基本全空)**:M1 任务记忆闭环(task_id)、M2 Context Engineering、M3 生命周期治理、M4 多租户审计闭环、M5 性能压测、M6 业务 connector、M7 多模态。
- **插件化平台(Phase 0-4 已做)**:**Phase 5 影响分析链路**(`find_impact/find_api_callers/find_table_usage/find_page_dependencies/generate_impact_report` + 报告)、Phase 6 Wiki/Jira/飞书 connector、Phase 7 Demo 项目、Phase 8 私有化 POC 包。
- **重构收尾**:`test_file_size_budget.py` 静态断言、agent FastAPI 路由补 `code` 字段、chroma `_state/_http` 叶子(非硬需求)。

**三大缺口**:① 影响分析报告链路(Phase 5 + Reports + Agent 工具)—— README 定义的核心商业价值,统一图谱数据已备齐正好做地基;② Agent Memory 平台化;③ Demo + POC 包。
**逻辑下一步** = Phase 5 影响分析(吃刚做完的统一图谱节点+跨层边,做"改一处影响哪些前端/后端/表"的 Agent 工具 + 报告)。

## 八、deep-audit-2026-06-03 安全/质量全面整改(后续会话)

> 对 [`docs/audits/deep-audit-2026-06-03.md`](../../audits/deep-audit-2026-06-03.md) **12 项发现全部闭环** + 源码级复核。测试基线 688 → **740 passed / 5 skipped**,前端 tsc **0 错误**。
> 复核文档 [`deep-audit-2026-06-03-review.md`](../../audits/deep-audit-2026-06-03-review.md)(逐条修正定级 + 6 个净新增发现)。

**审计的审计**:12 项现象复现全属实,但原审计"定级偏高 + 漏看链路运行时是否真的通"。净新增 6 发现:① projects 修复撞 session/gateway **双 token** 模型;② web `/indexes/rebuild` 是 `_noop_trigger` **空壳**;③ graph 幂等 `DELETE WHERE plugin` **project-blind**;④ webhook 链(后查实 **WSL 19099 一直正常**,Windows 18099 是影子日志,原"链死了"判断错);⑤ 413 已修(8MB);⑥ 平台自助重建路径仅剩手动 CLI。

**P1 安全(3/3 ✅)**:
- **projects 两级 RBAC**:org 隔离 + 逐项目 `role_allows`(复用 `core/rbac` 单一真值;新增 `web/security/membership.py` 统一 prod PG `project_access` + dev 内存 `OrgMember.project_roles`)。
- logout 撤 access(session_id 关联 access/refresh);register 原子 `open(x)`;PG fail-fast(prod 配 PG 失败 RuntimeError,dev/ImportError 回退);均补回归测试。
- **双轨收口**:`SessionAwareAuthenticator` 包 gateway 认证器 —— token 模式 web 走 session token、gateway token 留 MCP。

**P2(6/6 ✅)**:reindex runner timeout(超时 kill+rc=124 防串行队头阻塞)/ graph 节点级 project_id 隔离(写强制+读过滤+DELETE 带 project)/ **业务 PG 库迁 SQLAlchemy Core**(rbac+account store 裸 SQL→`select/insert`,`_SCHEMA` 收口 `web/db/tables.py`,**方言感知 upsert**,sqlite 内存引擎集成测试覆盖 fetch_membership,+ **Alembic baseline 7 表与 tables.py 0-diff**)/ 前端 TS 4.9→5.4(tsc **36→0**,删 8 个 stock-admin-web 死模板组件)/ 前端 403 会话失效清 token 跳登录 / PG fail-fast。

**P3(3/3 ✅)**:webhook/worker + 3 个 MCP daemon `mcp_server.log` 迁 `data_root/logs`(distinct 前缀)/ 移除 `@ts-nocheck`(fetch.ts 清 0 错 + 删死模板 DemoSelect;`.umi/` 生成物不算)/ 真静默 broad except 加可观测(6 处,上报型保留避噪)。

**对抗式审计(派兄弟)**:前端 0 issue(403 正则 `/会话|未登录/` vs 后端全部 9 条文案 → **0 误判/0 漏判**);后端查出 **1 HIGH**(`search_recall.jsonl` 写迁了但 3 个读取方未迁 → ops/health+metrics+platform_status 对齐 writer 修复)+ 1 LOW(docstring)。

**依赖**:Windows 测试 python + WSL venv 各装 `sqlalchemy 2.0.50` + `alembic`(进 `pyproject`/`requirements-runtime`)。

**提交区间** `b67d7f2 … 2943742`(13 commit;runner-timeout 在并发会话 `64b0dae`)。全推 Gitea + WSL `/home/helloworld/work/codev-platform` ff-pull 同步 + codegraph reindex,MCP 索引验证含 `resolve_membership`/`SessionAwareAuthenticator` 新符号。
