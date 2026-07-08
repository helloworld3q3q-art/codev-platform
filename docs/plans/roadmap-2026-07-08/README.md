# roadmap-2026-07-08 迭代规划目录

> **当前主目录**:reindex worker 短驻化 + 队列可观测。
>
> **上轮归档**:[`../roadmap-2026-06-14/`](../roadmap-2026-06-14/)

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [reindex-worker-lifecycle-plan-2026-07-08.md](reindex-worker-lifecycle-plan-2026-07-08.md) | reindex 队列自动拉起短驻 worker、idle 退出、worker 健康状态、drain-once 运维入口 | 🟢 P1-P6 已落地 |

## 本轮判断

本轮不把问题定义为"索引算法慢",而是定义为"post-commit 已成功入队,但本机没有消费者"。

既有代码已经具备这些地基:

- `post-commit` / `post-merge` / `post-checkout` 走 `_dispatch_reindex()` 入队,默认不前台 drain。
- `ReindexWorker.drain_once()` 已能串行跑完当前待办,并在完成后刷新 health 快照。
- `ReindexWorker.run_forever()` 会启动先 drain、随后 watch + 60s 周期兜底,但没有 idle 退出。
- `reindex-queue status` 只看待办和 STALE,不直接报告 worker 是否存在。
- Linux/WSL 已有 `codev-reindex.service` systemd 渲染;Windows/本机缺等价的短驻 worker lifecycle。

## 本轮不做

- 不先做并发 worker。Chroma / SQLite / codegraph 写侧继续单写者。
- 不绕过队列手工清 marker。真实队列处理走 worker/drain/prune 命令。
- 不把 Windows 方案写成 systemd 替代品。Windows 走 hidden background process + pid/heartbeat;Linux/服务器继续可用 systemd 常驻。

## 成功指标

- commit 后无需手动起 worker,队列能自动被消费。
- worker 在无新任务一段时间后退出,避免本机长期占用资源。
- `reindex-queue status` 能一眼看到 worker running、heartbeat、last processed、oldest age。
- 开发时可用正式 `drain-once` 命令同步等待当前队列清空。

## 已执行修正

- 新增 reindex worker supervisor,用 start lock + run lock 防止重复拉起/重复消费。
- `post-commit` 入队后默认只对 FileSpoolQueue 幂等唤醒短驻 worker,PG 后端默认不 auto-start。
- `reindex-queue worker` 支持 `--idle-exit-sec`/`--heartbeat-sec`;不传仍保持常驻语义。
- 新增 `reindex-queue drain-once`;前台 drain 和 drain-once 都走同一把 run lock。
- `reindex-queue status` 改为只读队列,显示 backend、worker、heartbeat、last job、oldest age;worker 正在运行时不把 FileSpool marker 误报为 STALE。
- `health` 与 `serve-mcp status` 接入 reindex worker 摘要,可直接指出"队列有待办但 worker 不在"。
- `health --all` 访问受保护的 `/platform/status` 时支持 Bearer token,按 `platform.token_env` / `PLATFORM_TOKEN` / `CODEV_PLATFORM_MCP_TOKEN` 顺序取环境变量;401 会继续尝试下一个候选 token,不再裸请求 token-mode 详情面。
- `config.example.json` 补充 `platform.token_env` 样例,不修改用户级 `~/.codev-platform/config.json`。
- `webhook extra repos` 诊断改为只扫描本机配置中带 `webhook_repo`、实际会被 webhook 入口命中的项目,并对 config/meta 重复声明的同一路径或同一 child project 做去重;不再让未启用 webhook 的本机项目或 meta-only 项目污染 health/startup。
- `hook missed?` 诊断复用 reindex scope 公共推导;worker/manifest 模式下对 `chroma/codegraph/ingest/code_vec` 全量 expected kind 判绿,但代码类必须先由 runner proof marker 证明成功,避免 fail-soft rc=0 假绿。

## 测试审计记录

- 已单独做测试覆盖审计,补齐 PG 默认不 auto-start、start/run lock 恢复、防旧 token 覆盖、foreground drain 锁冲突、短驻 worker CLI 生命周期、真实 FileSpool STALE 防误报、auto-start fail-soft 返回分支。
- 目标回归:reindex/post-hook 相关 `93 passed`;MCP serve 相关 `43 passed`。
- `health --all` token 鉴权经单独测试审计兄弟二次审查,补齐无 token 时不发送 Authorization、首个 token 401 后继续尝试下一个候选 token 的覆盖;目标回归 `11 passed`,CLI parser `9 passed`,配置样板 JSON 解析通过。
- WSL 实调:`/healthz` 可达,`/platform/status` 仍 401;诊断确认交互 shell 未导出 `PLATFORM_TOKEN` / `CODEV_PLATFORM_MCP_TOKEN`,后续需单独处理 WSL token 环境注入。
- `webhook extra repos` 收敛经测试审计兄弟二次审查并修正扫描范围后,Windows/WSL 运行态诊断均为 0 条,`health --mode light` 均为 READY/all green;目标回归合计 `53 passed`(运行态相关 `44 passed` + CLI parser `9 passed`)。
- `hook missed?` manifest 兜底先经测试审计兄弟二次审查收窄为 `chroma` only,后续补 runner proof 后扩展为 `chroma/codegraph/ingest/code_vec` 全链路覆盖。`cmd_reindex` 成功路径输出 `proof: <kind> ok`;runner 对 codegraph/ingest/code_vec 的 fail-soft/skip 输出提升为失败,`code_vec` 同时要求 `proof: codegraph ok` 和 `proof: code_vec ok`;`CodegraphReindexRunner` 前置 ensure-link error 直接失败。实测首次暴露 Windows code_vec 因 platform-docs daemon 未启动而失败,随后修正 health/wait 语义:队列入队日志不再等于完成,同一 commit 多段日志取最新 queued block,manifest failed 时 `health` WARN、`wait-for-reindex` 返回失败,legacy finished failed 也返回失败。测试审计复核后又补齐 `wait-for-reindex` 同一 commit 多段日志取最新 block,避免旧 OK/旧 failed 干扰重跑结果。启动 daemon 后重跑 `code_vec` 成功。终审无阻塞,目标回归 `138 passed`,ruff 目标文件通过,Windows health READY。
- `reindex worker` runner 失败可观测性增强:每个 project/kind 的子进程输出覆盖写入 `data/logs/reindex-runner/<project>__<kind>.log`,失败/超时时 tail 写入 manifest note 并进入 worker.log detail;成功输出也保留在 runner log。测试审计兄弟复审无阻塞,目标回归 `52 passed`,ruff 目标文件通过。剩余风险:runner log 尚未做敏感串脱敏和单次日志大小上限。
- 实时状态:`reindex-queue status` 显示 FileSpoolQueue 队列空,短驻 worker 已 idle 退出。
