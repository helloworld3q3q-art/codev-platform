"""agent memory 向量召回接缝(B1)—— Embedder + 向量索引抽象。

按 agent-provider-architecture(策略接口 + 中性类型): 上层 `VectorRecallService` 只依赖这里的
抽象, 真实实现(chroma collection `<pid>__agent_memory` + Qwen/CPU embed)在后续步接, 换实现零改
召回逻辑。**GPU footprint 控制**(嵌入走 CPU / 复用 daemon, 不在 agent-memory 进程再 load 一份
GPU 模型)是实现细节, 封在 Index/Embedder 实现里, 不泄漏到 recall 排序逻辑。

设计铁律(AI 专家): 向量**只替换排序层**。`visible_scopes + resolve_conflicts + redline-first`
仍是前置(在 VectorRecallService 里保留), 向量只对"非 redline 候选"参与 RRF(关键词 ∪ 向量)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from codev_platform.agent.memory_store import MemoryEntry


class Embedder(ABC):
    """文本 → 向量。真实实现复用 chroma 的 Qwen3-Embedding(query prompt + normalize),
    device 由实现选(agent memory 低频, 可走 CPU 避免与 chroma daemon 抢 GPU)。"""

    @abstractmethod
    def encode(self, text: str) -> list[float]:
        ...


class RerankModel(ABC):
    """(query, docs) → 每 doc 的相关性分(越高越相关)。reranker 精排用。
    真实实现复用 chroma 的 Qwen3-Reranker(优先 remote 走 daemon /rerank 复用 GPU 那份)。"""

    @abstractmethod
    def score(self, query: str, docs: list[str]) -> list[float]:
        ...


class MemoryVectorIndex(ABC):
    """agent memory 的独立向量索引(chroma collection `<pid>__agent_memory`,与 platform_docs 隔离)。

    写时增量 embed(`upsert`/`delete` 由写路径调), recall 时 `query_ids` 返回按语义相似度降序的
    entry_id —— **必须按 org_id + 可见作用域过滤**(向量库不能成为绕过 ACL 的旁路)。
    """

    @abstractmethod
    def upsert(self, entry: MemoryEntry) -> None:
        """写一条记忆的向量(content embed + 元数据 org_id/scope/scope_ref/is_redline)。"""
        ...

    @abstractmethod
    def delete(self, entry_id: str, *, org_id: str) -> None:
        """删一条记忆的向量(forget/supersede 时同步,防召回到已失效条)。
        org_id 定位 per-org collection(`<org_id>__agent_memory`)。"""
        ...

    @abstractmethod
    def query_ids(self, query: str, *, org_id: str,
                  scopes: list[tuple[str, str]], k: int) -> list[str]:
        """语义检索: 返回 entry_id 列表(相似度降序), 已按 org_id + scopes 过滤。

        scopes = [(scope, scope_ref), ...] 当前 user 的可见作用域(由 recall 算好传入);
        实现据此构造向量库 where 过滤, 杜绝跨 org / 跨作用域泄漏。
        """
        ...
