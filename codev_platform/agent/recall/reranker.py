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


class QwenReranker(Reranker):
    """神经精排:对前 top_k 候选用 RerankModel((query,doc)→分)重排,top_k 之后原样接在后面。

    注:这里只精排"非 redline 段"——redline 早在 service 核心被拆出永置顶,reranker 拿不到(不变量)。
    模型打分失败 / 数量不匹配 → 不动序(降级,绝不崩、绝不丢候选)。模型默认走 remote(复用 chroma
    daemon 那份 GPU reranker,不新增实例)。
    """
    name = "qwen"

    def __init__(self, model) -> None:
        self._model = model

    def rerank(self, query: str, entries: list[MemoryEntry], top_k: int) -> list[MemoryEntry]:
        if not entries or top_k <= 0:
            return entries
        head, tail = entries[:top_k], entries[top_k:]
        try:
            scores = self._model.score(query, [e.content for e in head])
        except Exception:  # noqa: BLE001 — 重排失败不动序(降级)
            return entries
        if not scores or len(scores) != len(head):
            return entries
        reordered = [e for e, _ in sorted(zip(head, scores, strict=True), key=lambda pair: -pair[1])]
        return reordered + tail
