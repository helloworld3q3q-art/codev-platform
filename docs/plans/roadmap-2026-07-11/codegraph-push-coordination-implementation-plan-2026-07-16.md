# CodeGraph 日常推送协调实施计划

> **状态（2026-07-20）：** Task 1–4 已完成并复审；Task 5 的真实 WSL、四类 manifest 与 MCP
> 验收由 `roadmap-2026-07-19` runtime-generation Integration 9–13 承接，本计划不再单独执行。
>
> **执行要求：** 按任务逐项测试驱动实现；每项先看到目标测试失败，再写最小实现。

**目标：** 让普通 push 触发的 CodeGraph 重建与在线 MCP 后端自动、有界协调，不再因长期租约形成失败 manifest 或永久等待。

**架构：** reindex 通过 OS advisory 写意图请求独占写窗口；HTTP 后端协作排空并释放既有操作租约；stage 只把协调结果映射为成功、真实失败或可重试三种结果。显式 systemd 维护流程保持不变。

**技术栈：** Python 3.12、`fcntl`/`msvcrt` advisory lock、`asyncio`、pytest、现有 isolated reindex executor。

## 全局约束

- 所有新增注释、异常和文档使用中文。
- 不新增配置键、数据库表、后台线程、PID/TTL 文件或 root 权限依赖。
- 锁等待、请求宽限和取消均有固定有限预算。
- 新模块单一职责；下层不导入 CLI、systemd、队列或 Web 层。
- 暂态竞争不写 failed manifest；真实 sync 错误不得降级。

---

### 任务 1：跨进程写意图与多仓协调

**文件：**
- 修改：`codev_platform/codegraph/operation_lease.py`
- 测试：`tests/test_codegraph_operation_coordination.py`

**接口：**
- 产出：`codegraph_writer_pending(repo, *, lock_root=None) -> bool`
- 产出：`codegraph_reindex_leases(repositories, *, timeout_sec=15.0, lock_root=None) -> AbstractContextManager[None]`

- [ ] 写进程级失败测试：写意图持有时另一进程可见，不同仓隔离，进程退出后自动释放。
- [ ] 写稳定顺序与超时失败测试：多仓先取得全部写意图，再取得操作租约；任一步失败逆序释放。
- [ ] 运行目标测试并确认因接口缺失失败。
- [ ] 复用现有路径规范化、摘要和 `advisory_lock`，实现最小协调上下文。
- [ ] 运行目标测试并确认通过。

### 任务 2：可排空后端收件箱

**文件：**
- 新建：`codev_platform/codegraph/backend_inbox.py`
- 测试：`tests/test_codegraph_backend_inbox.py`

**接口：**
- 产出：`CodegraphBackendDrainingError`
- 产出：`BackendInbox.reset/open/submit/next/close`
- 产出：`wait_for_writer_intent(check, *, poll_sec) -> Awaitable[None]`

- [ ] 写失败测试：关闭时拒绝新请求；关闭会拒绝全部排队 future；重新开放使用新队列。
- [ ] 写失败测试：轮询探针只在返回真时完成，探针异常原样失败。
- [ ] 运行目标测试并确认接口缺失失败。
- [ ] 实现状态锁、无界队列和统一暂态异常，不引入后台线程。
- [ ] 运行目标测试并确认通过。

### 任务 3：HTTP 后端协作排空

**文件：**
- 修改：`codev_platform/codegraph/server.py`
- 修改：`tests/test_codegraph_server_fanout.py`

**接口：**
- 消费：任务 1 的写意图探针与现有操作租约。
- 消费：任务 2 的收件箱和排空异常。

- [ ] 写失败测试：启动前与取得租约后二次检查写意图，存在时不得 spawn stdio。
- [ ] 写失败测试：空闲后端看到写意图后关闭；当前调用在宽限内完成；超时调用被取消；排队请求被拒绝。
- [ ] 写失败测试：排空异常映射为 `upstream_unavailable`，下一次请求在意图消失后可懒启动。
- [ ] 运行目标测试并确认现有长期会话行为不满足新契约。
- [ ] 用小函数组合收件箱、意图 watcher 和 stdio 调用，保持主循环最多两层嵌套。
- [ ] 运行 fanout 与启动协调测试并确认通过。

### 任务 4：reindex 三态返回与队列回归

**文件：**
- 修改：`codev_platform/ops/reindex/codegraph_stage.py`
- 修改：`codev_platform/ops/reindex/commands.py`
- 修改：`tests/test_reindex_codegraph_operation_lease.py`
- 修改：`tests/test_reindex_runner_timeout.py`

**接口：**
- stage 成功：`(False, 0)` 并输出一次 `proof: codegraph ok`。
- 协调超时：`(True, 2)`，不输出 proof。
- 真实错误：非零失败码，保持失败。

- [ ] 写失败测试：多仓只申请一次协调上下文；协调忙返回 `rc=2`；真实 sync 错误仍返回 `rc=1`。
- [ ] 写失败测试：isolated runner 收到 `rc=2` 时 proof 不改判为 `rc=1`。
- [ ] 运行目标测试并确认当前 skip/proof 链失败。
- [ ] 接线多仓协调并收敛三态返回，删除不再可达的旧锁忙分支。
- [ ] 运行 stage、runner、executor、publisher、dependency gate 回归并确认通过。

### 任务 5：综合验证与 WSL 发布

**文件：**
- 修改：`docs/plans/roadmap-2026-07-11/daily-summary-2026-07-16.md`
- 修改：`docs/plans/roadmap-2026-07-11/README.md`

- [ ] 运行 Ruff、`git diff --check` 和相关完整 pytest 集合。
- [ ] 提交时核对作者为 `helloworld3q3q <helloworld3q3q>`，只推送 `origin/dev`。
- [ ] 从最终完整 SHA 创建独立 WSL release 并通过 runtime/CUDA 探针。
- [ ] 首次升级使用现有受控 maintenance 流程停止旧后端、重建四类索引并恢复 CodeGraph。
- [ ] 验证 worker 健康、四类 manifest 精确等于最终 SHA、四个 MCP HTTP 端点可用。
- [ ] 验收后清理未激活候选和旧 release，只保留一个活动环境及必要的可重建 Git 提交证据。
