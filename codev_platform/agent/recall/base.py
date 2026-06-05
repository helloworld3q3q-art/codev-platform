"""召回抽象 + 纯 helper(从 recall_service 迁来,单向无环:recall/ 不 import recall_service)。

固定不变量(ACL / 去重 / redline / 截断)由 PipelineRecallService 持有;本文件只放抽象 + 纯函数。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.core.rbac import compute_visible_scopes


def visible_scopes(org_id: str, user_id: str | None, project_id: str | None) -> list[tuple[str, str]]:
    """无 ACL 期(M5 前)桩:org + 当前 project + 个人。rbac_store 非 None 时 service 改走真实 Membership。"""
    scopes: list[tuple[str, str]] = [("org", "org")]
    if project_id:
        scopes.append(("project", project_id))
    if user_id:
        scopes.append(("personal", user_id))
    return scopes


def _score(entry: MemoryEntry, terms: list[str]) -> int:
    """query 词在 content 的子串命中数(轻量关键词相关性;中文不分词)。"""
    if not terms:
        return 0
    c = entry.content.lower()
    return sum(1 for t in terms if t in c)


def _rank_for_query(entries: list[MemoryEntry], query: str,
                    task_id: str | None = None) -> list[MemoryEntry]:
    """排序:redline → 当前 task → query 子串命中 → recency(稳定排序)。redline 优先是硬不变量。"""
    terms = [t for t in query.lower().split() if t]
    return sorted(entries, key=lambda e: (
        0 if e.is_redline else 1,
        0 if (task_id and getattr(e, "task_id", None) == task_id) else 1,
        -_score(e, terms),
    ))


@dataclass
class RankCtx:
    """传给 Scorer 的请求上下文(org / 可见作用域 / task / 向量 top-k)。"""
    org_id: str
    scopes: list[tuple[str, str]] = field(default_factory=list)
    task_id: str | None = None
    k: int = 20


class Scorer(ABC):
    """打分器:对候选 entries 按 query 排序,返回 id 序(降序)。可插拔(keyword / bm25 / vector ...)。"""
    name: str = "scorer"

    @abstractmethod
    def rank(self, entries: list[MemoryEntry], query: str, ctx: RankCtx) -> list[str]:
        ...


class Fusion(ABC):
    """融合:多路 id 序 → 一路。可插拔(rrf / weighted ...)。"""
    name: str = "fusion"

    @abstractmethod
    def fuse(self, ranked_lists: list[list[str]]) -> list[str]:
        ...


class Reranker(ABC):
    """可选后置精排:query + 有序 entries → 重排后的 entries。可插拔(none / qwen ...)。"""
    name: str = "reranker"

    @abstractmethod
    def rerank(self, query: str, entries: list[MemoryEntry], top_k: int) -> list[MemoryEntry]:
        ...


class RecallService(ABC):
    """召回抽象(对外稳定签名)。上层(ChatService / memory MCP)只依赖它。"""

    @abstractmethod
    def recall(self, *, org_id: str, user_id: str | None, project_id: str | None,
               query: str = "", limit: int = 8, policy: str | None = None,
               task_id: str | None = None) -> list[MemoryEntry]:
        ...
