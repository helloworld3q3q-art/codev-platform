# daily-summary 2026-07-08 —— OMS Spring 接口映射解析收口

> 承 P3 多仓多根索引真实项目验收。今天聚焦 OMS Java 后端统一图谱解析:先用真实 dsm 仓确认 `backend_spring=0` 的根因,再补当前 Spring 插件的接口映射能力,不新增插件、不改 ingest 核心。

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

## 五、剩余边界

- `builtin.call_resolvers` 在本次真实 ingest 后仍为 0;这说明 endpoint 已入图,但端点到 SQL/表的函数链路还没有命中 store/codegraph join。该项应作为下一步调用链覆盖问题单独追,不再混进 Spring endpoint scanner。
- 当前 scanner 仍是正则级轻量解析,适合平台低成本索引;如果后续遇到复杂 Java AST 场景,再评估引入 tree-sitter/JDT 作为可插拔 parser adapter。
