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

_DEFAULT_ORG = "default"


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


def _rank_for_query(entries: list[MemoryEntry], query: str) -> list[MemoryEntry]:
    """redline 永远最前(组织硬约束必须让模型看到),其次按 query 命中数,稳定排序保 recency。"""
    terms = [t for t in query.lower().split() if t]
    return sorted(entries, key=lambda e: (0 if e.is_redline else 1, -_score(e, terms)))


class RecallService(ABC):
    """召回抽象。上层只依赖它;本地直查 / 未来向量 / 未来远端微服务都实现它(plan §3.9)。"""

    @abstractmethod
    def recall(self, *, org_id: str, user_id: str | None, project_id: str | None,
               query: str = "", limit: int = 8, policy: str | None = None) -> list[MemoryEntry]:
        ...


class LocalRecallService(RecallService):
    """直查 PG 的本地实现:跨可见作用域取 active → 冲突消解 → query 排序 → top-N。"""

    def __init__(self, store: MemoryStore, *, default_policy: str = DEFAULT_POLICY,
                 per_scope_limit: int = 50) -> None:
        self._store = store
        self._policy = default_policy
        self._per_scope_limit = per_scope_limit

    def recall(self, *, org_id: str, user_id: str | None, project_id: str | None,
               query: str = "", limit: int = 8, policy: str | None = None) -> list[MemoryEntry]:
        policy = policy or self._policy
        pooled: list[MemoryEntry] = []
        for scope, ref in visible_scopes(org_id, user_id, project_id):
            pooled.extend(self._store.list_scope(scope, ref, org_id=org_id, limit=self._per_scope_limit))
        resolved = resolve_conflicts(pooled, policy=policy)
        return _rank_for_query(resolved, query)[:limit]
