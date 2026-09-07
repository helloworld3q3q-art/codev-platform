"""部署运行时受管进程的固定资源 profile 真值。"""

from __future__ import annotations

from codev_platform.runtime_managed_process import ManagedProcessLimits


CANDIDATE_BUILD_LIMITS = ManagedProcessLimits(
    runtime_sec=900.0,
    stop_sec=30.0,
    stdout_limit_bytes=64 * 1024,
    stderr_limit_bytes=64 * 1024,
    tasks_max=128,
    memory_high_bytes=6 * 1024**3,
    memory_max_bytes=8 * 1024**3,
    memory_swap_max_bytes=0,
    cpu_quota_percent=400,
)

TARGET_USER_PROBE_LIMITS = ManagedProcessLimits(
    runtime_sec=120.0,
    stop_sec=30.0,
    stdout_limit_bytes=4 * 1024,
    stderr_limit_bytes=4 * 1024,
    tasks_max=64,
    memory_high_bytes=3 * 1024**3,
    memory_max_bytes=4 * 1024**3,
    memory_swap_max_bytes=0,
    cpu_quota_percent=200,
)


def deployment_leaf_limits(runtime_sec: float) -> ManagedProcessLimits:
    """维护叶子只允许调整受计划约束的墙钟，其他资源保持固定。"""
    return ManagedProcessLimits(
        runtime_sec=runtime_sec,
        stop_sec=30.0,
        stdout_limit_bytes=64 * 1024,
        stderr_limit_bytes=64 * 1024,
        tasks_max=128,
        memory_high_bytes=6 * 1024**3,
        memory_max_bytes=8 * 1024**3,
        memory_swap_max_bytes=0,
        cpu_quota_percent=400,
    )


__all__ = [
    "CANDIDATE_BUILD_LIMITS",
    "TARGET_USER_PROBE_LIMITS",
    "deployment_leaf_limits",
]
