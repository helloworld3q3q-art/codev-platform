"""cgroup 两阶段启动在句柄落盘前的确定性恢复。"""

from __future__ import annotations

from collections.abc import Callable

from codev_platform.reindex.attempt_process import (
    Deadline,
    RecoveryReport,
    RecoveryState,
)
from codev_platform.reindex.cgroup_v2 import (
    CgroupFilesystem,
    encode_cgroup_native_ref,
)


def recover_unjournaled_attempt(
    *,
    filesystem: CgroupFilesystem,
    boot_id: str,
    attempt_id: str,
    deadline: Deadline,
    wait_empty: Callable[[str, float], bool],
) -> RecoveryReport:
    """终止并移除 prepare 后、完整句柄落盘前遗留的 cgroup。"""
    try:
        exists = filesystem.exists_attempt(attempt_id)
        native_ref = encode_cgroup_native_ref(boot_id, attempt_id)
    except (OSError, ValueError):
        return RecoveryReport(
            RecoveryState.UNCONFIRMED, None, None, "attempt cgroup 存在性无法验证"
        )
    if not exists:
        return RecoveryReport(RecoveryState.NEVER_STARTED, None, None, "attempt cgroup 不存在")
    try:
        filesystem.kill(native_ref)
    except (OSError, ValueError):
        return RecoveryReport(RecoveryState.UNCONFIRMED, None, None, "遗留 attempt cgroup 无法终止")
    if not wait_empty(native_ref, deadline.expires_at):
        return RecoveryReport(
            RecoveryState.UNCONFIRMED, None, None, "遗留 attempt cgroup 未确认清空"
        )
    try:
        filesystem.remove_empty(native_ref)
    except (OSError, ValueError):
        return RecoveryReport(RecoveryState.UNCONFIRMED, None, None, "遗留 attempt cgroup 无法移除")
    return RecoveryReport(
        RecoveryState.NEVER_STARTED,
        None,
        None,
        "遗留 blocked bootstrap 已清理，目标从未放行",
    )


__all__ = ["recover_unjournaled_attempt"]
