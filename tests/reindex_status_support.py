from __future__ import annotations


from codev_platform.reindex.queue import QueueSnapshot

class _Queue:
    def __init__(self, pending=(), active=(), results=(), expired_active=()):
        self._snapshot = QueueSnapshot(
            pending=list(pending),
            active=list(active),
            results=list(results),
            expired_active=list(expired_active),
        )

    def snapshot(self):
        return self._snapshot
