"""ChromaMemoryVectorIndex —— MemoryVectorIndex 的 chroma 实现(B1 step2)。

- **collection 按 org 一库**: `chroma_collection_name(org_id, "agent_memory")` = `<org_id>__agent_memory`
  (与 platform_docs 文档向量隔离)。memory 是 org 中心(personal/org/project/team 同 PG 靠 org_id
  隔离),故向量库也按 org 切;personal recall 无 project 也落本 org 库可查。metadata 带
  org_id/scope/scope_ref/is_redline,query 时 where 过滤,**向量库不绕过 ACL**。
- **嵌入复用平台 Qwen3-Embedding,device 默认 cpu**(memory 低频写;避免与 chroma daemon 抢 8GB
  GPU → OOM,见 agent_tool_health GPU OOM 教训)。config `memory.embed_device` 可覆盖。
- chromadb PersistentClient 连 `chroma_dir()`(与 daemon 同 persist 目录,不同 collection)。
"""
from __future__ import annotations

import logging

from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.agent.memory_vector import Embedder, MemoryVectorIndex

_log = logging.getLogger(__name__)


def build_scope_where(org_id: str, scopes: list[tuple[str, str]]) -> dict:
    """构造 chroma metadata where: org_id 命中 且 (scope,scope_ref) ∈ 可见作用域(纯函数, 可测)。

    杜绝跨 org / 跨作用域召回(向量库不能成 ACL 旁路)。chroma where 语法: 单作用域直接 $and,
    多作用域用 $or 包多个 (scope ∧ scope_ref)。
    """
    clauses = [{"$and": [{"scope": s}, {"scope_ref": r}]} for s, r in scopes]
    if not clauses:
        return {"org_id": org_id}
    scope_filter = clauses[0] if len(clauses) == 1 else {"$or": clauses}
    return {"$and": [{"org_id": org_id}, scope_filter]}


class ChromaMemoryVectorIndex(MemoryVectorIndex):
    """per-org chroma collection 实现。collection 懒建并缓存(org_id -> collection)。"""

    def __init__(self, embedder: Embedder, persist_dir) -> None:
        self._embedder = embedder
        self._persist = str(persist_dir)
        self._client = None
        self._cols: dict[str, object] = {}

    def _col(self, org_id: str):
        col = self._cols.get(org_id)
        if col is not None:
            return col
        import chromadb

        from codev_platform.core.paths import chroma_collection_name
        if self._client is None:
            self._client = chromadb.PersistentClient(path=self._persist)
        name = chroma_collection_name(org_id, "agent_memory")
        col = self._client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})
        self._cols[org_id] = col
        return col

    def upsert(self, entry: MemoryEntry) -> None:
        vec = self._embedder.encode(entry.content)
        self._col(entry.org_id).upsert(
            ids=[entry.id], embeddings=[vec], documents=[entry.content],
            metadatas=[{
                "org_id": entry.org_id, "scope": entry.scope,
                "scope_ref": entry.scope_ref, "is_redline": bool(entry.is_redline),
            }],
        )

    def delete(self, entry_id: str, *, org_id: str) -> None:
        self._col(org_id).delete(ids=[entry_id])

    def query_ids(self, query: str, *, org_id: str,
                  scopes: list[tuple[str, str]], k: int) -> list[str]:
        qv = self._embedder.encode(query)
        res = self._col(org_id).query(
            query_embeddings=[qv], n_results=k, where=build_scope_where(org_id, scopes),
        )
        ids = res.get("ids") or [[]]
        return list(ids[0]) if ids else []


def build_memory_vector_index(cfg: dict):
    """按 config 建 ChromaMemoryVectorIndex;缺 chromadb 或嵌入模型不可用 → None(调用方退回 local)。

    嵌入模型经 embed.registry 按 `memory.embed.backend` 选(可换模型,零核心改);缺 backend/依赖 → None。
    """
    import importlib.util
    if importlib.util.find_spec("chromadb") is None:
        _log.warning("[agent.memory_vector] chromadb 缺,向量召回不可用,退回 local")
        return None
    from codev_platform.agent.embed.registry import build_embedder
    from codev_platform.core.paths import chroma_dir
    embedder = build_embedder(cfg)
    if embedder is None:
        _log.warning("[agent.memory_vector] 嵌入模型不可用(memory.embed.backend),向量召回退回 local")
        return None
    return ChromaMemoryVectorIndex(embedder, chroma_dir())
