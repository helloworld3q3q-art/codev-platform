"""VectorRecallService(B1)单测 —— 向量只换排序层,前置(scope/冲突消解/redline)全保留。

fake store + fake index, 不碰 chroma / GPU。验证: redline 永置顶、向量驱动重排、空 query 退化、
索引异常兜底、冲突消解前置、池外向量 id 忽略。
"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.agent.recall_service import LocalRecallService, VectorRecallService


def _e(id, scope, content, topic_key=None, is_redline=False):
    ref = {"org": "org", "project": "proj1", "personal": "alice"}[scope]
    return MemoryEntry(id=id, scope=scope, scope_ref=ref, owner_user_id="alice",
                       content=content, topic_key=topic_key, is_redline=is_redline)


class _Store:
    def __init__(self, per_scope):
        self._m = per_scope  # {scope: [entries]}

    def list_scope(self, scope, scope_ref, org_id="default", limit=100):
        return list(self._m.get(scope, []))


class _Index:
    """query_ids 返回预置 id 序;raises=True 模拟向量库不可用。"""
    def __init__(self, ids, raises=False):
        self._ids = ids
        self._raises = raises
        self.calls = []

    def upsert(self, entry):  # 接口完整性
        pass

    def delete(self, entry_id):
        pass

    def query_ids(self, query, *, org_id, scopes, k):
        if self._raises:
            raise RuntimeError("vector index down")
        self.calls.append((query, org_id, tuple(scopes), k))
        return list(self._ids)


def _store_4():
    # org redline + project + 2 personal, 无 topic_key(全 passthrough)
    return _Store({
        "org": [_e("r1", "org", "org redline rule", is_redline=True)],
        "project": [_e("pr1", "project", "project note one")],
        "personal": [_e("p1", "personal", "personal note one"),
                     _e("p2", "personal", "personal note two")],
    })


def _ctx():
    return dict(org_id="default", user_id="alice", project_id="proj1")


def test_redline_top_and_vector_reorders_rest():
    # query 关键词命不中任何条 → 排序全靠向量;向量把 p2 顶到非 redline 首位。
    store = _store_4()
    idx = _Index(["p2"])
    svc = VectorRecallService(store, idx, rbac_store=None)
    out = svc.recall(query="zzz", limit=8, **_ctx())
    ids = [e.id for e in out]
    assert ids[0] == "r1"           # redline 永置顶
    assert ids[1] == "p2"           # 向量驱动:p2 被顶到非 redline 首位
    assert set(ids) == {"r1", "pr1", "p1", "p2"}  # 候选不丢


def test_redline_never_demoted_even_if_vector_ranks_others():
    # 向量强推 p1/pr1, redline 仍必须第一(组织硬约束不被语义挤下)。
    store = _store_4()
    svc = VectorRecallService(store, _Index(["p1", "pr1", "p2"]), rbac_store=None)
    out = svc.recall(query="note", limit=8, **_ctx())
    assert out[0].id == "r1"


def test_empty_query_falls_back_to_local():
    store = _store_4()
    vec = VectorRecallService(store, _Index(["p2"]), rbac_store=None)
    loc = LocalRecallService(store, rbac_store=None)
    out_vec = [e.id for e in vec.recall(query="", limit=8, **_ctx())]
    out_loc = [e.id for e in loc.recall(query="", limit=8, **_ctx())]
    assert out_vec == out_loc       # 空 query 向量无信号 → 与 Local 一致


def test_index_failure_falls_back_to_keyword():
    store = _store_4()
    svc = VectorRecallService(store, _Index([], raises=True), rbac_store=None)
    out = svc.recall(query="one", limit=8, **_ctx())  # 不崩
    assert out[0].id == "r1"        # 退回关键词,redline 仍置顶
    assert {e.id for e in out} == {"r1", "pr1", "p1", "p2"}


def test_conflict_resolution_preserved_before_ranking():
    # p1 与 pr1 同 topic_key → personal_first 下 p1 胜、pr1 被压;即便向量推 pr1 也召不回。
    store = _Store({
        "org": [],
        "project": [_e("pr1", "project", "proj val", topic_key="t")],
        "personal": [_e("p1", "personal", "personal val", topic_key="t")],
    })
    svc = VectorRecallService(store, _Index(["pr1"]), rbac_store=None)
    ids = {e.id for e in svc.recall(query="zzz", limit=8, **_ctx())}
    assert "p1" in ids and "pr1" not in ids   # 冲突消解前置,被压的不因向量复活


def test_vector_id_outside_pool_ignored():
    store = _store_4()
    svc = VectorRecallService(store, _Index(["ghost", "p2"]), rbac_store=None)
    out = svc.recall(query="zzz", limit=8, **_ctx())
    ids = [e.id for e in out]
    assert "ghost" not in ids and ids[1] == "p2"   # 池外 id 忽略,不崩


def test_index_query_gets_visible_scopes():
    # 向量查询必须带可见作用域(防绕过 ACL):索引收到 org+project+personal。
    store = _store_4()
    idx = _Index(["p1"])
    VectorRecallService(store, idx, rbac_store=None).recall(query="x", limit=8, **_ctx())
    _q, org, scopes, _k = idx.calls[0]
    assert org == "default"
    assert ("personal", "alice") in scopes and ("project", "proj1") in scopes
