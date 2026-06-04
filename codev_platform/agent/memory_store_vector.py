"""VectorSyncMemoryStore —— 写时增量 embed 装饰器(B1 step2)。

包在任意 MemoryStore 外:write/supersede/forget 透传内层后,同步维护向量索引(upsert/delete)。
所有写路径(remember 工具 / web 路由 / memory MCP)都走 `deps.get_memory_store()`,故只需在 deps
包这一层,**全写路径自动 embed**(单一接缝,不碰 3 个调用点)。

向量同步失败**不影响主写**(swallow + log):向量是召回加速副本,坏了顶多召回退化为关键词,绝不能
让 embed 异常把记忆写库本身搞挂。
"""
from __future__ import annotations

import logging

from codev_platform.agent.memory_store import MemoryEntry, MemoryStore
from codev_platform.agent.memory_vector import MemoryVectorIndex

_log = logging.getLogger(__name__)


class VectorSyncMemoryStore(MemoryStore):
    def __init__(self, inner: MemoryStore, index: MemoryVectorIndex) -> None:
        self._inner = inner
        self._index = index

    # ---- 写路径:透传 + 向量同步 ----

    def write(self, entry: MemoryEntry) -> str:
        eid = self._inner.write(entry)
        entry.id = entry.id or eid          # 索引按真实 id upsert
        self._safe_upsert(entry)
        return eid

    def supersede(self, old_id: str, new_entry: MemoryEntry, *,
                  owner_user_id: str | None = None, protect_redline: bool = False) -> str:
        eid = self._inner.supersede(old_id, new_entry, owner_user_id=owner_user_id,
                                    protect_redline=protect_redline)
        # 旧条已 superseded(不再 active)→ 从索引移除;新条入索引。
        self._safe_delete(old_id, new_entry.org_id)
        new_entry.id = new_entry.id or eid
        self._safe_upsert(new_entry)
        return eid

    def forget(self, entry_id: str, *, owner_user_id: str | None = None,
               org_id: str | None = None, protect_redline: bool = False) -> bool:
        ok = self._inner.forget(entry_id, owner_user_id=owner_user_id, org_id=org_id,
                                protect_redline=protect_redline)
        if ok and org_id:
            self._safe_delete(entry_id, org_id)
        return ok

    # ---- 读 / 其它:纯透传 ----

    def list_scope(self, scope, scope_ref, org_id="default", limit=100):
        return self._inner.list_scope(scope, scope_ref, org_id=org_id, limit=limit)

    def archive(self, entry_id: str) -> bool:
        # 注: archive/archive_expired 不同步删向量(archive 入参无 org_id, 无法定位 per-org
        # collection; archive_expired 是批量无 id)。不构成泄漏 —— recall 的 `vec_ids ∩ active 池`
        # 硬闸会过滤掉已归档条;残留向量仅是冗余,留待后续"向量 GC"(扫 status!=active 清理)。
        return self._inner.archive(entry_id)

    def archive_expired(self, org_id: str | None = None) -> int:
        return self._inner.archive_expired(org_id=org_id)

    def set_task_state(self, task_id, task_state, org_id="default", owner_user_id=None) -> int:
        return self._inner.set_task_state(task_id, task_state, org_id=org_id,
                                          owner_user_id=owner_user_id)

    def __getattr__(self, name):
        # 透传内层额外方法(如 advisory_lock),不在 ABC 内的也能用。
        return getattr(self._inner, name)

    # ---- 向量同步(失败不影响主写)----

    def _safe_upsert(self, entry: MemoryEntry) -> None:
        try:
            self._index.upsert(entry)
        except Exception as e:  # noqa: BLE001 — 向量是副本,坏了不拖垮写库
            _log.warning("[memory_vector] upsert 失败 id=%s: %s: %s", entry.id, type(e).__name__, e)

    def _safe_delete(self, entry_id: str, org_id: str) -> None:
        try:
            self._index.delete(entry_id, org_id=org_id)
        except Exception as e:  # noqa: BLE001
            _log.warning("[memory_vector] delete 失败 id=%s: %s: %s", entry_id, type(e).__name__, e)
