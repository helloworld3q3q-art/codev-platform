"""PipelineRecallService 不变量 + Fusion/registry 盲区直测(P0 解构补漏)。

旧 test_agent_vector_recall.py 经 VectorRecallService(固定 2 scorer + NoReranker)间接测;本文件用
fake 多 scorer / 恶意 scorer / 反转 reranker 直接构 PipelineRecallService,把"插件碰不到的不变量"
(redline 永置顶 / 去重前置 / top-N 截断 / rbac_store 走 compute_visible_scopes)钉死在 pipeline 层;
另补 RrfFusion 单元(单路恒等 / 3+ 路 / 空)+ registry 的 fusion/rerank 未知名退默认 + rerank_top_k 透传。

纯逻辑:fake store / scorer / index / rbac_store,不碰 PG / GPU / chroma。
"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.agent.recall.base import RankCtx, Reranker, Scorer
from codev_platform.agent.recall.fusion import RrfFusion
from codev_platform.agent.recall.reranker import NoReranker
from codev_platform.agent.recall.scorers import KeywordScorer
from codev_platform.agent.recall.service import PipelineRecallService
from codev_platform.core.rbac import Membership


# ---------------- fakes ----------------

def _e(id, scope, content, topic_key=None, is_redline=False, ref=None):
    ref = ref or {"org": "org", "project": "proj1", "personal": "alice", "team": "teamA"}[scope]
    return MemoryEntry(id=id, scope=scope, scope_ref=ref, owner_user_id="alice",
                       content=content, topic_key=topic_key, is_redline=is_redline)


class _Store:
    """list_scope 按 (scope) 取预置;记录被请求的 (scope, ref) 以验作用域路由。"""
    def __init__(self, per_scope):
        self._m = per_scope
        self.asked = []

    def list_scope(self, scope, scope_ref, org_id="default", limit=100):
        self.asked.append((scope, scope_ref))
        return list(self._m.get(scope, []))


class _SeqScorer(Scorer):
    """返回固定 id 序(只取落在候选池内的)。模拟任意打分器。"""
    def __init__(self, name, ids, raises=False):
        self.name = name
        self._ids = ids
        self._raises = raises

    def rank(self, entries, query, ctx: RankCtx):
        if self._raises:
            raise RuntimeError(f"{self.name} down")
        pool = {e.id for e in entries}
        return [i for i in self._ids if i in pool]


class _ReverseReranker(Reranker):
    """把传入顺序整个反转 —— 用来证明 reranker 只作用于非 redline 段。"""
    name = "reverse"

    def rerank(self, query, entries, top_k):
        return list(reversed(entries))


class _RbacStore:
    def __init__(self, membership):
        self._m = membership
        self.calls = []

    def fetch_membership(self, org_id, user_id, project_id):
        self.calls.append((org_id, user_id, project_id))
        return self._m


def _svc(store, scorers, *, fusion=None, reranker=None, **kw):
    return PipelineRecallService(
        store, scorers=scorers, fusion=fusion or RrfFusion(),
        reranker=reranker or NoReranker(), **kw)


def _ctx():
    return dict(org_id="default", user_id="alice", project_id="proj1")


# ---------------- 不变量:pipeline 层直测 ----------------

def test_redline_top_even_if_scorer_ranks_it_last():
    # 恶意 scorer 把 redline id 放最后;pipeline 仍把 redline 永置顶(redline 在 scorer 前已拆出)。
    store = _Store({
        "org": [_e("r1", "org", "redline", is_redline=True)],
        "project": [_e("pr1", "project", "note one")],
        "personal": [_e("p1", "personal", "note two")],
    })
    svc = _svc(store, [_SeqScorer("s", ["pr1", "p1", "r1"])], rbac_store=None)
    out = [e.id for e in svc.recall(query="note", limit=8, **_ctx())]
    assert out[0] == "r1"
    assert set(out) == {"r1", "pr1", "p1"}


def test_reranker_cannot_touch_redline_segment():
    # 反转 reranker 只反转非 redline 段;redline 仍第一(reranker 拿不到 redline)。
    store = _Store({
        "org": [_e("r1", "org", "redline", is_redline=True)],
        "personal": [_e("p1", "personal", "alpha"), _e("p2", "personal", "beta")],
    })
    svc = _svc(store, [_SeqScorer("s", ["p1", "p2"])], reranker=_ReverseReranker(),
               rbac_store=None)
    out = [e.id for e in svc.recall(query="alpha", limit=8, **_ctx())]
    assert out[0] == "r1"               # redline 免疫 reranker
    assert out[1:] == ["p2", "p1"]      # 非 redline 段被 reranker 反转


def test_conflict_dedup_before_scorers_no_revival():
    # p1 与 pr1 同 topic_key → personal_first 下 pr1 被压;scorer 强推 pr1 也不复活。
    store = _Store({
        "org": [],
        "project": [_e("pr1", "project", "proj val", topic_key="t")],
        "personal": [_e("p1", "personal", "personal val", topic_key="t")],
    })
    svc = _svc(store, [_SeqScorer("s", ["pr1", "p1"])], rbac_store=None)
    ids = {e.id for e in svc.recall(query="zzz", limit=8, **_ctx())}
    assert ids == {"p1"}                 # 被压条不因 scorer 复活


def test_top_n_truncation_keeps_redline():
    # 5 条候选(1 redline + 4 rest),limit=2 → 截断为 redline + 1。
    store = _Store({
        "org": [_e("r1", "org", "redline", is_redline=True)],
        "personal": [_e(f"p{i}", "personal", f"note {i}") for i in range(4)],
    })
    svc = _svc(store, [_SeqScorer("s", ["p0", "p1", "p2", "p3"])], rbac_store=None)
    out = [e.id for e in svc.recall(query="note", limit=2, **_ctx())]
    assert len(out) == 2 and out[0] == "r1"


def test_rbac_store_drives_visible_scopes():
    # rbac_store 非 None + user → 走 compute_visible_scopes(Membership):仅 org + 个人(无 project_role)。
    store = _Store({"org": [_e("o1", "org", "org note")],
                    "project": [_e("pr1", "project", "proj note")],
                    "personal": [_e("p1", "personal", "personal note")]})
    rbac = _RbacStore(Membership(org_role="member", teams=(), project_role=None))
    svc = _svc(store, [KeywordScorer()], rbac_store=rbac)
    out = {e.id for e in svc.recall(query="note", limit=8, **_ctx())}
    assert rbac.calls == [("default", "alice", "proj1")]
    asked_scopes = {s for s, _ in store.asked}
    assert asked_scopes == {"org", "personal"}     # project 无角色 → 不可见,未被拉取
    assert "pr1" not in out and out == {"o1", "p1"}


def test_rbac_store_includes_team_scope():
    # Membership 带 team → team 作用域可见(compute_visible_scopes 路径完整性)。
    store = _Store({"org": [_e("o1", "org", "org note")],
                    "team": [_e("tm1", "team", "team note")],
                    "personal": [_e("p1", "personal", "personal note")]})
    rbac = _RbacStore(Membership(org_role="member", teams=(("teamA", "member"),), project_role=None))
    svc = _svc(store, [KeywordScorer()], rbac_store=rbac)
    out = {e.id for e in svc.recall(query="note", limit=8, **_ctx())}
    assert ("team", "teamA") in store.asked and "tm1" in out


# ---------------- 降级路径:pipeline 层 ----------------

def test_all_scorers_throw_falls_back_to_keyword():
    # 两个 scorer 全异常 → 关键词兜底(_rank_for_query),不崩,候选不丢。
    store = _Store({"org": [_e("r1", "org", "redline alpha", is_redline=True)],
                    "personal": [_e("p1", "personal", "alpha one"),
                                 _e("p2", "personal", "beta two")]})
    svc = _svc(store, [_SeqScorer("a", [], raises=True), _SeqScorer("b", [], raises=True)],
               rbac_store=None)
    out = [e.id for e in svc.recall(query="alpha", limit=8, **_ctx())]
    assert out[0] == "r1"
    assert set(out) == {"r1", "p1", "p2"}       # 全异常仍返回完整候选


def test_single_scorer_throws_others_plus_keyword_backfill():
    # 一个 scorer 异常 → 另一个仍生效;且关键词补漏把未被 fusion 覆盖的候选补到末尾。
    store = _Store({"org": [],
                    "personal": [_e("p1", "personal", "alpha"),
                                 _e("p2", "personal", "alpha beta"),
                                 _e("p3", "personal", "gamma")]})
    # good scorer 只召回 p2;p1/p3 靠关键词补漏(p1 命中 alpha 排前)
    svc = _svc(store, [_SeqScorer("bad", [], raises=True), _SeqScorer("good", ["p2"])],
               rbac_store=None)
    out = [e.id for e in svc.recall(query="alpha", limit=8, **_ctx())]
    assert out[0] == "p2"                         # 存活 scorer 驱动首位
    assert set(out) == {"p1", "p2", "p3"}         # 补漏:无候选丢失


def test_empty_query_uses_rank_for_query():
    # 空 query → 跳过 scorer/fusion,直接 _rank_for_query(redline 置顶 + recency)。
    store = _Store({"org": [_e("r1", "org", "redline", is_redline=True)],
                    "personal": [_e("p1", "personal", "x"), _e("p2", "personal", "y")]})
    # scorer 若被调用会把 p2 顶前;空 query 不应调用它 → p2 不被特殊提升
    svc = _svc(store, [_SeqScorer("s", ["p2", "p1"])], rbac_store=None)
    out = [e.id for e in svc.recall(query="   ", limit=8, **_ctx())]
    assert out[0] == "r1"
    assert set(out) == {"r1", "p1", "p2"}


# ---------------- Fusion:RrfFusion 单元 ----------------

def test_rrf_single_list_identity():
    assert RrfFusion().fuse([["a", "b", "c"]]) == ["a", "b", "c"]


def test_rrf_empty_inputs():
    assert RrfFusion().fuse([]) == []
    assert RrfFusion().fuse([[], []]) == []       # 全空路 → 空


def test_rrf_drops_empty_lists_before_count():
    # 一空一非空 → 视为单路恒等(空路被剔除,不参与计数)。
    assert RrfFusion().fuse([[], ["a", "b"]]) == ["a", "b"]


def test_rrf_three_way_consensus_ranks_first():
    # a 在三路都靠前 → 总分最高排第一;通用 N 路 RRF。
    k = 60
    fused = RrfFusion(k=k).fuse([
        ["a", "b", "c"],
        ["b", "a", "d"],
        ["a", "c", "e"],
    ])
    assert fused[0] == "a"
    # 与公式 Σ 1/(k+rank+1) 一致性核对:a 出现在 rank 0/1/0
    expect_a = 1 / (k + 0 + 1) + 1 / (k + 1 + 1) + 1 / (k + 0 + 1)
    expect_b = 1 / (k + 1 + 1) + 1 / (k + 0 + 1)  # b 在 list1 rank1 / list2 rank0
    assert expect_a > expect_b                      # a 应压 b(与排序一致)
    assert fused.index("a") < fused.index("b")


def test_rrf_two_way_matches_formula():
    # 2 路非对称:b 在两路都比 a 靠前(list1 b@0/a@1, list2 b@0/a@2)→ b 排第一(对齐 rrf_fuse 公式)。
    fused = RrfFusion().fuse([["b", "a"], ["b", "c", "a"]])
    assert fused[0] == "b"
    assert fused.index("b") < fused.index("a")


def test_rrf_ties_keep_first_seen_order():
    # 完全对称两路 → 全员同分 → 保首见序(稳定契约)。
    fused = RrfFusion().fuse([["x", "y"], ["y", "x"]])
    # x 首见于 list1 head, y 首见于 list1 tail;同分 → x 在前
    assert fused == ["x", "y"]


# ---------------- registry:未覆盖盲区 ----------------

def test_registry_unknown_fusion_falls_back_to_rrf():
    from codev_platform.agent.recall.registry import build_recall_service
    svc = build_recall_service(
        {"memory": {"recall": {"fusion": "bogus_fusion"}}}, _Store({}))
    assert svc._fusion.name == "rrf"


def test_registry_unknown_rerank_falls_back_to_none():
    from codev_platform.agent.recall.registry import build_recall_service
    svc = build_recall_service(
        {"memory": {"recall": {"rerank": "bogus_rerank"}}}, _Store({}))
    assert svc._reranker.name == "none"


def test_registry_rerank_top_k_passthrough():
    from codev_platform.agent.recall.registry import build_recall_service
    svc = build_recall_service(
        {"memory": {"recall": {"rerank_top_k": 7}}}, _Store({}))
    assert svc._rerank_top_k == 7
