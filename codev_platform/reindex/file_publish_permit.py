"""File 发布能力的延迟提交生命周期。"""
from __future__ import annotations

from collections.abc import Callable

from codev_platform.reindex.queue_ports import QueueClaimLost


class DeferredFilePublishPermit:
    """ack 只登记意图，持锁 context 正常退出时才执行提交。"""

    def __init__(self, *, superseded: bool, commit: Callable[[], bool]) -> None:
        self._superseded = superseded
        self._commit_callback = commit
        self._ack_requested = False
        self._closed = False

    @property
    def superseded(self) -> bool:
        return self._superseded

    def ack(self) -> bool:
        if self._closed:
            raise RuntimeError("publish permit 已关闭")
        if self._ack_requested:
            return False
        self._ack_requested = True
        return True

    def commit_if_requested(self) -> None:
        if self._ack_requested and not self._commit_callback():
            raise QueueClaimLost("发布围栏提交时 claim 已失权")

    def close(self) -> None:
        self._closed = True


__all__ = ["DeferredFilePublishPermit"]
