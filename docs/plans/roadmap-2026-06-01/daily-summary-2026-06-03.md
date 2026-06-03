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
- **P2b sql 扩 Java MyBatis 注解 SQL**:`@Select/@Insert` 等→表读写,补 Java 后端读(BaseMapper 隐式 CRUD 留后续)。
- **P3 核心 linker pass**:ingest 末尾跨所有后端插件产 calls_api(单一 owner `builtin.linker`),修前端链不到 Java/Spring 缺口;openclaw 529 calls_api。
- **P4 cross_link 适配器插件退场 + 生产者归属防复发闸**:`plugins/ownership.py`(kind→owner 单一真值)+ `test_plugin_owner_uniqueness`。**两库重建确认 cross_link rows=0,每 kind 单一 owner**;openclaw 节点 ~6800→2053 干净,codev→361。
- 坑:删插件后旧行成 orphan,须 `rm graph_store/<pid>.sqlite*` 重建(已重建)。
- 测试基线 680 → **705 passed**。提交区间 `8649722 … cce2385`。

## 六、待办

1. **前端 `跨层链路` 页并入统一图谱**(P4 余留):该页现回落 legacy cross_layer 视图(非破);目标做成统一图谱的视图预设 + table-refs/endpoint-link 进节点详情面板。
2. MyBatis-Plus BaseMapper 隐式 CRUD 的表读(需 `@TableName` 实体解析);Java/.NET 语言基座 `_java_scan` 待抽。
3. 真实验证 webhook 自动链路(下次 push 看 `tail /tmp` 日志 + `reindex-queue status`)。
4. demo 项目(CRM Vue/React/Java/FastAPI)做 POC 演示数据(plan Phase 7)。
5. 统一图谱 db_column 过密(openclaw 1088)→ KindFilter 按层分组 / db_column 默认折叠。
