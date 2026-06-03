# 对 deep-audit-2026-06-03 的复核(审计的审计)2026-06-03

> 对 [`deep-audit-2026-06-03.md`](deep-audit-2026-06-03.md) 的逐条源码级复核。原审计 12 条
> 现象复现**全部属实、无误报**,但存在系统性的"**定级偏高 + 漏看链路运行时是否真的通**"
> 问题:它擅长静态扫"代码长这样",弱在"这条路跑起来到底通不通"。本复核把 4 簇追到代码
> 底层,修正定级,并补 6 个原审计完全没有的发现。

---

## 一、结论

| 维度 | 复核判断 |
|---|---|
| 现象复现真实性 | 12/12 属实,无误报 |
| 定级准确性 | 3 条 P1 中 1 条根因定性错、2 条应降级;多条 P2 实际风险被分库/中间件兜住 |
| 链路运行时盲区 | 原审计按标称连线画图,未追 `_noop_trigger` / webhook.log → 漏判两条核心链路实际不通 |
| 净新增发现 | 6 条(原审计 0) |
| 本轮已落地修复 | reindex runner timeout(P2#4),5 测试,见 §四 |

---

## 二、四簇逐条修正定级

### 簇1 auth/authz(原 P1#1 + #8 + #9)

完整链路:`AuthMiddleware`(gateway,带 `_PUBLIC_PATHS` 白名单)在请求到 route **之前**就拦截。

- **passthrough(dev,默认)**:任何请求 → `Identity(all_projects=True)`(`gateway/auth.py:108`),不验任何东西。
- **token(prod,被 `assess_prod_requires_token` 强制)**:无有效 token → 中间件 **401**,到不了 route。

| 原审计条目 | 原级 | 修正 | 依据 |
|---|---|---|---|
| 未登录访问 projects/indexes | P1 | **dev-only,降 P2** | prod token 模式中间件 401 拦;passthrough 绑非 loopback 有启动 loud WARN + `assess_*` 护栏(`gateway/auth.py:187-224`) |
| projects 路由 token 模式零授权 | (混在 P1) | **真 P1,原审计未单列** | `routes/projects.py` 5 路由无 `current_session`/`require_org_role`;中间件只做认证不做授权 → 任意有效 token 可跨 org register/load/unload |
| indexes/rebuild | P1 | **降 P2** | 有 `require_project_access`(`routes/indexes.py:34`),token 模式强制 org+白名单;仅 passthrough 退化 advisory |

**净新增发现 ①(修复难题)**:给 projects 加 `current_session` 会撞 **web session token vs gateway token 双轨**——`current_session`(`security/deps.py:30`)读 web 登录签发的 token(`session_store`),与 gateway token 是两套。token 模式下前端要同时持两张票。**修复前置 = 先收口 web 控制台鉴权主线(走 session 还是 gateway),不是加一行依赖。**

### 簇2 reindex worker + job 系统(原 P2#4 + "job 重启丢状态")

真实链路:`/indexes/rebuild → IndexService → JobService.submit → self._trigger = _noop_trigger`(`job_service.py:35,52`)**什么都不做**,只建内存 job 记录返回 jobId。真实队列 `FileSpoolQueue`(磁盘持久)的喂入者经 grep 确认仅:`webhook/server.py:125` + `ops/reindex.py` / `ops/reindex_queue.py`(手动 CLI)。

| 原审计条目 | 原级 | 修正 | 依据 |
|---|---|---|---|
| 未登录提交 rebuild | P1 | **降级** | endpoint 是 noop 空壳,只能轻量 DoS(占锁/内存),**不触发真实重建** |
| runner 无 timeout | P2 | **属实,已修** | 唯一无界点 `runners.py:61`;worker 对失败已很健壮(rc2 重试/rc≠0 丢弃/except 丢弃/health timeout=120),串行是 SQLite 单写者刻意设计 |
| job 重启丢状态 | P2 | **概念混淆** | 丢的是装饰性内存 job 层;真队列 FileSpoolQueue 磁盘持久不丢 |

**净新增发现 ②**:web "重建索引"按钮是**假的**——UI 拿到 jobId、job 进 Pending,但无任何东西真的重建(trigger 是 noop,与 FileSpoolQueue 未接线)。原审计链路图标称"提交 job → worker 消费"与代码不符。

### 簇3 graph store 隔离(原 P2#5)

`upsert_result` 信任 `n.project_id`(`store.py:173`)而非校验过的入参;`load_graph` 只 `WHERE plugin=?`(`store.py:242`)。靠 `data/graph_store/<pid>.sqlite` 物理分库隔离。

| 原审计条目 | 原级 | 修正 | 依据 |
|---|---|---|---|
| graph 无 project_id 过滤 | P2 | **机制属实,实际风险再降一档** | 物理分库,生产零泄漏;串库仅在显式共享 `store_path`(测试)场景 |

**净新增发现 ③**:幂等 `DELETE FROM nodes WHERE plugin=?`(`store.py:165`)**project-blind** —— 共享库场景下为项目 A 重灌插件 X 会**连项目 B 的插件 X 节点一起删**(比"读取串库"更危险的写时静默删)。生产被分库掩盖。

### 簇4 webhook(原 P2 webhook 风险 + P3 日志)

| 原审计条目 | 原级 | 修正 | 依据 |
|---|---|---|---|
| allow_insecure 跳验签 | 风险 | **方向反了** | 默认 **fail-closed**(`server.py:97-98`),需主动开 allow_insecure **且**反代暴露才成立 |
| 日志写包目录 | P3 | **精确属实** | `paths.py` 有 `data_root`("data/ 可打包带走")却没用;`webhook.log`/`worker.log` 落 `__file__.parent`,wheel/只读安装写失败 |

**净新增发现 ④**:webhook→reindex 链**当前实际瘫痪** —— `webhook.log` 06-01~06-02 全是 `webhook.secret 未配置, fail-closed 拒绝`,所有 gitea 推送被拒,自动 reindex 没在工作。
**净新增发现 ⑤**:webhook >1MB payload 被 413 拒绝(`server.py:26` 注释实录),即使配了 secret 大 push 仍失败。

---

## 三、⭐ 净新增发现 ⑥:平台无可用的自助重建路径(跨簇系统性)

合 簇2+簇4:平台自身两个触发 reindex 的入口**现在都不通**——

- **webhook(自动)**:fail-closed 全拒(secret 未配)→ 死;
- **web `/indexes/rebuild`(手动 UI)**:`_noop_trigger` 空壳 → 假;
- **唯一真能触发重建 = 手动 CLI**(`codev-platform reindex` / `update-local-ai`)。

这是比原审计任何一条 P1/P2 都更影响"平台能不能用"的事实,原审计因停在标称连线而完全没看到。

---

## 四、本轮已落地修复

**reindex runner timeout(原 P2#4)** — `codev_platform/reindex/runners.py`:
- `CliReindexRunner.run` 包 `subprocess.run(cmd, timeout=_runner_timeout(cfg))`;
- config 驱动 `reindex.runner_timeout_sec`(默认 1800s,≤0 禁用,非法值回默认);
- 超时 kill 子进程 + 返回 **rc=124** → worker 走"非 0 非 2 → 丢弃防死循环"(挂死 job 不重试堵队头);
- 测试 `tests/test_reindex_runner_timeout.py`(5 例:传参/124/默认/禁用/非法回退),`22 passed`,compile OK。

---

## 五、修订后的修复排期(取代原审计 §优先级)

```
P0 运营(现在就坏,原审计漏):
  - 决断 webhook.secret: 配上修活自动 reindex / 或明确下线(否则自动链是假的)
  - web rebuild 空壳: 接线 FileSpoolQueue / 或 UI 标注"仅 CLI 重建"

第一波 安全(先写失败测试):
  1. 收口 web 控制台鉴权主线(session vs gateway 双轨)← 簇1 修复前置
  2. projects 路由补 current_session + register/load/unload 加 admin 角色
  3. logout 撤 access(复用 revoke_user 范式) + register 原子写(open x)

第二波 韧性:
  ✅ reindex runner timeout(本轮已做)
  4. PG fallback prod fail-fast(except 收窄到 ImportError)
  5. graph store: 写强制 n.project_id=入参 + 读 WHERE project_id + DELETE 带 project_id

第三波 技术债(各自立 plan):
  6. TS 工具链版本对齐 → 解 @ts-nocheck → 收紧 any
  7. SQLAlchemy Core+Alembic(大重构,从 bug 清单拆出)
  8. 日志迁 data_root()/logs + .gitignore(webhook.log 已进 git)
  9. broad except 区分"上报型 vs 真吞型"(_checks.py 多为上报型,定级偏高;真吞的是 worker._log / account_store fallback)
```

与原审计差异:① 加 P0 运营止血(原审计漏的两条不通链路);② 簇1 真 P1 改为"projects 授权缺失"而非"未登录";③ 簇1 修复增加"双轨鉴权收口"前置;④ SQLAlchemy 降出第一/二波;⑤ broad except 定级按"是否真吞"细分。

---

## 六、收尾状态(2026-06-03 复核闭环)

§五 排期逐项核到代码底层后的真实状态(✅=已落地并测 / ◻=运营决断非代码 / ⏳=按设计保留):

| 项 | 状态 | 落地点 |
|---|---|---|
| P0 webhook.secret 决断 | ◻ **运营** | 代码已 fail-closed 正确(配 secret 即活 / 或显式下线);配密钥是部署动作,非代码改 |
| P0 web rebuild 空壳接线 | ✅ | `index_service.make_reindex_dispatch_trigger` 把 index_rebuild job 派进真实 FileSpoolQueue(与 webhook 同队列, worker 消费);'all' 展开 runners.kinds();`jobs.py` 注入;submit 派发失败释放锁;`test_reindex_dispatch_trigger.py` 5 例 |
| 1 双轨鉴权收口 | ✅ | `SessionAwareAuthenticator`(`8c6153c`) |
| 2 projects 路由授权 | ✅ | list/detail/load/unload 带 `current_session`,register 带 `require_org_role("admin")`;service 逐项目 `_authorize_project` |
| 3 logout 撤 access + register 原子写 | ✅ | `sessions.revoke` 撤 access+refresh + `revoke_user`;`project_write_repo` 用 `open(target,"x")` 排他写 |
| 4 PG fallback prod fail-fast | ✅ | `bind_account_stores`:`except ImportError`→回退内存,`except Exception`+prod→raise |
| 5 graph store project_id | ✅ | 写 `DELETE ... WHERE plugin=? AND project_id=?` + INSERT 强制入参 project_id;读 `WHERE project_id=?`(nodes) |
| ✅ reindex runner timeout | ✅ | 本复核 §四 |
| 6 TS 工具链 / 解 @ts-nocheck | ✅ | TS 4.9→5.4 + 清零类型错 + 移除 fetch.ts @ts-nocheck(`58256b1`/`5ce34a5`/`9260957`) |
| 7 SQLAlchemy Core+Alembic | ✅ | rbac/account store 迁移 + tables.py 收口 + baseline migration(`0e6f220`/`c25066f`) |
| 8 日志迁 data_root/logs + gitignore | ✅ | webhook.log / worker.log / MCP daemon 全走 `logs_dir()`;`.gitignore` `*.log` 覆盖,无 .log 入库 |
| 9 broad except 区分上报型 vs 真吞型 | ⏳ **按设计保留** | 真吞的两处已收口:worker._log 是写日志失败兜底(可接受)、account_store fallback 已收窄 ImportError;MCP daemon 真静默 except 已加可观测(`2943742`)。`_checks.py` 多为上报型(原审计定级偏高)。剩余纯定性,无独立代码债 |

**结论**:§五 12 项中 10 项已落地并测,1 项(webhook.secret)是运营决断,1 项(broad except)按设计保留。复核闭环。测试基线 786→**791 passed / 5 skipped**。

**唯一待用户决断**:webhook.secret —— 配上则自动 reindex 链复活(配 `webhook.secret` + VCS 端同密钥),或显式下线只走 web rebuild(已真实)/ CLI。
