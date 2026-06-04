"""记忆召回服务(memory M3)。

把"分层记忆"按当前请求上下文 (org, user, project) 召回成一组可注入 prompt 的条目:

    visible_scopes(org,user,project)  →  跨作用域 list_scope  →  resolve_conflicts(policy)
        →  按 query 轻量排序(redline 优先)  →  top-N

接缝(memory plan §3.9 "留接缝不预建分布式"):
  上层(ChatService)只依赖 `RecallService` 抽象。
  - 现在:`LocalRecallService` 直查 PG —— 结构化召回 + 确定性冲突消解。记忆量小时,
    "拉全可见作用域 active + 去冲突 + top-N" 已足够,语义排序零边际价值。
  - 未来:记忆量大到需语义排序时,加 `VectorRecallService`(chroma 向量召回),
    config `memory.recall_backend="vector"` 切换,上层零改。本步**不预建向量**
    (无数据=负 ROI,同读写分离副本的取舍)。

权限(memory plan §3.4):M5 前无 ACL —— `visible_scopes` 返回 org + 当前 project + 个人,
全部可见。team 暂略(无 team 数据)。M5 起 `visible_scopes` 改为按 org_members /
team_members / project_access 真实计算,recall 调用点不变。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from codev_platform.agent.memory_recall import DEFAULT_POLICY, resolve_conflicts
from codev_platform.agent.memory_store import MemoryEntry, MemoryStore
from codev_platform.core.rbac import compute_visible_scopes


def visible_scopes(org_id: str, user_id: str | None, project_id: str | None) -> list[tuple[str, str]]:
    """当前请求上下文下,该 user 可被 recall 的 (scope, scope_ref) 集合。

    无 ACL 期(M5 前):org(scope_ref 固定 'org')+ 当前 project + 个人。
    team 暂略(无 team 数据);M5 起这里换成查 org_members/team_members/project_access。
    """
    scopes: list[tuple[str, str]] = [("org", "org")]
    if project_id:
        scopes.append(("project", project_id))
    if user_id:
        scopes.append(("personal", user_id))
    return scopes


def _score(entry: MemoryEntry, terms: list[str]) -> int:
    """query 词在 content 里的命中数(轻量关键词相关性)。

    仅做"有 query 时把相关的往前提"的弱排序;语义排序是 VectorRecallService 的事(接缝)。
    中文不分词,靠子串命中 + redline 优先 + recency(list_scope 已按 created_at DESC)兜底。
    """
    if not terms:
        return 0
    c = entry.content.lower()
    return sum(1 for t in terms if t in c)


def _rank_for_query(entries: list[MemoryEntry], query: str,
                    task_id: str | None = None) -> list[MemoryEntry]:
    """排序优先级:redline(组织硬约束必须让模型看到)→ 当前 task → query 命中 → recency(稳定排序)。

    M1:task 匹配并入 key 元组(而非另起一次 sorted), 确保 redline 仍压过 task ——
    红线优先是硬不变量, 不能被任务加权破坏。
    """
    terms = [t for t in query.lower().split() if t]
    return sorted(entries, key=lambda e: (
        0 if e.is_redline else 1,
        0 if (task_id and getattr(e, "task_id", None) == task_id) else 1,
        -_score(e, terms),
    ))


class RecallService(ABC):
    """召回抽象。上层只依赖它;本地直查 / 未来向量 / 未来远端微服务都实现它(plan §3.9)。"""

    @abstractmethod
    def recall(self, *, org_id: str, user_id: str | None, project_id: str | None,
               query: str = "", limit: int = 8, policy: str | None = None,
               task_id: str | None = None) -> list[MemoryEntry]:
        ...


class LocalRecallService(RecallService):
    """直查 PG 的本地实现:跨可见作用域取 active → 冲突消解 → query 排序 → top-N。"""

    def __init__(self, store: MemoryStore, *, default_policy: str = DEFAULT_POLICY,
                 per_scope_limit: int = 50, rbac_store=None) -> None:
        self._store = store
        self._policy = default_policy
        self._per_scope_limit = per_scope_limit
        self._rbac_store = rbac_store

    def _visible_scopes(self, org_id: str, user_id: str | None,
                        project_id: str | None) -> list[tuple[str, str]]:
        """该 user 可 recall 的 (scope, scope_ref) 集合。

        M5:rbac_store 非 None → 查真实 Membership 经 compute_visible_scopes(org_members /
        team_members / project_access 真实角色)。否则回退模块级 `visible_scopes` 桩
        (无 ACL 期,返回 org+project+personal,现有行为不变)。recall() 调用点不变(plan §3.9)。
        """
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
        for scope, ref in scopes:
            pooled.extend(self._store.list_scope(scope, ref, org_id=org_id, limit=self._per_scope_limit))
        resolved = resolve_conflicts(pooled, policy=policy)
        # 排序层抽成 _rank 钩子: Local 用关键词, Vector(B1)覆写为 RRF(关键词∪向量), 前置流程共享。
        return self._rank(resolved, query=query, task_id=task_id, limit=limit,
                          org_id=org_id, scopes=scopes)

    def _rank(self, resolved: list[MemoryEntry], *, query: str, task_id: str | None,
              limit: int, org_id: str, scopes: list[tuple[str, str]]) -> list[MemoryEntry]:
        # M1: task_id 并入排序 key(redline > task > query 命中), redline 不变量不被破坏。
        return _rank_for_query(resolved, query, task_id=task_id)[:limit]


class VectorRecallService(LocalRecallService):
    """语义召回(B1 M3)。前置流程(visible_scopes → list_scope → resolve_conflicts)完全复用
    Local;**只覆写排序层**: redline 仍永置顶(不参与 RRF), 非 redline 候选走 RRF(关键词 ∪ 向量)。

    向量候选由 `MemoryVectorIndex.query_ids`(已按 org_id + 可见作用域过滤)给出, 与关键词序经
    `chroma.bm25.rrf_fuse` 融合 —— 关键词∪向量都能命中的排前, 单边命中的次之, 都没命中的按关键词
    序兜底补齐。空 query 无语义信号 → 退回 Local 关键词/recency 排序(向量在空 query 退化为纯
    recency, 无增益, 见 design §九)。
    """

    def __init__(self, store: MemoryStore, index, *, default_policy: str = DEFAULT_POLICY,
                 per_scope_limit: int = 50, rbac_store=None, rrf_k: int = 60) -> None:
        super().__init__(store, default_policy=default_policy,
                         per_scope_limit=per_scope_limit, rbac_store=rbac_store)
        self._index = index
        self._rrf_k = rrf_k

    def _rank(self, resolved: list[MemoryEntry], *, query: str, task_id: str | None,
              limit: int, org_id: str, scopes: list[tuple[str, str]]) -> list[MemoryEntry]:
        if not query.strip():
            # 空 query: 向量无信号, 退回关键词/recency(redline 仍置顶)。
            return _rank_for_query(resolved, query, task_id=task_id)[:limit]

        # redline 永置顶, 不参与 RRF(组织硬约束不可被语义相似度挤下去)。
        redlines = [e for e in resolved if e.is_redline]
        rest = [e for e in resolved if not e.is_redline]
        by_id = {e.id: e for e in rest}

        # 关键词序(task 匹配优先 → query 子串命中数), 复用 _rank_for_query 的口径但只对非 redline。
        kw_ids = [e.id for e in _rank_for_query(rest, query, task_id=task_id)]
        # 向量序: 只保留落在当前候选池内的 id(向量库可能含已被冲突消解压掉的条)。
        try:
            vec_raw = self._index.query_ids(query, org_id=org_id, scopes=scopes,
                                            k=max(limit * 4, 20))
        except Exception:  # noqa: BLE001 — 向量库不可用 → 退回纯关键词, 不让召回崩
            return _rank_for_query(resolved, query, task_id=task_id)[:limit]
        vec_ids = [i for i in vec_raw if i in by_id]

        from codev_platform.chroma.bm25 import rrf_fuse  # lazy: 不为 Local 用户拉 jieba/rank_bm25
        fused = rrf_fuse(vec_ids, kw_ids, k_const=self._rrf_k)
        ordered: list[MemoryEntry] = [by_id[i] for i, _ in fused if i in by_id]
        # 兜底补漏: RRF 未覆盖的(向量/关键词都没召回)按关键词序补到末尾, 不丢候选。
        seen = {e.id for e in ordered}
        ordered.extend(by_id[i] for i in kw_ids if i not in seen)
        # redline 仍按既有优先级置顶(_rank_for_query 内部 redline 一致), 再接 RRF 序。
        return (_rank_for_query(redlines, query, task_id=task_id) + ordered)[:limit]
