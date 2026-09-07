"""Pg 队列隔离测试的共享常量与租约构造。"""

from __future__ import annotations

from codev_platform.reindex.queue_ports import ClaimedJob, Job, JobMeta

_OID_A = "a" * 40
_OID_B = "b" * 40


def _claim(*, owner: str = "worker-1", token: str = "claim-1") -> ClaimedJob:
    return ClaimedJob(
        Job(
            "demo",
            "chroma",
            10.0,
            meta=JobMeta(source="test", target_commit=_OID_A),
            lease_expires_at=200.0,
        ),
        token,
        owner,
        200.0,
    )
