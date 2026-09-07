"""B1 step2 单测 —— build_scope_where(纯函数)+ VectorSyncMemoryStore 装饰器(fake inner/index)。

chroma / 嵌入模型的真实行为由 WSL 实测覆盖,这里只测可纯逻辑测的部分。
"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.agent.memory_store_vector import VectorSyncMemoryStore
from codev_platform.agent.memory_vector_chroma import build_scope_where


def _e(id="e1", org="acme", scope="personal", ref="alice", content="c", redline=False):
    return MemoryEntry(id=id, scope=scope, scope_ref=ref, owner_user_id="alice",
                       content=content, org_id=org, is_redline=redline)


# ---- build_scope_where ----

def test_where_empty_scopes():
    assert build_scope_where("acme", []) == {"org_id": "acme"}


def test_where_single_scope():
    w = build_scope_where("acme", [("personal", "alice")])
    assert w == {"$and": [{"org_id": "acme"}, {"$and": [{"scope": "personal"}, {"scope_ref": "alice"}]}]}


def test_where_multi_scope_uses_or():
    w = build_scope_where("acme", [("org", "org"), ("personal", "alice")])
    assert w["$and"][0] == {"org_id": "acme"}
    assert "$or" in w["$and"][1] and len(w["$and"][1]["$or"]) == 2


# ---- VectorSyncMemoryStore ----

class _Inner:
    def __init__(self):
        self.calls = []

    def write(self, entry):
        self.calls.append(("write", entry.id))
        return entry.id or "wid-new"

    def supersede(self, old_id, new_entry, *, owner_user_id=None, protect_redline=False):
        self.calls.append(("supersede", old_id, owner_user_id, protect_redline))
        return "sid-new"

    def forget(self, entry_id, *, owner_user_id=None, org_id=None, protect_redline=False):
        self.calls.append(("forget", entry_id, owner_user_id, org_id, protect_redline))
        return self._forget_ret

    _forget_ret = True

    def list_scope(self, scope, scope_ref, org_id="default", limit=100):
        return ["INNER-RESULT"]

    def advisory_lock(self, key):   # 额外方法,走 __getattr__ 透传
        return f"LOCK-{key}"


class _Idx:
    def __init__(self, fail=False):
        self.ups = []
        self.dels = []
        self._fail = fail

    def upsert(self, entry):
        if self._fail:
            raise RuntimeError("boom")
        self.ups.append(entry.id)

    def delete(self, entry_id, *, org_id):
        if self._fail:
            raise RuntimeError("boom")
        self.dels.append((entry_id, org_id))


def test_write_syncs_upsert():
    inner, idx = _Inner(), _Idx()
    s = VectorSyncMemoryStore(inner, idx)
    eid = s.write(_e(id="e9"))
    assert eid == "e9" and idx.ups == ["e9"]


def test_write_empty_id_uses_returned_id_for_index():
    inner, idx = _Inner(), _Idx()
    eid = VectorSyncMemoryStore(inner, idx).write(_e(id=""))
    assert eid == "wid-new" and idx.ups == ["wid-new"]   # 索引按真实 id


def test_forget_syncs_delete_with_org():
    inner, idx = _Inner(), _Idx()
    ok = VectorSyncMemoryStore(inner, idx).forget("e9", owner_user_id="alice", org_id="acme")
    assert ok and idx.dels == [("e9", "acme")]


def test_forget_no_org_no_delete():
    inner, idx = _Inner(), _Idx()
    VectorSyncMemoryStore(inner, idx).forget("e9", owner_user_id="alice")
    assert idx.dels == []   # 无 org_id 不知删哪库 → 不删(不瞎删)


def test_forget_not_found_no_delete():
    inner, idx = _Inner(), _Idx()
    inner._forget_ret = False
    VectorSyncMemoryStore(inner, idx).forget("e9", org_id="acme")
    assert idx.dels == []   # inner 没删成(非本人/不存在)→ 索引也不动


def test_supersede_deletes_old_upserts_new():
    inner, idx = _Inner(), _Idx()
    new = _e(id="new1", org="acme")
    eid = VectorSyncMemoryStore(inner, idx).supersede("old1", new, owner_user_id="alice")
    assert eid == "sid-new"
    assert idx.dels == [("old1", "acme")] and idx.ups == ["new1"]


def test_index_failure_does_not_break_write():
    inner, idx = _Inner(), _Idx(fail=True)
    eid = VectorSyncMemoryStore(inner, idx).write(_e(id="e9"))   # 不抛
    assert eid == "e9"   # 写库成功, 向量同步失败被吞


def test_list_scope_and_extra_method_delegate():
    s = VectorSyncMemoryStore(_Inner(), _Idx())
    assert s.list_scope("personal", "alice") == ["INNER-RESULT"]
    assert s.advisory_lock(7) == "LOCK-7"   # __getattr__ 透传内层额外方法
