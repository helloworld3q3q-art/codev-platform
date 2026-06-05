"""PipelineRecallService —— 唯一对外召回实现:固定不变量 + 三段可插拔(Scorer→Fusion→Reranker)。

不变量(插件碰不到,安全/正确性):
  visible_scopes(ACL) → list_scope(PG) → resolve_conflicts(去重) → redline 永置顶 → top-N 截断
可插拔(config 决定):Scorer(s) → Fusion → Reranker。

行为与 B1 的 LocalRecallService/VectorRecallService 等价(后两者现为本类的薄 shim,见 recall_service.py):
- scorers=[KeywordScorer]            ≡ 旧 LocalRecallService
- scorers=[VectorScorer, KeywordScorer] ≡ 旧 VectorRecallService(RRF(向量, 关键词))
"""
from __future__ import annotations

from codev_platform.agent.memory_recall import DEFAULT_POLICY, resolve_conflicts
from codev_platform.agent.memory_store import MemoryEntry, MemoryStore
from codev_platform.agent.recall.base import (
    Fusion, RankCtx, Reranker, RecallService, Scorer, _rank_for_query, visible_scopes,
)
from codev_platform.core.rbac import compute_visible_scopes


class PipelineRecallService(RecallService):
    def __init__(self, store: MemoryStore, *, scorers: list[Scorer], fusion: Fusion,
                 reranker: Reranker, default_policy: str = DEFAULT_POLICY,
                 per_scope_limit: int = 50, rbac_store=None, rerank_top_k: int = 20) -> None:
        self._store = store
        self._scorers = scorers
        self._fusion = fusion
        self._reranker = reranker
        self._policy = default_policy
        self._per_scope_limit = per_scope_limit
        self._rbac_store = rbac_store
        self._rerank_top_k = rerank_top_k

    # ---- 不变量:可见作用域(ACL)----
    def _visible_scopes(self, org_id, user_id, project_id) -> list[tuple[str, str]]:
        if self._rbac_store is not None and user_id:
            m = self._rbac_store.fetch_membership(org_id, user_id, project_id)
            return compute_visible_scopes(org_id, user_id, project_id, m)
        return visible_scopes(org_id, user_id, project_id)

    def recall(self, *, org_id: str, user_id: str | None, project_id: str | None,
               query: str = "", limit: int = 8, policy: str | None = None,
               task_id: str | None = None) -> list[MemoryEntry]:
        policy = policy or self._policy
        scopes = self._visible_scopes(org_id, user_id, project_id)
        pooled: list[MemoryEntry] = []
        for scope, ref in scopes:                                   # 不变量:跨可见作用域拉候选
            pooled.extend(self._store.list_scope(scope, ref, org_id=org_id, limit=self._per_scope_limit))
        resolved = resolve_conflicts(pooled, policy=policy)         # 不变量:冲突消解(去重)

        if not query.strip():                                       # 空 query 无信号 → 关键词/recency
            return _rank_for_query(resolved, query, task_id=task_id)[:limit]

        redlines = [e for e in resolved if e.is_redline]            # 不变量:redline 拆出永置顶
        rest = [e for e in resolved if not e.is_redline]
        by_id = {e.id: e for e in rest}
        ranked = self._rank_rest(rest, by_id, query, org_id, scopes, task_id, limit)
        # 不变量:redline 置顶 + 截断
        return (_rank_for_query(redlines, query, task_id=task_id) + ranked)[:limit]

    def _rank_rest(self, rest, by_id, query, org_id, scopes, task_id, limit) -> list[MemoryEntry]:
        """非 redline 候选:各 Scorer 排序 → Fusion 融合 → 关键词补漏 → Reranker。各 Scorer 异常→空。"""
        ctx = RankCtx(org_id=org_id, scopes=scopes, task_id=task_id, k=max(limit * 4, 20))
        lists: list[list[str]] = []
        for s in self._scorers:
            try:
                lists.append([i for i in s.rank(rest, query, ctx) if i in by_id])
            except Exception:  # noqa: BLE001 — 单档失败不拖垮召回(降级:其它档/关键词兜底)
                lists.append([])
        if not any(lists):                                          # 全档空(无依赖/全异常)→ 关键词兜底
            return _rank_for_query(rest, query, task_id=task_id)
        fused = self._fusion.fuse(lists)
        ordered = [by_id[i] for i in fused if i in by_id]
        # 补漏:Fusion 未覆盖的(向量/关键词都没召回)按关键词序补到末尾,不丢候选
        kw_ids = [e.id for e in _rank_for_query(rest, query, task_id=task_id)]
        seen = {e.id for e in ordered}
        ordered.extend(by_id[i] for i in kw_ids if i not in seen)
        return self._reranker.rerank(query, ordered, self._rerank_top_k)
