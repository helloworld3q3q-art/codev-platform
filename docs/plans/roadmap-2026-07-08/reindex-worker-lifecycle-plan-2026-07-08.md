# Plan — reindex worker 短驻化与队列可观测(2026-07-08)

> **状态**:🟢 P1-P6 已落地;WSL systemd 服务固化已审计
>
> **主题**:修复"入队成功但无人消费"导致 MCP/索引落后的链路缺口。

## 一、定位

本计划只解决 reindex 队列消费链路的生命周期和可观测性,不重写索引算法。

目标状态:

- 本地提交后,`post-commit` 只负责入队,随后幂等唤醒一个短驻 `codev-reindex worker`。
- worker 启动后先 drain 当前队列,有新任务则继续消费;队列持续空闲超过配置时间后自动退出。
- `reindex-queue status` 明确报告 worker 是否运行、heartbeat、最近处理任务、最老待办 age 和 STALE。
- 开发/运维有正式 `drain-once` 入口,用于"跑完当前队列后退出"。

非目标:

- 不先做并发 worker。
- 不把所有机器统一改成常驻后台进程。
- 不绕过队列直接前台 reindex 作为常态方案。

## 二、决策前提

### 当前现象

- `reindex-queue status` 有 8 个待办;`openclaw-stock` 4 个已 STALE,`codev-platform` 4 个等待。
- 进程列表没有 `reindex-queue worker` / `codev-reindex worker`。
- `worker.log` 最后一次真正消费停在 2026-07-08 01:22;10:48 提交后没有 worker 日志。
- FileSpoolQueue 后端的待办就是 `data/reindex_queue/*` marker 文件。
- 本仓索引停在 `5e2dfcc2`,未追到 `08c57c8`。

结论:当前主因不是索引慢,而是消费者缺席。

### 代码核实

- `codev_platform/ops/reindex/dispatch.py` 已把命中文件按 scope 展开并 `q.enqueue(...)`;默认 `foreground=False` 时不会启动 worker,只打印"worker 串行消费"。
- `codev_platform/reindex/worker.py` 的 `drain_once()` 已是可复用的"跑完当前 pending"原语;`run_forever()` 目前是常驻模型,没有 idle exit。
- `codev_platform/ops/reindex_queue.py` 的 CLI 只有 `enqueue/status/worker/prune-stale`;缺正式 `drain-once` 和 worker health。
- `codev_platform/mcp_serve.py` 已有 Windows hidden detached spawn 模式,可复用后台进程启动经验;但 MCP 端点有端口可探活,worker 无端口,需要 pid/heartbeat 文件。
- `codev_platform/mcp_systemd.py` 已渲染 `codev-reindex.service`;Linux/服务器已有常驻路线,本轮重点补 Windows/本机短驻路线。
- `PgJobQueue` 已有 lease/release/reclaim 语义;FileSpoolQueue 无 running lease,所以本机短驻 worker 要保证单实例幂等启动。

### 计划修正

- pid/heartbeat 不能单独承担并发控制;post-commit 连续触发时还需要短期 start lock,用原子创建防止两个进程同时拉起 worker。
- `drain-once` 和 `post-commit --foreground` 也必须拿同一把 run lock;后台 worker 已运行时不能再前台并发 drain。
- worker 状态建议先收敛到一个 JSON state 文件,包含 pid、heartbeat、mode、last_job 和 exit_reason;status 只读这个 state,不反向认领队列。
- heartbeat 必须由后台线程持续刷新,不能只靠 async loop;runner 是同步阻塞子进程,长任务期间 event loop 不会跳。
- `worker` 默认仍保持常驻语义;短驻只通过 `--idle-exit-sec` 打开,避免破坏 systemd `codev-reindex.service`。
- auto-start 先只在本机 FileSpoolQueue 默认打开;PG/服务器环境继续以 systemd 常驻为主,需要短驻时再显式配置。

### 资源前提

本机 CPU 不是瓶颈,但内存/GPU 余量偏紧。默认策略应低资源友好:

- 本机默认串行、短驻、低并发。
- 服务器/部署环境可继续 systemd 常驻,后续再配置化调整 batch/concurrency。

## 三、Phase 划分

| Phase | 内容 | Estimate | Gate |
|---|---|---:|---|
| P0 现场止血 | 手动启动一次 `python -m codev_platform.cli reindex-queue worker` 或临时 `drain_once()` 消费现有队列 | 0.5h | 当前 pending 归零;本仓 manifest 覆盖 `08c57c8`;不手删 marker |
| P1 短驻 worker CLI | 给 `reindex-queue worker` 增加 `--idle-exit-sec`/`--heartbeat-sec`;新增 `reindex-queue drain-once` 正式入口 | 0.5d | 单测覆盖 idle 退出、drain-once 返回 processed 数;默认旧 `worker` 常驻行为不破坏 |
| P2 worker supervisor | 新增幂等 `ensure_worker_running()` 内部能力:start lock、worker state JSON、stale pid 回收、Windows hidden spawn | 1d | 连续两次唤醒只产生一个 worker;worker 异常退出后下次提交可重启;不弹可见窗口 |
| P3 post-commit 唤醒 | `_dispatch_reindex()` 入队后在非 foreground 模式下调用 supervisor 唤醒短驻 worker;失败 fail-soft,不影响 commit | 0.5d | commit 后队列自动消费;worker 日志出现本次 trigger;hook 失败仍不阻塞 commit |
| P4 status 可观测 | `reindex-queue status` 增加 backend、worker running、pid、heartbeat age、last processed、oldest age、STALE 摘要 | 0.5d | worker 未运行且有 pending 时明确提示"worker not running";队列空时也能显示 worker 状态 |
| P5 serve-mcp/health 汇总 | `serve-mcp status` 或 `health` 汇总 reindex worker 状态,让 MCP 端点与索引消费链路同屏可诊断 | 0.5d | `serve-mcp status`/`health` 能指出 worker 缺席导致索引滞后 |
| P6 配置化与文档 | 配置默认 idle TTL、heartbeat TTL、是否 auto-start;更新 onboarding/QUICKSTART 排错 | 0.5d | 低资源本机默认短驻;服务器文档仍推荐 systemd 常驻 |

推荐执行顺序:P0 → P1 → P2 → P3 → P4。P5/P6 可随实现收尾。

## 四、风险

- **重复 worker**:多个提交同时触发时,必须用 pid 文件 + 原子创建/更新规避多实例。FileSpoolQueue 没有 running lease,重复 worker 会破坏单写者假设。
- **误判 stale pid**:Windows 进程探活不能只看 pid 文件时间;至少要校验 pid 是否存在,并用 heartbeat age 判定。
- **commit hook fail-soft**:唤醒 worker 失败只能记日志/提示,不能让 git commit 失败。
- **PG 多机语义**:PG 后端仍应按 `config.projects` fail-closed。短驻 auto-start 默认面向本机 FileSpoolQueue;PG/服务器可 opt-in 或继续 systemd。
- **资源占用**:idle TTL 不能太长;默认建议 5-10 分钟,避免显存/内存紧张时长期占后台。
- **旧常驻路线兼容**:已有 systemd `codev-reindex.service` 不能被短驻逻辑破坏;若检测到常驻 worker 正在跑,post-commit 只入队不重复拉起。

## 五、替代方案

| 方案 | 结论 | 原因 |
|---|---|---|
| 直接长期常驻 Windows worker | 不作为默认 | 能解决消费,但和既定"提交唤醒、空闲退出"设计不一致,也不适合低资源本机 |
| 每次 commit 前台 `drain_once()` | 不采用 | 会阻塞 commit,大仓可能几分钟;也破坏 hook 快速返回 |
| 只让用户手动跑 `reindex-queue worker` | 仅作 P0 止血 | 人工容易忘,当前事故已经证明该模式不可依赖 |
| 绕过队列直接 `reindex --repo ...` | 仅作临时调试 | 队列 marker 仍在,后续仍要处理,还会绕过合并/dirty 重入语义 |
| 并发 worker | 暂缓 | Chroma / SQLite / codegraph 写侧单写者约束未解除,盲目并发会制造锁冲突和陈旧嵌入 |

## 六、启动条件

- 先确认当前队列已被 P0 消费或明确保留为测试样本。
- 保持 `data/`、`.codegraph/` 和用户级配置不手改;运行态只通过 CLI/worker 自身生成。
- 测试审计已补齐:
  - `reindex-queue drain-once` parser/CLI 与 run lock 拒绝。
  - worker idle exit 与短驻 CLI 生命周期。
  - supervisor 双唤醒幂等、start lock、dead pid run lock 恢复。
  - status 在"有 pending + worker 不运行"时给出明确提示,且 worker running 时不误报 FileSpool STALE。
  - post-commit 唤醒失败 fail-soft,PG-like queue 默认不 auto-start。
  - `health` / `serve-mcp status` 输出 reindex worker 摘要。
- 最小验证命令:

```powershell
python -m pytest tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_reindex_worker_affinity.py tests/test_cli_parser.py
python -m pytest tests/test_wait_for_reindex_worker.py
codev-platform reindex-queue status
```

实现涉及 `serve-mcp status` 或 `health` 时追加:

```powershell
python -m pytest tests/test_mcp_serve.py tests/test_serve_mcp_diagnose.py
```

本次落地验证:

- reindex/post-hook 目标集: `93 passed`。
- MCP serve 相关: `43 passed`。
- `health --all` token-mode 修正:支持从 `platform.token_env` / `PLATFORM_TOKEN` / `CODEV_PLATFORM_MCP_TOKEN` 读取 Bearer token 访问 `/platform/status`;首个 token 401 时继续尝试下一个候选,失败提示只输出环境变量名,不输出 token 值。
- 测试审计兄弟已二次审查该修正;补齐无 token 时不发送 Authorization、首个 token 401 后继续尝试下一个候选 token 的用例。目标回归: `python -m pytest tests/test_health_all_auth.py tests/test_health_split_security.py tests/test_health_reindex_worker.py` → `11 passed`;`python -m pytest tests/test_cli_parser.py` → `9 passed`;`config.example.json` JSON 解析通过。
- WSL 实调:`/healthz` 可达,`/platform/status` 仍 401;诊断确认交互 shell 未导出 `PLATFORM_TOKEN` / `CODEV_PLATFORM_MCP_TOKEN`,后续需单独处理 WSL token 环境注入。
- `webhook extra repos` 诊断收敛:health/webhook startup 只扫描本机配置中带 `webhook_repo`、实际会被 webhook 入口命中的项目,并对 config/meta 重复声明的同一路径或同一 child project 去重。测试审计兄弟二次审查后补齐 OMS-like、enabled 对照和 health 直接断言;Windows/WSL 运行态诊断均为 0 条,`health --mode light` 均为 READY/all green。目标回归: `python -m pytest tests/test_graph_ingest.py tests/test_webhook_body_limit.py tests/test_health_webhook_mapping.py tests/test_health_reindex_worker.py tests/test_cli_parser.py` → `53 passed`。
- `git diff --check` 无 whitespace error,仅 `tests/test_webhook_body_limit.py` CRLF 提示。
- `hook missed?` 诊断改为复用 reindex scope 公共推导。第一轮经测试审计指出 codegraph/ingest/code_vec runner 存在 fail-soft rc=0 假绿风险,先收窄为 `chroma` only;本轮补强后扩展为 `chroma/codegraph/ingest/code_vec` 全量 expected kind 覆盖:`cmd_reindex` 成功路径输出 `proof: codegraph ok` / `proof: ingest ok` / `proof: code_vec ok`,runner 只在 proof marker 完整且无 skip/fail-soft warning 时返回成功,`code_vec` 同时要求 codegraph proof 与 code_vec proof,`CodegraphReindexRunner` 前置 ensure-link error 直接失败。测试审计兄弟终审无阻塞。实测首次 `wait-for-reindex` 返回 manifest `status=failed` 才暴露诊断缺口:队列模式下 reindex.log 只证明已入队,不能证明完成;同一 commit 多段日志必须取最新 queued block;`wait-for-reindex` 对 manifest failed / legacy finished failed 不应返回 0。已修正为:当前 HEAD 的最新 queued block 必须有 manifest 全部 success 才 `health` OK,manifest failed 或 legacy failed 时 `wait-for-reindex` 返回 1;`wait-for-reindex` 自身也反向取最新 trigger block,避免同一 commit 重跑时旧 OK/旧 failed 干扰结果。Windows 首次 code_vec 失败根因为 platform-docs daemon 未启动(remote `/embed` 连接拒绝);`serve-mcp start --wait` 后重跑 `code_vec` 成功。目标回归: `python -m pytest tests/test_reindex_runner_timeout.py tests/test_reindex_worker_affinity.py tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_health_reindex_worker.py tests/test_wait_for_reindex_worker.py tests/test_health_hook_missed.py tests/test_post_hooks.py tests/test_index_manifest.py tests/test_cli_parser.py tests/test_reindex_ingest_stage.py tests/test_codegraph_ensure_link.py` → `138 passed`;`python -m ruff check codev_platform/reindex/runners.py codev_platform/ops/reindex/commands.py codev_platform/ops/health/_checks.py codev_platform/index_manifest.py tests/test_reindex_runner_timeout.py tests/test_health_hook_missed.py tests/test_reindex_ingest_stage.py tests/test_codegraph_ensure_link.py tests/test_wait_for_reindex_worker.py` → passed;Windows `wait-for-reindex` OK,`health --mode light` READY/all green。
- `code_vec` remote `/embed` 依赖可靠化:新增 `mcp_runtime` 隔离隐藏后台启动副作用,`mcp_serve.ensure_serving` 复用该 helper 并保持端点枚举/探活职责;新增 `agent.embed.remote_ready` 作为 code_vec 写侧 guard,仅默认 `remote` 且 `memory.embed.url` 指向本机 platform-docs 时确保 platform-docs 单端点,外部 URL / `qwen-local` / `ensure_remote_daemon=false` 不启动本地 daemon;未知 `recall.code_vec.embed_backend` 返回 None 并 warn,不静默落 remote。测试审计兄弟复核无阻塞,非阻塞建议(未知 backend、qwen-local 不触发 guard、spawn fail/skip、旧注释)均已修正。post-commit 实测暴露 `reindex.supervisor` 仍从 `mcp_serve` 导入已迁出的 `_spawn_detached`,导致 worker auto-start fail-soft;已改为复用 `mcp_runtime.spawn_detached` 并新增 supervisor 回归。目标回归: `python -m pytest tests/test_agent_embed_registry.py tests/test_mcp_serve.py tests/test_reindex_ingest_stage.py tests/test_reindex_runner_timeout.py tests/test_reindex_worker_affinity.py tests/test_reindex_worker_supervisor.py tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_wait_for_reindex_worker.py tests/test_code_vec_chunking.py tests/test_recall_vector_lane.py tests/test_embed_batch.py tests/test_cli_parser.py` → `210 passed / 5 skipped`;`python -m ruff check ...` → passed;Windows 实测 daemon 停止状态下 `python -m codev_platform.cli reindex --code-vec` 自动拉起 platform-docs,`serve-mcp status` 显示只启动 platform-docs。
- `reindex worker` runner 失败可观测性增强: `CliReindexRunner` 将子进程 stdout/stderr 覆盖写入 `data/logs/reindex-runner/<project>__<kind>.log`;失败/超时时 tail 生成 `last_note`,worker 写入 index manifest note 并在 worker.log 失败行输出压缩 detail。测试审计兄弟复审后补齐成功输出保留和测试 `PLATFORM_DATA_DIR` 隔离。目标回归: `python -m pytest tests/test_reindex_runner_timeout.py tests/test_reindex_worker_affinity.py tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_health_reindex_worker.py tests/test_wait_for_reindex_worker.py` → `52 passed`;`python -m ruff check codev_platform/reindex/runners.py codev_platform/reindex/worker.py tests/test_reindex_runner_timeout.py tests/test_reindex_worker_affinity.py` → passed。
- `runner log` 脱敏与大小上限:新增 `codev_platform/reindex/runner_logs.py`,把子进程 stdout/stderr 读入 bounded head/tail buffer 后再落盘,默认 512KiB;常见敏感形态(Authorization、token/password/secret/api key/DSN/env key、JSON quoted value、URL credentials、`sk-*`)写入前统一替换为 `<redacted>`。`CliReindexRunner` 只委托日志 helper,继续从落盘后的脱敏日志取 tail/sample 做 `last_note` 和 proof/fail-soft 判定。timeout 路径改为 Windows `taskkill /T` / Unix process group kill,避免后代进程持 stdout 导致 reader 卡住。测试审计兄弟三轮复核中发现并修正:分片长 secret 后缀泄漏、quoted secret 后缀泄漏、超长 quoted line 前缀泄漏、timeout 后代进程残留、UTF-8 head/tail 切分。目标回归: `python -m pytest tests/test_reindex_runner_logs.py tests/test_reindex_runner_timeout.py tests/test_reindex_worker_affinity.py tests/test_reindex_queue.py tests/test_reindex_queue_cli.py tests/test_health_reindex_worker.py tests/test_wait_for_reindex_worker.py tests/test_post_hooks.py tests/test_index_manifest.py tests/test_cli_parser.py` → `92 passed`;`python -m ruff check codev_platform/reindex/runners.py codev_platform/reindex/runner_logs.py tests/test_reindex_runner_logs.py tests/test_reindex_runner_timeout.py` → passed。取舍:超长无换行物理行触发 oversized marker 后丢弃该行直到换行,防泄密优先;若工具把 proof marker 拼到同一坏格式行,runner 会 fail-safe 判失败。
- `reindex-queue status` 显示队列空,短驻 worker 已 idle 退出。

## 七、WSL 服务固化审计

WSL 服务固化本步不新增 nohup/supervisor,因为 WSL 已接入仓内正式 systemd 路线:

- 仓内真值源:`codev_platform/mcp_systemd.py` 的 `render_reindex_unit()` 渲染 `codev-reindex.service`,语义是开机自起 + 崩溃重启 + 常驻 worker。
- WSL 实际单元:`/etc/systemd/system/codev-reindex.service`,`User=helloworld`,`WorkingDirectory=/home/helloworld`,`ExecStart=/home/helloworld/work/codev-platform/.venv/bin/python -m codev_platform.cli reindex-queue worker`,`Restart=always`,`RestartSec=3`。
- 安装状态:`systemctl is-enabled codev-reindex.service` 返回 `enabled`;`systemctl show ...` 返回 `ActiveState=active`,`SubState=running`,`UnitFileState=enabled`,`Restart=always`,`RestartUSec=3s`。
- 运行状态:进程列表只看到一个由 systemd 托管的 `reindex-queue worker`;未发现额外 nohup/手动 worker 残留。
- 生成物关系:`~/codev-systemd/` 保留 `codev-reindex.service` 与 `install.sh` 等生成产物,作为重装来源;`/etc/systemd/system/` 是实际安装目录,当前只安装正式 codev units。
- 链路验证:`reindex-queue status` 显示 `worker running: yes`,队列后端 `FileSpoolQueue`,队列空;`health --mode light` 显示 READY,`reindex worker` OK,`hook missed?` 已由 manifest 覆盖 HEAD `0f6d61c` 的 `chroma/codegraph/ingest/code_vec`。

结论:WSL reindex worker 已完成服务固化,下一步不应重复铺 nohup 或另起 supervisor。后续只需在需要重装 unit 时重新生成 `~/codev-systemd/install.sh` 并由 sudo 安装。

剩余风险:WSL token 环境注入仍未规范化,`health` 对 platform-docs 详情面仍只能提示 `/platform/health needs auth in token mode`;`/platform/status` token-mode 详情访问留到下一步单独处理。

## 八、WSL / 服务器 token 环境注入

第一步已完成仓内能力,不落明文 token:

- systemd 渲染新增可选 `systemd.env_file`。配置后 MCP 端点、reindex、webhook、agent、memory maintenance 等需要运行环境变量的 service unit 包含 `EnvironmentFile=-<path>`;clock resync oneshot 不需要 token 注入。未配置时 unit 内容保持原样。
- `EnvironmentFile` 使用 `-` 前缀,文件缺失不阻塞 service 启动,方便先发布能力再逐机落地。
- `config.example.json` 只声明路径口径和变量名,不存 token 值。WSL 单机建议 `/home/<user>/.config/codev-platform/platform.env`;服务器建议 `/etc/codev-platform/platform.env`。
- env 文件应为机器本地机密文件,不进 git;建议权限 `0600`,内容可放 `PLATFORM_TOKEN` / `CODEV_PLATFORM_MCP_TOKEN` / `CODEV_PLATFORM_MEMORY_DSN` 等运行环境变量。
- 服务器部署时继续通过用户级 `~/.codev-platform/config.json` 设置 `systemd.env_file` 和 `platform.token_env`,不把服务器路径或 token 写死到代码。

验证:

- TDD 红灯:`test_reindex_unit_uses_configured_environment_file` 在实现前失败,因为 unit 未渲染 `EnvironmentFile`。
- 目标回归:`python -m pytest tests/test_mcp_serve.py tests/test_systemd_restart.py tests/test_systemd_agent_clock.py tests/test_health_all_auth.py tests/test_health_split_security.py -q` → `54 passed / 1 warning`。
- 静态检查:`python -m ruff check codev_platform/mcp_systemd.py codev_platform/core/config.py tests/test_systemd_restart.py` → passed。
- `config.example.json` JSON 解析通过。

第二步已完成 WSL 本机落地:

- WSL 使用 `/home/helloworld/.config/codev-platform/platform.env` 作为机器私有 env 文件;目录权限 `0700`,env 文件权限 `0600`。
- `~/.codev-platform/config.json` 设置 `platform.token_env=CODEV_PLATFORM_MCP_TOKEN` 和 `systemd.env_file=/home/helloworld/.config/codev-platform/platform.env`;当前 config 与历史 `config.json.bak*` 权限均为 `0600`。
- 重新生成并安装 systemd unit 后,MCP 端点、reindex、webhook、agent 等 7 个常驻服务均 `active` + `enabled`,并确认运行进程环境中存在 `CODEV_PLATFORM_MCP_TOKEN`;memory maintenance timer 继续由 timer 触发。
- `health --all` 客户端新增 env-file 兜底:仍优先读取真实进程环境变量;若未设置,再从 `systemd.env_file` 解析 `platform.token_env` / `PLATFORM_TOKEN` / `CODEV_PLATFORM_MCP_TOKEN` 对应值。这样交互式 CLI 不必把 token 放进 shell profile,服务器也可复用 `/etc/codev-platform/platform.env`。
- env 文件解析只支持 `KEY=value`、注释、可选 `export ` 前缀和简单单双引号,不执行 shell、不展开变量、不输出 token 值。

验证:

- TDD 红灯:`test_health_all_reads_token_from_configured_systemd_env_file` 在实现前失败,因为请求未带 Authorization;审计后补齐“任一进程 env 优先于 env-file”和“env-file shell-like 值按字面处理”两个回归。
- 目标回归:`python -m pytest tests/test_health_all_auth.py tests/test_health_split_security.py -q` → `13 passed / 1 warning`;扩大回归 `tests/test_mcp_serve.py tests/test_systemd_restart.py tests/test_systemd_agent_clock.py tests/test_health_all_auth.py tests/test_health_split_security.py` → `57 passed / 1 warning`。
- 静态检查:`python -m ruff check codev_platform/ops/health/__init__.py tests/test_health_all_auth.py` → passed。

实调验收:

- Windows commit `59e11de` 已 push;本机 `wait-for-reindex` 通过,`health --mode light` READY。
- WSL 仓库自动拉到 `59e11de`,systemd `codev-reindex.service` 完成 `codev-platform` 的 `chroma/codegraph/ingest/code_vec`,队列空,`health --mode light` READY。
- 在 WSL 命令环境显式移除 `PLATFORM_TOKEN` 和 `CODEV_PLATFORM_MCP_TOKEN` 后运行 `health --all` 仍成功,证明交互 CLI 可从 `systemd.env_file` 读取 Bearer token,无需写入 shell profile。
- WSL 配置确认:`platform.token_env=CODEV_PLATFORM_MCP_TOKEN`,`systemd.env_file=/home/helloworld/.config/codev-platform/platform.env`;env 文件存在且权限 `0600`;安装后的 systemd unit 中共有 8 处 `EnvironmentFile=-/home/helloworld/.config/codev-platform/platform.env`。

## 九、runner log 真实故障演练

目标:在真实 `data/logs/reindex-runner/*.log` 和 `index_manifest.sqlite` 上验证失败可观测性,不依赖单测假对象,同时不污染真实 `chroma/codegraph/ingest/code_vec` 成功记录。

演练方式:

- 尝试一:临时注册 `drill_fail` runner 并传入无效 reindex 参数。结果 CLI argparse 返回 rc=2;worker 约定 rc=2 表示锁占用/db busy retry,因此不会写 failed manifest,只会保留重试语义。该方式不适合作为"失败终态"演练样本,但已确认 rc=2 边界。
- 尝试二:创建临时非 git/非项目 repo,以 `project_id=runner-log-drill`、`kind=chroma` 经真实 `ReindexWorker` 调 `CliReindexRunner` 执行 `reindex --chroma --repo <temp>`。子进程因无法解析 project_id 返回 rc=1;worker 写入 failed manifest note 并 complete 该 job。该方式只生成 `runner-log-drill__chroma.log` 和 `runner-log-drill/chroma` manifest,不写真实项目索引产物。共享 data root 中会留下合成 drill 记录,服务器演练应使用明确 drill 前缀或独立 `PLATFORM_DATA_DIR`。

Windows 结果:

- `data/logs/reindex-runner/*.log` 共 13 个;默认上限 `DEFAULT_RUNNER_LOG_MAX_BYTES=524288`;`oversized=[]`。
- 用同一 `redact_runner_output()` 对真实日志复扫,`unredacted_sensitive_shapes=[]`;测试审计兄弟额外扫 JWT/GitHub PAT/AWS/Google/Slack/裸长高熵串,结果 0 命中。
- 演练日志 `runner-log-drill__chroma.log` 大小 `696` bytes,含 `FAIL: chroma reindex exit=1`。
- manifest `runner-log-drill/chroma` 为 `status=failed`,note 长度 `696`,能直接看到 `chroma rc=1`、project_id 解析失败和 `FAIL: chroma reindex exit=1`。
- `reindex-queue status` 队列空,短驻 worker 已 idle 退出。

WSL / 服务器形态结果:

- `data/logs/reindex-runner/*.log` 共 8 个;`oversized=[]`;复扫 `unredacted_sensitive_shapes=[]`。
- `codev-platform__code_vec.log` 大小 `481` bytes,含 `proof: code_vec ok`。
- 同期真实 `openclaw-stock__chroma.log` 大小 `2464` bytes,含 `FAIL:`;worker.log 显示 Chroma `disk I/O error`。该问题属于 openclaw-stock 的独立运行态风险,不阻塞本仓 runner log/token 验收,但应在后续 roadmap 收尾审计中拆出单独排查项。

结论:

- runner log 的大小上限、脱敏、失败 note 和 proof marker 在 Windows/WSL 两种运行形态均可验证。
- rc=2 retry 不是失败终态,后续运维演练若要验证 manifest failed,应选择 rc=1/124 等终态失败。
- 真实演练验证了当前文件未超限;超大输出截断机制仍主要由 `tests/test_reindex_runner_logs.py` 覆盖。
