"""可插拔召回流水线 —— registry 装配 + 降级 + 别名(P0 新架构覆盖;行为等价由旧测试守)。"""
from __future__ import annotations

from codev_platform.agent.recall.registry import build_recall_service


class _Store:
    def list_scope(self, scope, scope_ref, org_id="default", limit=100):
        return []


class _Idx:
    def upsert(self, e):
        pass

    def delete(self, entry_id, *, org_id):
        pass

    def query_ids(self, query, *, org_id, scopes, k):
        return []


def _names(svc):
    return [s.name for s in svc._scorers]


# ---- 别名(recall_backend → scorers)----

def test_backend_local_alias():
    svc = build_recall_service({"memory": {"recall_backend": "local"}}, _Store())
    assert _names(svc) == ["keyword"]


def test_backend_vector_alias_with_index():
    svc = build_recall_service({"memory": {"recall_backend": "vector"}}, _Store(), index=_Idx())
    assert _names(svc) == ["vector", "keyword"]


def test_default_no_config_is_keyword():
    svc = build_recall_service({}, _Store())
    assert _names(svc) == ["keyword"]
    assert svc._fusion.name == "rrf" and svc._reranker.name == "none"


# ---- 显式 scorers 列表 ----

def test_explicit_scorers_order_preserved():
    svc = build_recall_service(
        {"memory": {"recall": {"scorers": ["keyword", "vector"]}}}, _Store(), index=_Idx())
    assert _names(svc) == ["keyword", "vector"]


# ---- 降级(原则 #5)----

def test_vector_degrades_to_keyword_without_index():
    # backend=vector 但无 index → vector 档剔除, 退 keyword 地板(不崩)
    svc = build_recall_service({"memory": {"recall_backend": "vector"}}, _Store(), index=None)
    assert _names(svc) == ["keyword"]


def test_unknown_scorer_skipped_floor_kept():
    # 未知 scorer 名剔除; 剔空 → 强制补 keyword 地板
    svc = build_recall_service({"memory": {"recall": {"scorers": ["bogus", "alsobad"]}}}, _Store())
    assert _names(svc) == ["keyword"]


def test_vector_only_config_degrades_to_keyword_floor():
    # 只配 vector 但无 index → 剔空 → keyword 地板(永不空)
    svc = build_recall_service({"memory": {"recall": {"scorers": ["vector"]}}}, _Store(), index=None)
    assert _names(svc) == ["keyword"]


# ---- store None ----

def test_store_none_returns_none():
    assert build_recall_service({}, None) is None


# ---- config 参数透传 ----

def test_rrf_k_and_policy_from_config():
    svc = build_recall_service(
        {"memory": {"recall": {"rrf_k": 42}, "conflict_policy": "org_first"}}, _Store())
    assert svc._fusion._k == 42 and svc._policy == "org_first"
