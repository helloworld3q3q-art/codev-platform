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

## 测试审计记录

- 已单独做测试覆盖审计,补齐 PG 默认不 auto-start、start/run lock 恢复、防旧 token 覆盖、foreground drain 锁冲突、短驻 worker CLI 生命周期、真实 FileSpool STALE 防误报、auto-start fail-soft 返回分支。
- 目标回归:reindex/post-hook 相关 `93 passed`;MCP serve 相关 `43 passed`。
- `health --all` token 鉴权经单独测试审计兄弟二次审查,补齐无 token 时不发送 Authorization、首个 token 401 后继续尝试下一个候选 token 的覆盖;目标回归 `11 passed`,CLI parser `9 passed`,配置样板 JSON 解析通过。
- WSL 实调:`/healthz` 可达,`/platform/status` 仍 401;诊断确认交互 shell 未导出 `PLATFORM_TOKEN` / `CODEV_PLATFORM_MCP_TOKEN`,后续需单独处理 WSL token 环境注入。
- 实时状态:`reindex-queue status` 显示 FileSpoolQueue 队列空,短驻 worker 已 idle 退出。
