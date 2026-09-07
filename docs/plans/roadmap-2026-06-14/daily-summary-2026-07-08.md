# daily-summary 2026-07-08 —— OMS Java 后端解析收口

> 承 P3 多仓多根索引真实项目验收。今天聚焦 OMS Java 后端统一图谱解析:先用真实 dsm 仓确认 `backend_spring=0` 与 `call_resolvers=0` 的根因,再补 Spring 接口映射和 dao-service/JPA 查询血缘能力,不新增项目特化插件、不改 ingest 核心。

## 一、问题定位

- `scl-sod-imp-dsm` 的 HTTP 契约写在 `scl-sod-imp-dsm-api` 接口方法上,例如 `ParamMappingConfigMicroservice` 的 `@PostMapping("param-mapping/...")`。
- 实现类在 `scl-sod-imp-dsm-server` 中标注 `@RestController`,通过 `implements ParamMappingConfigMicroservice` 承载实际调用链。
- 原 `builtin.backend_spring` 只扫描带 `@RestController/@Controller` 的文件内 mapping 注解,所以这种“接口声明 URL、实现类声明 Controller”的结构会被跳过。

## 二、设计决策

- 不新增 `backend_spring_dsm` 一类项目特化插件;继续增强 `builtin.backend_spring`,保持插件名和 ingest 接口稳定。
- 不把所有 Feign 接口都提升为后端 endpoint;只有同仓存在 `@RestController/@Controller` 实现类并 `implements` 该接口时,才把接口 mapping 视作服务端 endpoint。
- endpoint 的 `file` 保留接口文件,作为 URL 契约证据;同时在 `meta.controller_file` 写入实现类文件,供 codegraph bridge 从真实 handler 实现方法起跳。
- 当前只用轻量两阶段 scanner:先收集 Java source / interface mappings / controller info,再由 `scan_spring` 编排。暂不上重型策略模式;等 WebFlux、元注解、多级接口等来源增加后,再把来源抽成正式 `EndpointSource`。

## 三、已落地

- `plugins/builtin/_stack_scan/spring.py`:
  - 抽出 `_JavaSource`、`_SpringEndpoint`、`_ControllerInfo` 三个内部结构。
  - 复用原 mapping 解析逻辑生成 `_SpringEndpoint`,避免新旧路径各写一套规则。
  - direct controller mapping 仍先入图;接口 mapping 作为补充,并用 endpoint id 去重,不覆盖旧能力。
- `graph/bridge_codegraph.py`:
  - 新增 `_endpoint_handler_file()`,老 endpoint 仍用 `ep.file`,接口 mapping endpoint 优先用 `meta.controller_file` 找 codegraph handler。
  - 保持原有歧义策略:指定文件未命中且同名 handler 多个时不挂边,宁缺毋滥。
- 测试补齐:
  - 纯 Feign 接口无 Controller 实现时不产 endpoint。
  - Controller implements 接口时产 3 个 Spring endpoint。
  - 接口文件和实现类都有同名方法时,bridge 从实现类调用链连接 backend function。

## 四、验证

```powershell
python -m pytest tests/test_plugin_backend_spring.py tests/test_graph_bridge_codegraph.py tests/test_ingest_linker.py tests/test_plugins_autodiscovery.py tests/test_plugin_capabilities.py tests/test_plugin_owner_uniqueness.py tests/test_plugins_stack.py
python -m codev_platform.cli reindex --repo D:\sampleProjectGamma\scl-sod-imp-dsm --ingest
python -m codev_platform.cli graph stats --project sample-project-gamma
python -m codev_platform.cli graph audit --project sample-project-gamma --json
git diff --check
```

结果:

- 目标测试 `46 passed`。
- DSM 真实仓 graph ingest 正常完成。
- `builtin.backend_spring node_count` 从 0 变为 3。
- 3 个 endpoint 均来自 `ParamMappingConfigMicroservice` 接口 mapping,并带 `controller_file=...ParamMappingConfigMicroserviceImpl.java`。
- `graph audit --project sample-project-gamma` 为 clean。
- `git diff --check` 无 whitespace error,仅 Git 提示部分文件下次 touch 会 CRLF 转 LF。

## 五、dao-service 查询血缘补齐

- 继续追 `call_resolvers=0` 时确认:CodeGraph 能从 3 个 endpoint handler 走到 `ParamMappingConfigQueryService`,但 graph store 中没有 dsm 的 SQL 访问 `backend_function`。
- 根因在 `builtin.sql`:既有覆盖 `.sql`、Python SQLAlchemy/Django/Core、Java MyBatis 注解/XML、MyBatis-Plus、HBM/HQL,但没有 Java JPA `@Entity/@Table/@Column` 和 dao-service `QEntity.root.select()/selectCount()` 查询 DSL。
- 设计上不新增顶层插件;在 `builtin.sql` 下新增 `java_orm.py` 扫描域:
  - 定义相抽取 JPA entity -> `db_table/db_column`,并记录 `entity_class`。
  - 访问相只把已知实体表映射下的 `QEntity.root.select()/selectCount()` 解析为 `backend_function reads_table`,不靠命名猜表。
  - `.sql` 先定义同名表时,JPA 节点会被去重,但实体到表映射仍保留给 QueryModel 访问相使用。
  - 注解参数改为括号平衡 + 顶层参数读取,避免 `@Table(indexes=@Index(name=...))` 或 `@Column(columnDefinition="decimal(19,2)", name=...)` 误取嵌套 `name`。
- 兄弟审计发现两处问题并已修:
  - `.sql + JPA + QueryModel` 混合仓下实体映射丢失。
  - JPA 注解非平衡正则导致表名/列名污染。

## 六、补充验证

```powershell
python -m pytest tests/test_sql_java_orm.py tests/test_plugin_sql_dml.py tests/test_plugins_stack_sql.py tests/test_sql_hbm.py tests/test_sql_sqlalchemy_core.py tests/test_graph_bridge_codegraph.py tests/test_plugin_backend_spring.py tests/test_ingest_linker.py
python -m ruff check codev_platform/plugins/builtin/sql/java_orm.py tests/test_sql_java_orm.py codev_platform/plugins/builtin/sql/__init__.py
python -m codev_platform.cli reindex --repo D:\sampleProjectGamma\scl-sod-imp-dsm --ingest
python -m codev_platform.cli graph stats --project sample-project-gamma
python -m codev_platform.cli graph audit --project sample-project-gamma --json
git diff --check
```

结果:

- 目标回归 `80 passed`,ruff 通过,`git diff --check` 无 whitespace error。
- DSM `SqlPlugin` 真实扫描产出 `ParamMappingConfigQueryService.queryConfigs/queryAllEnabled/queryMappingValue -> unified_param_mapping_config` 三条 `reads_table` 边。
- DSM graph ingest 正常完成;`builtin.sql node_count=1358 edge_count=1300`。
- `builtin.call_resolvers edge_count` 从 0 变为 3。
- 三个 `/param-mapping/*` endpoint 均连到 `ParamMappingConfigQueryService` 对应 service 方法,`graph audit --project sample-project-gamma` 为 clean。

## 七、剩余边界

- dao-service 当前只覆盖 `select()/selectCount()` 读路径;若后续遇到 update/delete/save 等 DSL,应在 `java_orm.py` 内按操作族补子扫描策略,不塞到 `SqlPlugin.analyze` 主流程。
- 当前 scanner 仍是正则级轻量解析,适合平台低成本索引;如果后续遇到复杂 Java AST 场景,再评估引入 tree-sitter/JDT 作为可插拔 parser adapter。

## 八、前端多仓桥接基础修复

- 继续分析 OMS 前端图谱时确认:`frontend_module` 来自 `builtin.frontend_deps` post-pass,未经过 `RepoScope.localize`;而 `frontend_api_call/frontend_route` 已在插件 merge 阶段带 `scl-www::` 仓 tag,导致同文件匹配失效,`builtin.frontend_bridge` 为 0。
- 设计上不改 `build_frontend_bridge_edges` 和 linker 核心,只把 `frontend_deps` post-pass 产物包成 `AnalyzerResult` 后复用既有 `RepoScope.localize`;单仓和主仓仍 no-op,extra 仓 module/imports 边与 api_call/route 使用同一文件命名空间。
- 补多仓回归测试:主仓 + extra 仓同相对路径文件同时产 `frontend_module` 和 `frontend_api_call`,断言两个仓都生成 `contains` 桥接边;同时断言 extra 仓 `imports` 边两端会同步改写为仓 tag。
- 兄弟审计未发现阻断问题;按建议补了 `imports` edge 端点改写集成断言。
- 验证:

```powershell
python -m pytest tests/test_graph_ingest.py tests/test_repo_scope.py tests/test_frontend_bridge.py
```

结果:`27 passed`。

后续步骤:在此基础上补通用 JS/TS `request({ url, method })` 请求对象扫描;坚持解析静态 URL 片段,不写 OMS 专用 `ContextEnum` 或 `daoServiceClientConfig` 分支。

## 九、通用 JS/TS request-object API 扫描

- 真实 `scl-www` 前端 API 大量使用导出函数包 `request({ url, method })`、`request<T>({ ... })`、模板前缀和字符串拼接;原 React/Vue 扫描只覆盖 `export async function` 的窄窗口和裸 `request('/x')`,导致 `frontend_api_call` 只有个位/十位级。
- 设计上新增 `_stack_scan/js_request.py` 作为 JS/TS 请求对象扫描基础单元,由 React/Vue 复用;不新增 OMS 专用插件,不硬编码 `ContextEnum`、`daoServiceClientConfig` 或具体仓名。
- URL 解析策略:
  - 只提取静态路径片段,如 `${prefix}/dbLink/queryAllEnabled`、`ContextEnum.xxx + 'baseCcs/getBaseCcs?'`。
  - 路径中出现动态段,如 `/base/${id}/detail` 或 `/base/${operate}`,不产硬 `frontend_api_call`,避免错连。
  - 只认明确 HTTP wrapper/callee,过滤 `router.push({ url })` 等非 HTTP 对象调用。
  - 支持 TypeScript 泛型调用 `request<Page<Row>>({})` 和 `request<{ data: Row }>({})`。
- React 旧能力保留:内联 `axios/fetch/request('/api')` 扫描仍走原入口;React/Vue 只增加共享扫描器调用,不改变 linker 核心。
- 真实 `D:\sampleProjectGamma\scl-www` 只读扫描验证:
  - `scan_vue` 产 `frontend_api_call=2374`,方法分布 `POST=1685, GET=613, PUT=60, DELETE=16`。
  - 覆盖样例:`ImpDbLinkApi.queryAllEnabled -> /dbLink/queryAllEnabled GET`,`BaseBin.saveBaseBinApi -> /baseBin/batchSaveData POST`,`BaseCcs.baseCcsQueryApi -> /ds/commonSearchHelp/query POST`。
  - 临时 ingest 小样本验证 `queryAllEnabled` 可通过核心 linker 连到 Spring `GET /dbLink/queryAllEnabled`。
- 兄弟审计发现并已修:
  - 任意 `xxx({ url })` 误识别为 HTTP 调用。
  - `/x/${id}/detail` 被误解析成 `/detail`。
  - `export const helper` 扫描窗口吸收后续 `export function`。
- 复审结论:阻断问题已闭环。剩余边界:`xxxRequest({ url })` 仍按 HTTP wrapper 处理,后续若遇到非 HTTP 同名模式,再考虑 wrapper 白名单配置或低置信标记,本步不提前复杂化。

验证:

```powershell
python -m pytest tests/test_js_request_scan.py tests/test_plugins_stack_vue.py tests/test_plugins_stack.py tests/test_ingest_linker.py
python -m ruff check codev_platform/plugins/builtin/_stack_scan/js_request.py codev_platform/plugins/builtin/_stack_scan/react.py codev_platform/plugins/builtin/_stack_scan/vue.py tests/test_js_request_scan.py tests/test_plugins_stack_vue.py
```

结果:`32 passed`,ruff 通过。

## 十、真实 OMS 前端 ingest 验收与插件生命周期修正

- 跑真实 `sample-project-gamma` ingest 后发现:`scl-www` 是 Vue/Vite 项目,但含 3 个 `.tsx` 渲染器组件;原 `react_detect` 只要看到 `.tsx` 就命中,导致 `builtin.frontend_react` 和 `builtin.vue` 同时扫描同一批 TS API,出现 2372 个重复 `frontend_api_call` warning。
- 通用修正:
  - `react_detect` 优先按 `package.json` 的 `react` 依赖判断;没有 React 依赖但声明 Vue 依赖时,不再用 `.tsx` fallback 误判 React。
  - 补回归测试:Vue 项目含 TSX renderer 但无 React 依赖时,`FrontendReactPlugin.detect()` 为 false。
- 继续验证时发现旧 `builtin.frontend_react` 产物仍留在 store:ingest 只 upsert 本轮成功插件,不会清理“上轮适用、本轮不适用”的插件旧数据。
- 通用生命周期修正:
  - ingest 改为收集所有插件执行结果;仅当某插件在所有仓均为 `NOT_APPLICABLE` 且 store 中有旧产物时,写空 `AnalyzerResult` 清旧结果。
  - 插件失败不清旧图谱;多仓部分失败且该 plugin 已有旧结果时,跳过该 plugin 本轮整体 upsert,保留旧图谱,避免按 plugin 全量替换时抹掉失败仓上轮数据。
  - 补测试覆盖:全仓不适用清旧、全仓 crash 保留旧、多仓部分成功部分失败时保留旧并记录 `partial_failure_kept_stale`。
- 真实 OMS 复验:
  - `python -m codev_platform.cli reindex --repo D:\sampleProjectGamma\scl-sod-gateway --ingest` 成功,约 8s。
  - `builtin.frontend_react node_count=0`,`builtin.vue node_count=3705`,`builtin.frontend_bridge edge_count=2405`。
  - store 探针:`frontend_api_call=2374, unique_api_ids=2374, duplicate=0, calls_api=0`。
  - `calls_api=0` 原因确认:当前后端 endpoint 只有静态 demo `GET /` 和 DSM `/param-mapping/*` 三个接口,`scl-www` 源码无这些 `/param-mapping/*` 调用;不是桥接失败。
  - `graph audit --project sample-project-gamma --json` 为 clean,duplicate_nodes/duplicate_edges/no_provenance 均为 0。

验证:

```powershell
python -m pytest tests/test_graph_ingest.py tests/test_repo_scope.py tests/test_frontend_bridge.py tests/test_plugins_stack.py tests/test_js_request_scan.py tests/test_plugins_stack_vue.py tests/test_ingest_linker.py
python -m ruff check codev_platform/graph/ingest.py codev_platform/plugins/builtin/_stack_scan/react.py tests/test_plugins_stack.py tests/test_js_request_scan.py tests/test_plugins_stack_vue.py
python -m codev_platform.cli reindex --repo D:\sampleProjectGamma\scl-sod-gateway --ingest
python -m codev_platform.cli graph audit --project sample-project-gamma --json
```

结果:`63 passed`,ruff 通过,真实 OMS audit clean。

## 十一、前后端 API 链路覆盖诊断

- 继续核对真实 `D:\sampleProjectGamma` 后端仓后确认:当前纳入的 Java 后端源码只产 3 个 DSM `/param-mapping/*` endpoint,另有 1 个前端静态 demo `GET /`;`scl-www` 的 2374 个前端 API 大多没有对应 Controller 源码在当前 OMS 工作区内。
- 结论:此时继续硬补 Spring annotation scanner 会变成项目特化/猜测式开发;真正需要先让平台可解释“为什么 `calls_api=0`”。
- 通用修正:
  - 在 `graph audit` 增加 `warnings.api_link_coverage`,只读统计 `frontend_api_call/backend_endpoint/calls_api` 覆盖率、未链接样本和诊断状态。
  - `clean/error_count` 仍只看结构 error;API 覆盖不足是 warning,不变成硬门禁。
  - 有效 `calls_api` 必须同时满足 source 是 `frontend_api_call`、target 是 `backend_endpoint`;畸形 `calls_api` 单独计入 `invalid_calls_api_edges`,避免误报覆盖完成。
  - `_unreadable_report` 同步补齐字段,旧 schema/坏库只读审计仍 fail-soft,CLI/JSON 形状稳定。
- 兄弟审计发现并已修:
  - 只校验 `calls_api.source` 会把 `frontend_api_call -> backend_function` 畸形边误算为已覆盖。
  - 已改为基于 valid edge 集合计算 linked counts,并补 wrong-kind 回归测试。
- 真实 OMS 复验:
  - `graph audit --project sample-project-gamma --json` 仍为 `clean=true`。
  - `api_link_coverage`: `frontend_api_calls=2374`,`backend_endpoints=4`,`calls_api_edges=0`,`raw_calls_api_edges=0`,`invalid_calls_api_edges=0`,`frontend_link_ratio=0.0`。
  - Markdown 输出直接展示 `frontend API link coverage: 0/2374 linked, backend endpoints 4, calls_api 0` 和未链接样本。

验证:

```powershell
python -m pytest tests/test_graph_audit.py
python -m pytest tests/test_web_graph_audit.py tests/test_graph_provenance.py tests/test_cli_parser.py
python -m ruff check codev_platform\graph\audit.py tests\test_graph_audit.py
git diff --check -- codev_platform\graph\audit.py tests\test_graph_audit.py
python -m codev_platform.cli graph audit --project sample-project-gamma --json
python -m codev_platform.cli graph audit --project sample-project-gamma
```

结果:`18 passed` + `29 passed`,ruff 通过,diff whitespace 通过;真实 OMS audit clean 且覆盖 warning 可见。

## 十二、全局 graph audit 覆盖摘要

- 继续验证发现:`graph audit --project sample-project-gamma` 已能看到 `api_link_coverage`,但 `graph audit --all` 普通文本只打印 `OK clean`,会把 `sample-project-gamma` 这种结构 clean 但 API 链路覆盖为 0 的项目隐藏掉。
- 设计上不改 JSON 契约、不改 `clean/error_count`、不把 warning 升级为门禁失败;只在全局文本行尾追加短摘要。
- 通用修正:
  - 在 `graph.audit` 增加 `api_link_coverage_brief(report)`,作为纯展示 helper。
  - 全覆盖或无前端 API 时返回空串,避免全局巡检刷屏。
  - `cli graph audit --all` 只拼接 helper 输出,不复制字段判断。
- 兄弟审计未发现阻断问题;按建议补:
  - 全 linked 时摘要静默的回归测试。
  - `graph audit --all --json` parser 参数组合测试。
  - helper 注释从 CLI 场景改为更中性的“API 覆盖短摘要”。
- 真实全局复验:
  - `codev-platform`:全覆盖,不显示摘要。
  - `sample-project-gamma`:显示 `api 0/2374 linked (backend 4, calls_api 0)`。
  - `openclaw-stock`:显示 `api 393/395 linked (backend 202, calls_api 393)`。
  - `--all --json` 仍为纯 JSON,`total_errors=0`。

验证:

```powershell
python -m pytest tests/test_graph_audit.py tests/test_cli_parser.py
python -m ruff check codev_platform\graph\audit.py codev_platform\cli.py tests\test_graph_audit.py tests\test_cli_parser.py
python -m codev_platform.cli graph audit --all
python -m codev_platform.cli graph audit --all --json
```

结果:`28 passed`,ruff 通过;真实全局 audit 文本摘要可见,JSON 契约不变。

## 十三、Web Dashboard 展示 API 链路覆盖

- 继续收口可见性时确认:CLI 单项目和 `audit --all` 已能看到 `api_link_coverage`,但 Web Dashboard 的“图谱结构健康”卡片仍只展示结构 error/warning,看不到 OMS `0/2374` 的前后端链路缺口。
- 设计上不新增请求、不改图谱存储、不改变 `clean/errorCount` 语义;只扩展 `/api/v1/graph/audit` 响应并在已有 Dashboard 卡片展示短摘要。
- 通用修正:
  - `GraphAuditResponse` 增加扁平 API 链路字段:`apiLinkStatus/apiLinkBrief/apiLinkDiagnosis/frontendApiCalls/backendEndpoints/callsApiEdges/...`。
  - Web route 复用 `api_link_coverage_brief(report)` 输出短摘要,不在 web 层复制诊断规则。
  - Dashboard `GraphHealthCard` 显示 API 链路 tag、已链接比例、后端 endpoint、有效 `calls_api`、畸形 `calls_api` 和 warning 诊断。
  - `clean/errorCount` 仍只代表结构 error;API 链路不足仍是 warning。
- 生成层处理:
  - 正常应跑 `pnpm run api` 更新 `web-ui/src/services/apis/**`。
  - 本步前工作区存在非本步 health OpenAPI dirty,直接生成可能把无关 schema 混入;因此只对 `typings.d.ts` 的 `GraphAuditResponse` 做最小手工同步,并用 `tsc` 校验。后续工作区干净时可再跑生成器确认零 diff。
- 兄弟审计未发现阻断问题;按建议补:
  - Web API fully linked 用例,断言 `apiLinkStatus=linked` 且 `apiLinkBrief=""`。
  - Web API malformed `calls_api` 用例,断言 `clean=true/errorCount=0/invalidCallsApiEdges=1/callsApiEdges=0`。
  - 前端 label 从 `calls_api` 改为“有效 calls_api”,降低 raw/valid 误读。
- 真实 OMS Web 响应探针:
  - `clean=True,errorCount=0`。
  - `apiLinkStatus=frontend_backend_unlinked`。
  - `apiLinkBrief=api 0/2374 linked (backend 4, calls_api 0)`。

验证:

```powershell
python -m pytest tests/test_web_graph_audit.py tests/test_graph_audit.py
python -m ruff check codev_platform\web\schemas\graph.py codev_platform\web\routes\graph.py tests\test_web_graph_audit.py
npm --prefix web-ui run tsc
npx eslint --ext .tsx --format=pretty src/pages/dashboard/components/GraphHealthCard.tsx src/services/apis/typings.d.ts
```

结果:`25 passed`,ruff 通过,前端 tsc 通过,定向 eslint 通过。全量 `npm --prefix web-ui run lint:js` 当前仍被非本步 `src/pages/agent/components/ChatPanel.tsx` 缩进问题阻断。
