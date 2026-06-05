"""嵌入模型 registry —— config `memory.embed.backend` 选 backend,零 if-else;缺依赖 → None 降级。

加新模型 = `register_embedder("<name>", factory)` 一行 + 一个 adapter;不动召回 / 向量索引。
device 解析:`memory.embed.device` > `memory.embed_device`(旧别名) > "cpu"。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from codev_platform.agent.memory_vector import Embedder

_log = logging.getLogger(__name__)

# name → (cfg) -> Embedder | None(None = 依赖不满足,降级)
_EMBEDDERS: dict[str, Callable[[dict], Embedder | None]] = {}


def register_embedder(name: str, factory: Callable[[dict], Embedder | None]) -> None:
    _EMBEDDERS[name] = factory


def build_embedder(cfg: dict) -> Embedder | None:
    """按 config 建 Embedder;未知 backend / 依赖缺 → None(调用方据此降级:无向量召回)。"""
    from codev_platform.core.config import get as _get
    backend = _get(cfg, "memory.embed.backend", "qwen-local")
    factory = _EMBEDDERS.get(backend)
    if factory is None:
        _log.warning("[embed] 未知 backend %r(可用:%s),向量嵌入不可用", backend, list(_EMBEDDERS))
        return None
    return factory(cfg)


def _embed_device(cfg: dict) -> str:
    from codev_platform.core.config import get as _get
    return _get(cfg, "memory.embed.device") or _get(cfg, "memory.embed_device", "cpu")


def _build_qwen_local(cfg: dict) -> Embedder | None:
    import importlib.util
    if importlib.util.find_spec("sentence_transformers") is None:
        _log.warning("[embed] sentence-transformers 缺,qwen-local 不可用")
        return None
    from codev_platform.core.config import get as _get
    from codev_platform.agent.embed.qwen import QwenLocalEmbedder
    path = _get(cfg, "models.embed_path", str(Path.home() / "models" / "Qwen3-Embedding-0.6B"))
    return QwenLocalEmbedder(path, _embed_device(cfg))


register_embedder("qwen-local", _build_qwen_local)
