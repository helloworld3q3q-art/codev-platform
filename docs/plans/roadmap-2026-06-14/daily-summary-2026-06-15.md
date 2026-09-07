# daily-summary 2026-06-15 —— 平台产品化迭代 Day 2:P2 token 完成 + Hibernate 图谱抽取(影响面根治)

> 续 [`daily-summary-2026-06-14.md`](./daily-summary-2026-06-14.md)(Day 1 止于"P2 web 路由下一步")。
> 今日两条线:① P2 token web 化收尾(后端路由 + 前端页);② **业务仓影响面根治**(web agent 在 sample-project-beta 答"入库订单加列影响哪些模块"撞 max_steps → 挖到统一图谱 DB 层全黑 → 治本)。
> 贯穿纪律不变:**先核实/验证再动手、镜像复用不堆码、验证驱动找真根因**。commit 链见末。

## 一、P2 多组织 token 签发 web 化 ✅(`a6bc08f` / `2e977a5` / `6260396`)

Day1 已抽 `issue_token` 共享纯函数 + 定死设计。今日实现并上线:

- **后端 4 文件**(`a6bc08f`):`schemas/tokens.py` + `services/token_service.py`(复用 `UserService._guard_org_member` + `issue_token` + `PgTokenStore`)+ `routes/tokens.py`(`org_id=sess.org_id` **绝不由 client**)+ `app.py` 挂载。**5 安全红线全落**,并**补一个洞**:list/revoke 加 **org 隔离**(防 org_admin 凭 hash 前缀跨 org 吊销)。12 越权单测(fake store 解耦真 SQL)。
- **openapi dev 放行**(`2e977a5`):token 模式下 `/openapi.json` 被鉴权挡 → 按 `deployment.mode != prod` 条件放行供 `pnpm run api`,prod 仍鉴权不暴露 API 全表。
- **前端页**(`6260396`):`pages/tokens` 列表 + 签发抽屉 + 吊销确认 + **一次性明文弹窗**(Bearer/PLATFORM_TOKEN 复制模板),归"系统管理 / 仅管理员可见"。组件直调生成接口、utils 只放纯转换(恪守 `frontend-no-services-wrapper`)。`pnpm run api` 再生成 tokenapi.ts。
- **验证**:WSL venv(有 sqlalchemy extras)33 测试 + 154 回归;tsc 通过;token 文件 lint 干净;codev-web 重启暴露新 schema。

## 二、Hibernate HBM/HQL 图谱抽取 + 实体类名→表桥(`ba421f1` / `b8aab14` / `8f2cb31`)

**起因**:web agent 在业务仓 sample-project-beta 答"入库订单加列影响哪些模块"两次撞 max_steps,核心死因 `table_usage('OmsInboundOrder') → found:false`。

**验证驱动**(本迭代再次印证):查 sample-project-beta 图谱 → **`db_table = 0`,DB 层全黑**。隔离跑 `SqlPlugin.analyze` → **真根因比设计预想更深**:

| # | 修复 | commit | 实证 |
|---|---|---|---|
| 1 | **编码容错(真根因)** | `b8aab14` | 一个 GBK 编码 `.sql` 让 Pass 1 `read_text(utf-8)` 崩 → 整个 SQL 插件 analyze 抛异常 → ingest **fail-soft 静默丢弃整插件**(8 插件列表从无 builtin.sql)→ 1068 hbm 表全扫不到。`_common._read_text`(utf-8 失败回退 errors=ignore),detect/analyze 全走它 |
| 2 | **HBM 抽取器** | `ba421f1` | `hbm.py`(Pass 3.6):扫 `<class>/<joined-subclass> table=` → db_table + db_column,**实体类名写 `meta.entity_class`**;继承列按 table 元素直接子归属。复用 `_emit_table_nodes` 零新 kind。**db_table 0→1117** |
| 3 | **实体类名→表桥** | `ba421f1` | `impact._resolve`:名字未命中按 `meta.entity_class` fallback → 拿**实体类名** OmsInboundOrder 也解析到表 OMS_INBOUND_ORDER,find_impact/find_table_usage 双受益。**found:True** |
| 4 | **HQL 访问边** | `8f2cb31` | `core._scan_java_hql`:扫 .java 字面量 HQL(`from OmsInboundOrder` 引用实体名),经 entity_class 映射回表产 `backend_function(类粒度)→reads/writes_table` 边。**只对已知实体产边**杜绝误连。**表访问边 0→2427,`find_table_usage('OmsInboundOrder')` usage backend=45**(各入库 service) |

**全部镜像复用**:HBM 照 `xml_mapper.py`、HQL 照 `_scan_java_dml`、桥照 `_resolve_endpoint`,复用 `_emit_table_nodes`/`_emit_table_access`,核心遍历零改。`entity_class` 一物两用(解析桥 + HQL 映射)。13 新单测,analyze 全仓 4.9s 不慢。worker 重 ingest live。

**诚实边界**:HQL 节点**类粒度**且未接进 Java 调用图 → usage 到 service 层(45),前端→端点→service 精确链仍交 codegraph(284K 节点 Java 调用图)。对"加列影响面"已足够。

## 三、运维坑:web agent 没重启 → 跑旧 impact 码

用户重跑 agent 仍 found:false。诊断:**数据库早建好**(召回已返回新 `db_column`/`frontend_component` 节点),但 **`codev-agent.service`(启动 06-14 18:25,早于 ~22:xx 改动)进程内跑旧 `impact.py`,没有实体桥**。我之前只重启 codev-reindex/codev-mcp-graph,**漏了 codev-agent**(它进程内调 impact,见 [[web-agent-runs-in-codev-agent-service]])。重启后 `agent_tool_health` **8/11 OK**(impact/table_usage/codegraph/list_dir 全通)。`search_docs` 仍挂=旧 daemon ASGI 错(`Expected http.response.body`),已重启新 daemon,独立 chroma flakiness 不挡代码影响面。

## 四、教训沉淀

1. **验证驱动找真根因**:设计以为"HBM 缺",验证才发现"编码崩在前"——一个坏编码文件 fail-soft 静默丢掉整插件,**会让任何遗留中文仓 DB 层全黑**。不验证就只会补 HBM 而 HBM 永远到不了。
2. **镜像复用是不堆码的范本**:三个新能力都是"照已有 source 抽取器/resolver 加一个文件 + 一处接线",核心零改(同 provider/plugin registry 铁律)。
3. **WSL .venv editable 指向 /mnt/d Windows 树**:改 codev-platform 码让 WSL 服务生效=**重启**(重新 import /mnt/d)不必 pull;长驻进程缓存 import 必 restart。([[wsl-venv-editable-points-to-mnt-d]])
4. **web agent 改 impact 必重启 codev-agent**:不是只重启 graph 服务——agent 进程内调 impact,数据建好≠agent 用上新解析码。

## commit 链(Day 2 段)
P2:`a6bc08f`(token 后端路由+org隔离)→`2e977a5`(openapi dev 放行)→`6260396`(前端 tokens 页+run api)。
图谱:`ba421f1`(HBM 抽取器+实体桥)→`b8aab14`(非UTF-8 容错=真根因)→`8f2cb31`(HQL 访问边)。全程 origin/dev,Windows+WSL 单测全绿,worker 重 ingest + codev-agent 重启 live。
