# Plan — reindex worker 短驻化与队列可观测(2026-07-08)

> **状态**:🟢 P1-P6 已落地
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
- `git diff --check` 无 whitespace error,仅 `core/config.py` CRLF 提示。
- `reindex-queue status` 显示队列空,短驻 worker 已 idle 退出。
