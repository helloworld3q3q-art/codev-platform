"""QwenLocalEmbedder —— 本地 sentence-transformers Qwen3-Embedding(从 memory_vector_chroma 迁来)。

device 可配(默认 cpu,避免与 chroma daemon 抢 GPU → OOM)。lazy load(构造不加载,首次 encode 才加载),
故 build_embedder 装它零成本、可测。未来共享 GPU 走 `remote` adapter(调 chroma daemon embed RPC),与本档平级可换。
"""
from __future__ import annotations

from codev_platform.agent.memory_vector import Embedder


class QwenLocalEmbedder(Embedder):
    def __init__(self, model_path: str, device: str = "cpu", *, batch_size: int = 32) -> None:
        self._model_path = model_path
        self._device = device
        self._model = None   # lazy: 首次 encode 才 load(_ensure 据此判)。勿删——删了 _ensure 读它即 AttributeError。
        # encode 内部分批大小 = GPU 激活显存峰值上界。小显存(8GB)跑大项目 code_vec 时调小(8)
        # 把 fp32 激活峰值压进显存防 OOM, 不降模型精度。默认 32(ST 原生默认, 大显存/CPU 不受限)。
        self._batch_size = batch_size if batch_size and batch_size > 0 else 32

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
        # batch_size 收口激活峰值(见 __init__); 一次调用吃整批文本, ST 内部按 batch_size 滑窗。
        arr = self._ensure().encode(texts, normalize_embeddings=True, batch_size=self._batch_size)
        return [v.tolist() for v in arr]
