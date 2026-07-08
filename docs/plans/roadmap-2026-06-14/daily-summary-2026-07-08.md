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
python -m codev_platform.cli reindex --repo D:\OmsWork\scl-sod-imp-dsm --ingest
python -m codev_platform.cli graph stats --project oms-work
python -m codev_platform.cli graph audit --project oms-work --json
git diff --check
```

结果:

- 目标测试 `46 passed`。
- DSM 真实仓 graph ingest 正常完成。
- `builtin.backend_spring node_count` 从 0 变为 3。
- 3 个 endpoint 均来自 `ParamMappingConfigMicroservice` 接口 mapping,并带 `controller_file=...ParamMappingConfigMicroserviceImpl.java`。
- `graph audit --project oms-work` 为 clean。
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
python -m codev_platform.cli reindex --repo D:\OmsWork\scl-sod-imp-dsm --ingest
python -m codev_platform.cli graph stats --project oms-work
python -m codev_platform.cli graph audit --project oms-work --json
git diff --check
```

结果:

- 目标回归 `80 passed`,ruff 通过,`git diff --check` 无 whitespace error。
- DSM `SqlPlugin` 真实扫描产出 `ParamMappingConfigQueryService.queryConfigs/queryAllEnabled/queryMappingValue -> unified_param_mapping_config` 三条 `reads_table` 边。
- DSM graph ingest 正常完成;`builtin.sql node_count=1358 edge_count=1300`。
- `builtin.call_resolvers edge_count` 从 0 变为 3。
- 三个 `/param-mapping/*` endpoint 均连到 `ParamMappingConfigQueryService` 对应 service 方法,`graph audit --project oms-work` 为 clean。

## 七、剩余边界

- dao-service 当前只覆盖 `select()/selectCount()` 读路径;若后续遇到 update/delete/save 等 DSL,应在 `java_orm.py` 内按操作族补子扫描策略,不塞到 `SqlPlugin.analyze` 主流程。
- 当前 scanner 仍是正则级轻量解析,适合平台低成本索引;如果后续遇到复杂 Java AST 场景,再评估引入 tree-sitter/JDT 作为可插拔 parser adapter。
