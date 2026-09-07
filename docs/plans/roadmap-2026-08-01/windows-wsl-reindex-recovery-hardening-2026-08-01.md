# Windows / WSL reindex 恢复链路加固计划

## 目标

彻底消除以下同源故障：

- Windows PowerShell 直接执行 `codev-platform post-commit` 时，跨 `\\wsl.localhost` 写 FileSpoolQueue 只报裸 `OSError`。
- `health --all` 的 18083 服务不可达被误判为索引未完成或索引损坏。
- worker 模式仅凭 `reindex.log` 缺少 `finished` 判断失败，忽略当前 HEAD 已被 manifest 覆盖。
- enqueue、pending 或 daemon 故障触发未经批准的全量 `update-local-ai`。
- Windows `serve-mcp start/status` 在 WSL-owned data 配置下误启 18xxx 本地影子服务，platform-docs 又因对 UNC lifecycle lock 调用 `msvcrt.locking` 以 `EINVAL` 退出。
- `code_vec` 已安全推进 checkpoint 后遇到瞬态 Chroma compaction 非零退出，却被当作终态失败而不交回队列续跑。

修复同时覆盖 `codev-platform` 真值源与 `C:\workspace\project`（`openclaw-stock`）消费仓，确保正常 Git hook、手工恢复、健康检查和 skill 指引使用同一套边界。

## 任务等级与适用规则

- 等级：L4（跨 Windows / WSL、Git hook、reindex 队列、常驻 MCP 服务、跨仓分发）。
- 必读：`AGENTS.md`、`.codex/rules/workflow.md`、`.codex/rules/ai-tools-mcp.md`、`.codex/rules/security.md`、`.codex/rules/windows-powershell.md`、`.codex/rules/code-quality-discipline.md`、`.codex/rules/verification-checklist.md`。
- Skill：`skill-creator`，用于保持 `ai-health`、`git-commit`、`update-local-ai` 简洁、可触发且可验证。
- 历史设计：WSL systemd worker 是正式消费端；File/PG queue 语义不可降级；manifest 是索引完成真值。

## 关键约束

1. WSL 独占位于 WSL ext4 的 file queue；Windows 不直接通过 UNC 执行其锁和耐久写协议。
1. WSL-owned data 同时意味着 WSL systemd 独占平台 MCP 服务；Windows 只探测/启动 platform source 的 19xxx 端点，不拉起读取 UNC 数据的 18xxx 影子进程。
2. 不修改 `~/.codev-platform/config.json`，不迁移 `data_root`，不手改 `data/`、`.codegraph/` 或索引数据库。
3. 不为兼容 UNC 放宽 `msvcrt.locking`、write-through rename、fencing 或 durability 保证。
4. `post-commit` 保持 never-fail-commit，但失败必须输出有界、脱敏、可行动的阶段和错误码。
5. `health --all` 只表示 HTTP 服务可达性；服务不可达不证明索引损坏。
6. reindex 完成优先以当前 target commit 的 manifest/result 证明；日志只作触发和诊断证据。
7. enqueue 失败、pending、服务停止或单次超时均不得自动触发 full rebuild。
8. `code_vec` 只允许同一严格 fingerprint 的当前候选 checkpoint 条目数确实前进时自动续跑；下降或零进展失败必须终止，避免振荡或无限重放。
9. 股票仓只通过正式 hook / sync / install 命令更新兼容入口，不手改业务代码和 Git 历史。
10. 不删除 `.claude/**`；如需更新兼容副本，必须通过既有同步流程并保留 Codex 真值优先级。
11. 不提交、不推送，除非用户另行明确要求。

## 合并影响面

- CLI / hook：`codev_platform/ops/hooks.py`、`codev_platform/ops/reindex/dispatch.py`。
- 队列路径策略：优先新增窄小的 producer preflight helper；不修改底层耐久协议。
- 健康与等待：`codev_platform/ops/health/__init__.py`、`codev_platform/ops/health/_checks.py`、`codev_platform/ops/reindex/wait.py`。
- 服务 owner 路由：`codev_platform/cli_cmds/mcp.py`、`codev_platform/mcp_serve.py` 及窄小的 Windows/WSL owner helper。
- Skill / 规则真值：`codev_platform/resources/skills/{ai-health,git-commit,update-local-ai}/SKILL.md`、`codev_platform/resources/rules/ai-tools-mcp.md`。
- 本仓 Codex 副本：`.codex/skills/**`、`.codex/rules/ai-tools-mcp.md`，优先通过正式 sync 命令生成。
- 股票仓：正式同步后的 `.codex/**` / hook；不修改业务代码。
- 测试：hook、dispatch、health、wait、资源打包与同步测试。

## 实施步骤

1. 读取股票仓入口规则和当前 hook / skills 状态，确认未知 dirty 文件不与本任务冲突。
2. 为 Windows + WSL UNC + file queue producer 增加失败关闭的 relay-required 判定；正常本地 NTFS、原生 WSL 和 PG 路径保持不变。
3. 将 enqueue 失败拆成 target、queue-open、enqueue 等安全阶段；输出异常类型及数值 errno/winerror，不回显秘密或完整运行路径。
4. 将 health hook-missed 与 wait-for-reindex 改成 manifest-first，并覆盖“本地 enqueue 失败但 webhook 已覆盖 HEAD”的情形。
5. 将 `health --all` 连接失败提示改为 `serve-mcp start/status`，明确禁止用 reindex 启服务。
5. WSL-owned data 下让 `health --all`、`serve-mcp status/start` 自动使用 platform source；Windows `start` 只对确实 DOWN 的 WSL systemd unit 发起受管启动，不生成 18xxx 影子进程。
6. 更新三个 skill 和 MCP 故障规则：Windows 手工恢复统一走 `git hook run post-commit`；任何 enqueue/daemon/pending 诊断不得调用 full rebuild。
7. 在 Chroma 写操作边界精确识别两种已复现 compaction 瞬态并做有界重试；`code_vec` attempt 若仍失败，仅以严格 checkpoint 高水位推进作为队列续跑凭据。
8. 运行 skill frontmatter/结构校验与资源同步 dry-run；再执行目标 pytest。
9. 通过正式同步/安装流程把真值分发到 `C:\workspace\project`，只更新本任务范围内的 Codex 资源和 Git hook。
10. 在股票仓执行正式增量 hook 并以 HTTP manifest 验收；不触发 full reindex。
11. 更新本计划与 README，记录验证证据和剩余风险。

## 验证矩阵

```powershell
python -m pytest tests/test_post_hooks.py
python -m pytest tests/test_reindex_dispatch_stage.py
python -m pytest tests/test_health_hook_missed.py
python -m pytest tests/test_wait_for_reindex_worker.py
python -m pytest tests/test_health_all_auth.py
python -m pytest tests/test_mcp_serve.py tests/test_mcp_cli.py
python -m pytest tests/test_sync_hooks.py tests/test_resources_packaging.py
python C:\workspace\project .codex\skills\ai-health
python C:\workspace\project .codex\skills\git-commit
python C:\workspace\project .codex\skills\update-local-ai
```

必要时扩大到：

```powershell
python -m pytest tests/test_cli_parser.py tests/test_reindex_queue_factory.py
codex mcp list
codev-platform dirty-check --json
```

## 回退

- 实现按“路径门禁 / 诊断 / manifest 判定 / skill 文案 / 分发”独立 seam 保持可逆。
- 回退代码与资源文件即可；不涉及数据格式、队列 schema、索引数据库或用户配置迁移。
- 股票仓同步若异常，保留其现有 `.git/hooks/post-commit` 备份并停止分发，不改业务工作树。

## 独立审计整改（2026-08-01）

用户要求三路独立复核后，架构、测试、运行态审计否决了直接交付。提交/发布前必须追加以下整改：

1. manifest 仍为唯一完成真值；远程 owner 下禁止 legacy log 覆盖 pending/failed，部分 enqueue 日志不得缩窄完整计划。
2. Windows/WSL file queue owner 门禁下沉到 queue factory，覆盖 hook、CLI、onboard、web/webhook 等全部调用方，并统一 backend 归一化语义。
3. WSL UNC owner 解析接受合法尾随分隔符；远程 health 必须验证四个 MCP 端点完整且使用 HTTP 探针，不以任意 TCP listener 假绿。
4. `dirty-check` 删除直接 reindex 建议；仅允许 read/grep 兜底或 commit + hook 增量刷新。
5. Codex/Claude 分发增加显式 surface 兼容契约；禁止把仅适用于 Claude 的资源无条件复制到 `.codex`。
6. Bearer token 不得随 redirect 转发；Windows remote-owner 默认 `health --json-out` 不得写 WSL runtime data。
7. reindex retry 记录使用 epoch 证据，避免跨时区和同秒判断；等待超时需明确区分 19xxx MCP 与 Web 状态面。
8. 修复远程 owner 命令级测试、MCP start/status/wait 失败分支和 post-merge/post-checkout 门禁覆盖。
9. `code_vector_build.py` 恢复到 600 行预算内，不新增白名单。

审计首轮基线：全量 `6154 passed / 634 skipped / 2 failed`；首轮发现的本任务 file-size 回归已修复。最终全量为 `6181 passed / 634 skipped / 2 failed`，剩余两项均为未改文件中的既有基线问题：5 个历史生产文件超过 600 行，以及 runtime wheel fixture 的 `candidate.whl` preflight 失败。

## 发布前审计快照

- 审计收口时状态：源码整改与三路独立审计已完成；当时尚未提交或发布到 WSL immutable runtime。后续是否发布以目标 Git revision、runtime identity 和服务验收回执为准，不回写本段伪造事前证据。
- 根因已闭环：Windows 直写 WSL UNC file queue、Windows 18xxx 影子服务、HTTP 服务与索引完成语义混淆，以及 Chroma compaction 瞬态失败均有独立边界和失败关闭策略。
- 架构落地：data-owner 判定、producer 路由、平台 HTTP client、索引状态 client、远程健康聚合、本地存储探针及 compaction 重试均为窄模块；CLI/health/wait/MCP 只做轻量编排。
- 股票仓恢复：`openclaw-stock` 当前 HEAD `be7498b` 已被 `chroma`、`code_vec`、`codegraph`、`ingest` manifest 覆盖；未执行 full rebuild，未手改索引数据。
- 服务验收：平台全局视图从 `19083` 返回 7 个项目；WSL 的 `19083/19091/19087/19092` 全部正常；Windows `18083/18091/18087/18092` 无影子监听。
- 最终自动化验收：独立测试扩展矩阵 `456 passed / 1 skipped`；架构反例矩阵 `217 passed`；所有变更 Python 均通过 Ruff，`git diff --check` 通过，变更生产文件均不超过 600 行（`_checks.py=593`、`code_vector_build.py=599`）。
- 全量远邻证据：`6181 passed / 634 skipped / 2 failed`；两项失败均可在未改基线上稳定复现，本任务没有新增失败。全仓门禁仍应单独治理这两项既有债务，不把它们伪装成本任务已解决。
- 变更规模：本仓当前 53 个变更路径，包含生产代码、回归测试、规则/skill 真值与工作副本及 roadmap；股票仓仅同步 8 个 Codex/Claude 规则与 skill 文件，业务代码改动为 0。
- 独立运行态审计：两仓、两 surface 共 16 组资源哈希一致；股票 HEAD 四个 lane manifest 覆盖；19xxx 四端点顺序探测正常、18xxx 无监听。并发探测曾出现一次 19083 瞬态 timeout，但 systemd 保持 active、无重启，列为发布后 soak 的 LOW 风险，不允许用 TCP 探针放宽 HTTP 健康语义。

## 审计收口时的发布门禁

- WSL systemd 当前运行的是 `/home/user/project` 的干净已提交版本；本次永久修复仍是 Windows 工作树中的未提交源码。
- 当前 immutable release 仍是 `a34853a5...`，runtime revision 仍是 `1cfde28...`；其中不含本任务新增 owner/HTTP/status 模块和 compaction retry。因此只能声明“源码已修复并通过审计”，不能声明“常驻运行时已部署永久修复”。
- 截至审计收口，按仓库纪律未擅自 commit、push 或热补丁 WSL 副本。要让新的自动恢复逻辑进入常驻服务，必须另行获得授权并执行标准提交与发布流程。

## 发布执行中的职责边界返工

- 用户授权继续后，提交 `2adc681755e44cc17fda68e14d8fb72e1fe09a55` 已仅推送 `origin/dev`，WSL 服务仓同 SHA 且干净。
- 官方 `runtime promote --target-revision` 在切换前 fail-closed；current、previous 与常驻控制服务均保持原状。门禁原因是本次把 Windows 远程 source 客户端逻辑放进了受保护的 `mcp_serve.py`，并对同样受保护的 `web/config.py` 做了无行为收益的常量重构，因而被正确分类为 systemd/runtime contract 漂移。
- 该远程逻辑不生成 unit、不改变服务端点、不参与 WSL server lifecycle。整改采用单一职责：恢复两个受保护文件到 current runtime 的契约版本，把 source endpoint 映射、严格 HTTP 探测、有界等待和受管 unit 启动编排移入独立客户端模块；CLI 只依赖该模块。
- 整改后必须证明：相对 current revision 的受保护路径 diff 为空；原有本地 MCP 行为不变；Windows remote-owner 的 status/start/wait、仅 TCP 假绿、失败非零等回归全部通过。之后只允许重新提交并重试官方 thin promotion，不使用退役 deploy、不手工切 release、不裸调 systemctl。
- 发布边界独立复核追加发现并关闭客户端失败边界：全局 wait 现在把共享 deadline 的剩余预算下传给每个端点，并把单端点的 `/healthz` + `/health` 总预算封顶为 2 秒；畸形 HTTP listener 的 `HTTPException` 被规范为 DOWN，不再让 status/start-wait 崩溃。最终相关矩阵 `375 passed / 1 skipped`，Ruff 与 `git diff --check` 通过，受保护 runtime contract pathspec 相对 `1cfde28` 的净差异为 0。

## 发布后 code_vec 验收缺口

- 新 release `a66a206c...` 已切换成功，四个 MCP 严格 HTTP 端点均为 OK，`chroma/codegraph/ingest` 均已对齐 `90a09cb`。
- `code_vec` 复跑暴露了一个与 daemon 启动无关的存储恢复缺口：旧 runtime 在 current build 的增量 upsert 中遇到 Chroma `Error purging logs`，已写入的分批 checkpoint manifest 被下次任务当成稳定基线；独立完整性证明最终正确拒绝了部分落盘的 collection。
- 最小治理边界限定在 `recall/code_vec` 写入编排：只允许未发布 side-build 写分批续跑 checkpoint；current 增量构建在最终证明前不覆盖成功 manifest。增量写或最终完整性失败时，最多一次转入隔离 full side-build，成功后才原子切换 current；side-build 自身失败仍保持原有失败关闭和严格 checkpoint 续跑语义。
- 不修改 Chroma schema、队列协议、MCP 路由、数据目录或用户配置；不手工删除/修改任何索引文件。
- 新增回归必须证明：增量分批不发布 checkpoint；增量 upsert 失败撤销 current 成功标记；损坏的未发布 side-build 在同一任务关闭、隔离并换 UUID 干净续建；只有证明成功才 publish。

## 发布后缺口的最终审计收口

- current 增量在首个存储写之前撤销 manifest，分批写不再发布 checkpoint；最终严格执行 `close writer → 独立 proof → meta → manifest`，full side-build 再原子切 pointer。
- 查询侧在实际 collection 查询前后采样 manifest 身份与 meta 代次；client 打开、collection 获取或查询异常均 fail-soft，代次变化（包括同内容原子重发 ABA）时丢弃在途结果。
- 未发布候选的排序、健康校验与 fingerprint 进度汇总已下沉到独立模块，finder 与 runner 共享同一真值；SQLite 缺失不再被视为健康 checkpoint。
- full build ID 改为 UUID；丢弃或从 current 切换 side-build 前必须先关闭旧 writer，避免 Windows SQLite 句柄阻止清理。
- runner 只把严格增长或 current manifest 的一次 `存在→缺失` 边沿交回队列；checkpoint 下降保留原失败码，拒绝下降/增长振荡重试。
- `/platform/status` 使用 co-located `local` profile 的严格 HTTP 探针；当前 platform-docs 请求作为 self 可达证明，其余三端点实际探测，既不 TCP 假绿，也不在单 worker 内同步自探死等。
- Git 路径计划对 commit、wait、post-merge 与 post-checkout 统一保留 rename 旧/新两端；non-ff 使用第一父范围，最近一次 fast-forward merge/pull 由 reflog + `ORIG_HEAD..HEAD` 覆盖完整集成范围。
- 第三轮三路只读审计已分别确认并发发布、checkpoint 重试、hook/HTTP 健康边界无剩余 HIGH/MEDIUM；最终交叉矩阵为 `1738 passed / 88 skipped`，Ruff、compileall 与 `git diff --check` 通过。
- `code_vector_build.py` 为 561 行；受保护的 `mcp_serve.py`、`web/config.py` 与 `cli_cmds/mcp.py` 相对已发布契约无工作树差异。全仓 file-size 门禁仍只报告 5 个未改历史文件，未新增 offender。

## 薄发布 install-only 锁自阻塞整改

- `65ad190` 的官方 thin promotion 已成功构建并发布目标 release 服务访问权限，但激活后的 install-only 事务触发补偿回滚；旧 release 与全部常驻服务保持健康。
- journal 证明根因是 install-only 在完整 `maintenance_systemd_transition_lock` 内执行长时间载荷复验：运行中的 reindex worker 申请共享写许可时被 intent/gate 排他锁阻塞并超时退出，进而破坏 install-only 的“进程活动态不变”契约。该冲突是确定性的锁职责重叠，不是索引损坏或目标 wheel 构建失败。
- 最小修复边界限定在 install-only 的生产锁适配：保留全局 systemd transition session 以串行化管理员转换，并在同一会话内前后复验 runtime mode；不再获取会阻断 worker 数据写许可的 intent/gate 排他锁。normal install、maintenance-stage、guarded-stage 继续使用原有完整转换锁。
- 新增回归必须证明：install-only 持有管理员会话但不进入完整 gate 锁；事务体异常与会话退出异常保持原语义；前后 runtime mode 漂移仍失败关闭；promotion 外层会话可重入且 deployment lock 仍覆盖切换、重启、验收与回滚。
- 独立事务审计发现并关闭一处复用泄漏：`guarded-stage` 原先继承 install-only ports，现已显式绑定回完整 maintenance-stage transition/gate 锁；normal、maintenance-stage 与 guarded-stage 均未放宽，只有正式 install-only 使用 session-only。
- 本地专项与扩大矩阵、三路独立审计及真实 Linux 临时锁探针均无剩余 HIGH/MEDIUM；全量结果为 `6210 passed / 634 skipped / 2 failed`。两项失败仍是未改基线中的 5 个历史超 600 行文件和旧 runtime wheel fixture，本次未新增失败；Ruff 与 `git diff --check` 通过。
- 该修复命中受保护的 `mcp_systemd_*.py` 契约，普通 `runtime promote --target-revision` 必须继续 fail-closed。受控 bootstrap 固定为：旧 current 只执行官方 `runtime build --source-user` 与 `runtime stage`；复验新 release 身份、依赖零漂移和批准的单一契约差异后，必须从新 release 自身的 `venv/bin/python -B -I` 执行 `runtime promote --target-release <new> --rollback-anchor <current>`。不得从 current alias 运行目标切换，也不得提前单独执行 staged-access 写入。
- bootstrap 成功后证明 current/previous、runtime revision、四个 MCP 严格 HTTP 端点、全部受管 systemd 服务、reindex worker 稳定性及目标 commit manifests。禁止手工切 symlink、裸调 systemctl、full rebuild 或直接修改索引文件。

## 多租户 platform-docs 冷启动 readiness 收口

- `1744940` bootstrap promotion 已成功，runtime/systemd/ingress 验收均通过；发布后严格探针发现 19083 持续返回 `503 starting`，而鉴权 `/platform/status` 已能完整聚合 7 个项目。journal 证明启动器在 `PROJECT_ID=None` 的合法多租户模式下仍调用 `_ensure_project(None)`，模型虽已预热，却因不存在默认 collection 永远无法满足“至少一个 collection 已加载”的 readiness 条件。
- 修复必须区分“无默认租户、按请求 lazy-load”的健康空态与真实未就绪：无默认项目时预热模型而不构造空 project，模型就绪即允许公共 health 200；若配置了默认项目，则仍必须证明其 collection 已加载。模型未就绪始终 503，单租户/默认项目失败不得被多租户规则掩盖。
- readiness 与默认租户预热判定下沉为无 I/O 的窄策略模块；Chroma server 只注入模型/项目加载端口并渲染状态，保持文件预算与单一职责。新增纯策略、启动编排和 HTTP 状态回归。
- 验证后只允许普通 thin promotion（该变更不触及依赖或受保护 systemd/runtime path），随后复核 19083 `/healthz` 无需首个业务查询即可 200、四端点严格 HTTP 全绿、`health --all` 可用、18xxx 无影子监听以及目标 commit manifests；不得靠手工 search、TCP 探针或忽略 503 伪造健康。
- 独立审计追加的发布阻断项必须同批关闭：默认项目就绪只认可该项目自己的 collection，详细健康页驱逐 stale collection 后须在同一响应重算 readiness；`/platform/status` 的 platform-docs 行由同一份本地 readiness 显式注入，不得因 self-probe 跳过而无条件假定 OK。
- 薄发布 MCP 验收适配器必须改用四端点同轮、严格 HTTP 的 source client，`/healthz` 与兼容 `/health` 均非 200 时不得回退 TCP。首次从旧 controller 发布本修复时，切换后额外执行同一严格 HTTP 门禁；失败必须通过官方反向 promotion 回滚，不手工切指针或裸调 systemctl。
- 严格 waiter 每轮状态必须隔离；若共享 deadline 在一轮中途耗尽，该 partial round 整体不得与上轮缓存结果拼接成成功证明，未完成全端点同轮探测时必须失败关闭。
- 回归覆盖纯策略、默认失败但其它租户成功、stale 驱逐、真实 ASGI 状态码、聚合 self 行和仅 TCP listener 反例；最终由三路只读审计复核无 HIGH/MEDIUM 后方可提交发布。
- 无默认租户时，MCP/SSE 请求必须显式携带合法 `project_id`；fallback 后仍为空须在进入 `_ensure_project` 前对称返回 400，禁止把 `None` 缓存进项目状态并连带破坏详细健康面。

### Readiness 最终审计证据

- 五个阻断边界均已关闭：默认租户只认可自身 collection；stale 驱逐后同一详情响应降为 503；无默认租户缺 `project_id` 的 MCP GET/POST/DELETE 与 SSE 均在状态机前返回 400；平台聚合 self 行使用同源 readiness；薄发布只接受四端点同轮严格 HTTP 200。
- strict source waiter 使用逐轮隔离状态，最终 partial round 未探端点明确落为 timeout；空、缺失、额外、重复或非 OK 的服务集合均由 promotion adapter 失败关闭，不再回退 TCP 或跨轮拼绿。
- 三路独立只读复审均无剩余 HIGH/MEDIUM。复审矩阵包括 `372 passed / 1 skipped`、全部 MCP `431 passed`、runtime contract `638 passed / 10 skipped`，以及最终 waiter 反例 `25 passed`；本地交叉矩阵为 `285 passed / 3 skipped`。
- 全仓回归为 `6227 passed / 634 skipped / 2 failed`。两项失败仍是未改基线：5 个历史生产文件超过 600 行、旧 runtime wheel fixture 的 `candidate.whl` preflight；本次 `server.py` 为 600 行且未新增 offender。Ruff、compileall、`git diff --check` 均通过。
- 首次发布仍由 `1744940` 旧 controller 发起，故 promotion 返回后必须在任何 search/MCP 查询之前额外执行四个 19xxx `/healthz` 严格探针；任一非 200 均通过官方反向 promotion 回滚。
