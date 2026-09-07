# CodeGraph 日常推送协调设计

> 日期：2026-07-16
>
> 状态：已确认，进入实现

## 一、问题

在线 CodeGraph HTTP 代理会让每个 stdio 后端在整个会话期间持有仓级操作租约；日常
reindex 同步使用同一租约。后端被长期缓存后，普通 push 触发的 CodeGraph 任务无法取得
租约。当前 stage 把租约忙先降为 `rc=0`，随后证明层又把缺少成功证明改判为 `rc=1`，
最终形成失败 manifest，并阻断同一目标的 `ingest` 与 `code_vec`。

该问题不是队列卡死，也不能靠删除锁、无限重试或每次 push 都以 root 停 systemd 解决。

## 二、方案比较

### 方案 A：每次 push 自动进入 systemd 维护窗口

能够强制释放租约，但把日常增量更新耦合到 root、runtime mask 和服务重启；会中断全部
CodeGraph 客户端，也扩大 webhook 权限边界。保留给 release、回滚和结构迁移，不用于日常 push。

### 方案 B：只把租约忙改为 `rc=2`

能够避免写失败 manifest，但长期后端不会主动释放租约，任务可能永久重试。只能作为正确
错误语义的一部分，不能单独解决问题。

### 方案 C：写意图、协作排空和有界重试（采用）

reindex 先取得短生命周期的仓级写意图，再有界等待原操作租约。在线后端观察到写意图后，
停止接收新请求，排空或在宽限时间后取消当前请求，关闭 stdio 会话并释放租约。同步完成后
写意图随 OS 锁自动释放，下一次 MCP 请求按现有机制懒启动后端。等待预算耗尽返回 `rc=2`，
由现有 executor 重试；真实同步错误仍返回 `rc=1`。

## 三、模块边界

1. `codev_platform/codegraph/operation_lease.py`
   - 维护操作租约与写意图路径的唯一真值源。
   - 提供多仓稳定排序的 reindex 协调上下文。
   - 仅负责同步锁协议，不依赖 HTTP、队列、systemd 或 manifest。
2. `codev_platform/codegraph/backend_inbox.py`
   - 管理后端收件箱的关闭、重新开放、排队请求拒绝和写意图轮询。
   - 对外只暴露窄异常和请求生命周期接口，不知道 Git、reindex 或 systemd。
3. `codev_platform/codegraph/server.py`
   - 组合操作租约、写意图探针、收件箱和 stdio session。
   - 写意图出现时给当前调用有限宽限；超时只取消该仓后端，不停止 HTTP 服务。
   - 暂态排空对外映射为 `upstream_unavailable`。
4. `codev_platform/ops/reindex/codegraph_stage.py`
   - 使用多仓协调上下文执行 sync。
   - 协调超时返回可重试 `rc=2`，不输出成功证明。

## 四、时序与竞态

1. reindex 先按规范仓根稳定排序，取得全部写意图，再按同一顺序等待全部操作租约。
2. 后端在启动前检查写意图，取得操作租约后再次检查，封闭“检查后、加锁前”竞态。
3. 已运行后端在空闲等待和当前调用执行期间都轮询写意图。
4. 发现写意图后先关闭收件箱并拒绝排队请求；当前请求在宽限时间内可完成，超时则取消。
5. reindex 超过总预算仍未取得租约时释放全部已取得资源并返回可重试结果。
6. 锁由内核随文件描述符或进程退出释放，不写 PID、TTL 或需人工清理的状态文件。

## 五、失败语义

- 写意图或租约路径不可验证：失败关闭，不启动/继续后端。
- 有界协调超时：`rc=2`，不写 failed manifest，依赖任务保持等待。
- `codegraph sync` 非零且非原生锁忙：`rc=1`，保持真实失败。
- 后端排空中的 MCP 请求：机器码 `upstream_unavailable`，不泄露锁路径或内部异常。
- 显式维护流程保持原契约，不被日常协调替代。

## 六、验收

- 同仓写意图跨进程可见，不同仓互不影响，持有者崩溃后自动释放。
- 后端空闲、调用中、调用超时、排队请求和启动竞态均能有界排空。
- 租约忙不再生成失败 manifest；随后成功会自动放行 `ingest/code_vec`。
- 多仓按稳定顺序协调，不产生部分仓同步证明。
- 现有 maintenance、CodeGraph fanout、executor 与 dependency gate 回归保持通过。
- WSL 真实流程最终四类 manifest 均为目标 SHA，MCP 排空后可懒恢复。
