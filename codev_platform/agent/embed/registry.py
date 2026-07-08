"""嵌入模型 registry —— config `memory.embed.backend` 选 backend,零 if-else;缺依赖 → None 降级。

加新模型 = `register_embedder("<name>", factory)` 一行 + 一个 adapter;不动召回 / 向量索引。
device 解析:`memory.embed.device` > `memory.embed_device`(旧别名) > "cpu"。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from codev_platform.agent.memory_vector import Embedder, RerankModel

_log = logging.getLogger(__name__)

# name → (cfg) -> Embedder | None(None = 依赖不满足,降级)
_EMBEDDERS: dict[str, Callable[[dict], Embedder | None]] = {}
_RERANK_MODELS: dict[str, Callable[[dict], RerankModel | None]] = {}


def register_embedder(name: str, factory: Callable[[dict], Embedder | None]) -> None:
    _EMBEDDERS[name] = factory


def register_rerank_model(name: str, factory: Callable[[dict], RerankModel | None]) -> None:
    _RERANK_MODELS[name] = factory


def build_rerank_model(cfg: dict) -> RerankModel | None:
    """按 config 建 RerankModel;未知 backend → None(调用方退 NoReranker)。默认 remote(共享 daemon)。"""
    from codev_platform.core.config import get as _get
    backend = _get(cfg, "memory.rerank_model.backend", "remote")
    factory = _RERANK_MODELS.get(backend)
    if factory is None:
        _log.warning("[rerank] 未知 backend %r(可用:%s)", backend, list(_RERANK_MODELS))
        return None
    return factory(cfg)


def _chroma_base_port(cfg: dict) -> int:
    """chroma daemon 端口(/embed /rerank 都在它上面)走端口统一的 _bind_port —— 认 canonical
    键 mcp.platform_docs_sse_port + daemon.port 别名,避免只配 canonical 时这里仍默认 18083。"""
    from codev_platform.mcp_serve import _bind_port
    return _bind_port(cfg, "chroma")


def _remote_timeout(cfg: dict) -> float:
    """远端嵌入/重排 HTTP 超时(秒),config 可调;默认 30.0。挂死的 daemon 不会无限阻塞。"""
    from codev_platform.core.config import get as _get
    return float(_get(cfg, "memory.embed.timeout", 30.0))


def _internal_call_secret(cfg: dict) -> str | None:
    """本机内部调用信物 = 平台单一 internal_secret(与 web→agent X-Identity 同源)。daemon 配了它时,
    /embed /rerank 的 loopback 豁免要求带此信物 → 同机反代转发的远程请求带不出, 防白嫖 GPU。未配 → None
    (单机 passthrough 维持纯 loopback 豁免不变)。"""
    from codev_platform.core.config import get as _get
    return _get(cfg, "agent.internal_secret") or None


def _build_remote_rerank(cfg: dict) -> RerankModel | None:
    from codev_platform.core.config import get as _get
    from codev_platform.agent.embed.remote import RemoteRerankModel
    url = _get(cfg, "memory.rerank_model.url") or f"http://127.0.0.1:{_chroma_base_port(cfg)}/rerank"
    return RemoteRerankModel(url, timeout=_remote_timeout(cfg), token=_get(cfg, "memory.embed.token"),
                             internal_call=_internal_call_secret(cfg))


register_rerank_model("remote", _build_remote_rerank)


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


def _build_remote(cfg: dict) -> Embedder | None:
    """共享 chroma daemon 嵌入:url = memory.embed.url > http://127.0.0.1:<chroma bind 口>/embed
    (bind 口走 _bind_port,认 canonical 键 + daemon.port 别名)。无依赖检查(urllib 自带);
    daemon 不可达在 encode 时降级。"""
    from codev_platform.core.config import get as _get
    from codev_platform.agent.embed.remote import RemoteEmbedder
    url = _get(cfg, "memory.embed.url") or f"http://127.0.0.1:{_chroma_base_port(cfg)}/embed"
    return RemoteEmbedder(url, timeout=_remote_timeout(cfg), token=_get(cfg, "memory.embed.token"),
                          internal_call=_internal_call_secret(cfg))


register_embedder("qwen-local", _build_qwen_local)
register_embedder("remote", _build_remote)


def _cuda_or_cpu() -> str:
    """有 CUDA 用 cuda, 否则 cpu。torch 缺也回 cpu(下游 sentence-transformers 自处理)。"""
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def build_code_vec_embedder(cfg: dict) -> Embedder | None:
    """code_vec 索引专用 embedder。

    默认 **remote** —— 复用共享 daemon 那份 GPU 模型, 不在 worker 里再 load 第二份。
    (曾因 daemon /embed 死锁短暂改默认本机; 死锁已由 server `_gpu_call` wait_for 根治 —— 卡死
    算子超时释放信号量, 不再永久死锁。故 remote 重新安全, 且: 单机 8GB 不必塞两份模型挤爆显存,
    云上无 GPU 的 worker 也只能走 remote → remote 是两端都成立的默认。)

    `recall.code_vec.embed_backend=qwen-local` 为**专用 GPU 索引节点**的 opt-in: 该节点不跑在线
    服务、GPU 独占, 本机直跑省 HTTP 更快, 与服务彻底隔离。device 默认自动 cuda/cpu。"""
    from codev_platform.core.config import get as _get
    backend = _get(cfg, "recall.code_vec.embed_backend", "remote")
    if backend == "remote":
        from codev_platform.agent.embed.remote_ready import ensure_code_vec_remote_daemon
        ensure_code_vec_remote_daemon(cfg)
        return _build_remote(cfg)
    if backend != "qwen-local":
        _log.warning("[code_vec] 未知 embed_backend %r(可用: remote, qwen-local)", backend)
        return None
    import importlib.util
    if importlib.util.find_spec("sentence_transformers") is None:
        _log.warning("[code_vec] sentence-transformers 缺, 本机 embedder 不可用")
        return None
    from codev_platform.agent.embed.qwen import QwenLocalEmbedder
    path = _get(cfg, "models.embed_path", str(Path.home() / "models" / "Qwen3-Embedding-0.6B"))
    device = _get(cfg, "recall.code_vec.embed_device") or _cuda_or_cpu()
    # encode batch 与 chroma daemon /embed 共用同一 config 键(models.embed_encode_batch), 单一真值源:
    # 小显存(8GB)调小防 fp32 激活峰值 OOM, 不降精度。默认 8(对 8GB 卡保守, 大显存可在 config 调大)。
    batch = int(_get(cfg, "models.embed_encode_batch", 8))
    return QwenLocalEmbedder(path, device, batch_size=batch)
