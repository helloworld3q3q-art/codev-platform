"""QwenLocalEmbedder —— 本地 sentence-transformers Qwen3-Embedding(从 memory_vector_chroma 迁来)。

device 可配(默认 cpu,避免与 chroma daemon 抢 GPU → OOM)。lazy load(构造不加载,首次 encode 才加载),
故 build_embedder 装它零成本、可测。未来共享 GPU 走 `remote` adapter(调 chroma daemon embed RPC),与本档平级可换。
"""
from __future__ import annotations

from codev_platform.agent.memory_vector import Embedder


class QwenLocalEmbedder(Embedder):
    def __init__(self, model_path: str, device: str = "cpu") -> None:
        self._model_path = model_path
        self._device = device
        self._model = None

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_path, device=self._device)
        return self._model

    def encode(self, text: str) -> list[float]:
        vec = self._ensure().encode([text], normalize_embeddings=True)[0]
        return vec.tolist()

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # sentence-transformers 原生分批 (默认 batch_size=32) → 一次调用吃整批。
        arr = self._ensure().encode(texts, normalize_embeddings=True)
        return [v.tolist() for v in arr]
