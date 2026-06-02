# 栈适配器三层 taxonomy + 插件自动发现(2026-06-02)

> 定位: 给 cross-link / codegraph 的 analyzer 插件定一套**语言 × 框架 × DB** 分层模型,
> 让后续多框架插件(Vue / Node-Express / Spring / ASP.NET / SQL 方言)可并行落地,
> 共享同一语言基座、产出同一套统一 NodeKind/EdgeKind。配套 registry 自动发现机制。
>
> 关联: `modular-core-plugin-extension-decision-2026-06-02.md`(核心/插件边界)、
> `pluginized-fullstack-ai-platform-2026-06-01.md`(Phase 3/4 前后端插件)、
> `agent-provider-architecture.md`(按协议族而非厂商分文件,本 taxonomy 同思路)。

---

## 一、核心判断:按"语言基座 + 框架适配 + DB 方言"三层,不按项目/不按厂商

对齐 `agent-provider-architecture.md` 铁律(按协议族分文件,厂商是 config 数据):

- **语言**是基座(代码层公共能力:文件遍历、AST、import、稳定 node id)。
- **框架**是建在语言基座上的适配层(路由/组件/endpoint/ORM 识别规则)。
- **DB** 是横切层(SQL 家族 + 方言维度,同一 scanner 带 dialect 标记)。

判定: 同一语言被多框架共用 → 语言层抽成基座(如 `_stack_scan` 是 JS/TS 基座);
框架是数据差异(detect 命中即用),不为每个项目写脚本。

---

## 二、三层模型

### Layer 1 — 语言基座(language base)

| 语言家族 | 后缀 | 共用框架 | 基座现状 |
|---|---|---|---|
| **JS/TS** | `.js` `.jsx` `.ts` `.tsx`(ts ≈ tsx 归一,统一 `language="typescript"`) | React / Vue / Node-Express | `_stack_scan.py` 已是 JS/TS 基座 |
| **Python** | `.py` | FastAPI(已有) / Django / Flask | `_stack_scan.scan_fastapi` + AST,可抽 `_py_scan` |
| **Java** | `.java` | Spring | 待建 `_java_scan`(正则/javalang) |
| **C#/.NET** | `.cs` | ASP.NET | 待建 `_dotnet_scan` |
| **SQL** | `.sql` | (无框架,直接到 DB 层) | 待建 `_sql_scan`(见 Layer 3) |

语言基座职责(框架插件复用,不重复造):
- 文件遍历(跳过构建物/依赖,`_SKIP_DIRS`)、相对路径正斜杠归一(`_rel`)。
- 稳定 node id 规则 `"<project_id>:<kind>:<stable-key>"`(跨插件可链接的硬约束)。
- 语言级解析原语(JS/TS 正则窗口扫 + Python AST)。
- 跨插件链接 `link_api_calls()`(URL + method 容差匹配,单一真值源)。

### Layer 2 — 框架适配(framework adapter)

每个框架 = 一个 builtin 插件文件,detect 基于 **repo 内容**(不靠目录名/项目名)。

| 框架 | 语言基座 | detect 依据 | 第一版产出 |
|---|---|---|---|
| **React**(已有) | JS/TS | `package.json` deps 含 `react` 或存在 `*.tsx` | `frontend_route` / `frontend_api_call` + `renders` / `calls_api` |
| **Vue**(待建) | JS/TS | deps 含 `vue` 或存在 `*.vue` SFC | 同上(`.vue` SFC + vue-router + pinia/vuex action) |
| **Node-Express**(待建) | JS/TS | deps 含 `express` 或 `app.get/post(...)` / `router.<m>(...)` | `backend_endpoint` + `defines_api` |
| **FastAPI**(已有) | Python | `import fastapi` / `from fastapi` / `@router.<m>(...)` | `backend_endpoint`(AST 扫装饰器) |
| **Spring**(待建) | Java | `@RestController` / `@RequestMapping` / `@(Get/Post)Mapping` | `backend_endpoint` / `backend_function` + MyBatis/JPA → `reads/writes_table` |
| **ASP.NET**(待建) | C#/.NET | `[ApiController]` / `[HttpGet/Post]` / `MapGet(...)` | `backend_endpoint` + EF Core → table 关系 |

铁律(防"每框架重造基座"):框架插件 `analyze()` **只调语言基座函数 + 自身框架规则**,
绝不复制文件遍历 / node id / 链接逻辑。React 与 Vue 共用 `react_detect` 同级的 JS/TS
遍历器与 `link_api_calls`,差异只在"怎么识别路由/组件/调用"。

### Layer 3 — DB 方言(SQL family × dialect)

SQL 是一个家族,方言(sqlite / mysql / postgres)是**同一 scanner 的维度**,不是三个插件:

| 维度 | 取值 | 落点 |
|---|---|---|
| family | `sql` | 插件 `builtin.database_sql` |
| dialect | `sqlite` / `mysql` / `postgres` | `GraphNode.meta["dialect"]` + `language="sql"` |

detect 依据: 存在 `*.sql` / Flyway `db/migration/V*.sql` / ORM schema 文件。
产出: `db_table` / `db_column` 节点 + `defines_table` / `defines_column` 边(对齐
cross_link 适配器现有裸字符串 kind)。方言只改解析细节(`AUTO_INCREMENT` vs `SERIAL`
vs `AUTOINCREMENT`),节点形状一致 → 一个 scanner 带 `dialect` 参数,杜绝方言爆炸成多文件。

---

## 三、各插件产出的统一 NodeKind / EdgeKind(契约)

build agent 必须只产 `graph/schema.py` 的 NodeKind/EdgeKind(无精确对应才用裸字符串 +
原始值进 meta,与 cross_link 适配器同范式)。映射汇总:

| 层 | 插件 | 产 NodeKind | 产 EdgeKind |
|---|---|---|---|
| 前端 | React/Vue | `frontend_route` `frontend_component` `frontend_api_call` | `renders` `calls_api` |
| 后端 | FastAPI/Express/Spring/ASP.NET | `backend_endpoint` `backend_function` | `defines_api` `calls` `reads_table` `writes_table` `updates_table` |
| DB | database_sql | `db_table` `db_column` | `defines_table`*/`defines_column`*(裸串) |
| 跨层 | cross_link(适配器) | 以上全部(映射自 sqlite) | 以上全部 + `calls_api` |

\* `defines_table`/`defines_column` 在统一 EdgeKind 暂无枚举,沿用 cross_link 适配器既定
裸字符串约定(原始值同时写 `meta["cross_link_rel"]`),后续若高频再提升为枚举。

**跨插件链接落点约定**(per-plugin ingest 模型):前端插件在自己的 `AnalyzerResult` 里
产 `frontend_api_call --calls_api--> backend_endpoint` 边(同仓后端节点仅作 URL 解析参照,
不并入正本——backend 插件产 endpoint 正本)。多语言混合仓由各前端插件各自链接,
URL 匹配统一走 `_stack_scan.link_api_calls`。

---

## 四、detect 约定(build agent 必须遵守)

1. **基于 repo 内容,不基于项目名/目录名**(不写死 web-ui / codev_platform)。
2. **廉价探测**:查标志文件(package.json deps / pom.xml / *.csproj)或浅扫特征字面量,
   不做完整 AST 扫(完整扫留给 `analyze`)。
3. **多框架共存友好**:detect 互不互斥——一个 monorepo 可同时命中 React + Spring + SQL,
   各插件独立产出,executor 各自隔离。
4. **缺数据 ≠ 不适用**:核心平台能力(cross_link)detect 恒 True,缺索引由 analyze 返空
   AnalyzerResult(不抛);技术栈插件按内容命中。

---

## 五、registry 自动发现(已落地)

`registry._discover_builtins()` 从"显式手工 import 三行"改为**扫 `plugins/builtin/` 目录**:

- `pkgutil.iter_modules` 遍历 builtin 包下**非下划线**模块(`_stack_scan` 等共享工具跳过)。
- 每模块 import 后 `inspect.getmembers` 找**本模块定义的** `AnalyzerPlugin` 具体子类
  (排除基类、import 进来的别处类、抽象类),逐一实例化 + `register_plugin`。
- 去重幂等:`register_plugin` 按 `name` 入表(重名覆盖);坏模块 import/实例化失败只
  warning + 跳过,不阻断其余(对齐 executor "插件失败不拖垮核心")。

**收益**:build agent 新增框架插件 = **只丢一个 `builtin/<framework>.py` 文件**(类继承
`AnalyzerPlugin` + 唯一 `name`),无需改 registry。三层 taxonomy 各框架可并行独立落地。

---

## 六、build agent 速查(怎么加一个框架插件)

1. 选语言基座:JS/TS 复用 `_stack_scan`;Python 复用其 AST 工具;Java/.NET 先抽 `_<lang>_scan`。
2. 新建 `codev_platform/plugins/builtin/<framework>.py`,类继承 `AnalyzerPlugin`,
   `name = "builtin.<framework>"`(唯一),`detect` 按 §四,`analyze` 只调基座 + 框架规则。
3. 产统一 NodeKind/EdgeKind(§三),跨层 `calls_api` 边落在前端插件结果里。
4. 不改 registry(自动发现)。加测试(detect 真值 + analyze 非空 + run_applicable 含它)。
5. 验证: `python -m pytest tests/test_plugins_*.py`。
