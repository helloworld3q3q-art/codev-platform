"""重排实现 —— NoReranker(默认恒等)。

QwenReranker(神经精排)是后续阶段,优先走"复用 chroma daemon 已加载模型的 RPC",避免再 load
第二个 GPU 模型(见 mcp/GPU OOM 教训)。加它 = 新增一个类 + registry 一行,不动 service。
"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.agent.recall.base import Reranker


class NoReranker(Reranker):
    """不精排:原样返回(召回默认档)。"""
    name = "none"

    def rerank(self, query: str, entries: list[MemoryEntry], top_k: int) -> list[MemoryEntry]:
        return entries
