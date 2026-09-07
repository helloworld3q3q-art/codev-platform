"""嵌入模型 registry —— config `memory.embed.backend` 选 backend,零 if-else;缺依赖 → None 降级。

加新模型 = `register_embedder("<name>", factory)` 一行 + 一个 adapter;不动召回 / 向量索引。
device 解析:`memory.embed.device` > `memory.embed_device`(旧别名) > "cpu"。
"""
from __future__ import annotations

import logging
import os
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


def _code_vec_remote_defaults(cfg: dict) -> tuple[int, float]:
    """code_vec remote 的批量/超时默认值。

    GPU daemon 保持原吞吐默认(64/30s)；CPU daemon 明确降到 2/180s，避免单请求过大导致
    code_vec 重建反复 timeout。配置项仍可覆盖，方便专用索引节点按机器规格调优。
    """
    from codev_platform.core.config import get as _get
    cpu_mode = code_vec_embedding_device(cfg) == "cpu"
    # 短样本中 4 条合批可能更快，但真实混合代码批次会显著放大长尾并触发请求重试；
    # 2 条以稳定的持续吞吐为先。每次调用仍受超时保护，机器差异可由配置显式覆盖。
    batch_default = 2 if cpu_mode else 64
    timeout_default = 180.0 if cpu_mode else _remote_timeout(cfg)
    batch = int(_get(cfg, "recall.code_vec.remote_embed_batch", batch_default))
    timeout = float(_get(cfg, "recall.code_vec.remote_embed_timeout", timeout_default))
    return batch, timeout


def code_vec_embedding_device(cfg: dict) -> str:
    """解析 code_vec 实际设备策略；qwen-local 与 remote 各自读取自己的真值源。"""
    from codev_platform.core.config import get as _get

    backend = str(_get(cfg, "recall.code_vec.embed_backend", "remote") or "remote")
    if backend == "qwen-local":
        return str(_get(cfg, "recall.code_vec.embed_device") or _cuda_or_cpu()).lower()
    return str(
        os.environ.get("PLATFORM_EMBED_DEVICE")
        or _get(cfg, "models.embed_device", "")
        or ""
    ).lower()


def code_vec_embedding_max_seq_length(cfg: dict) -> int:
    """按 code_vec 实际设备解析统一序列上限，供构建器与 checkpoint 指纹复用。"""
    from codev_platform.chroma.embedding_limits import resolve_embed_max_seq_length
    from codev_platform.core.config import get as _get

    configured = os.environ.get("PLATFORM_EMBED_MAX_SEQ_LENGTH")
    if configured is None:
        configured = _get(cfg, "models.embed_max_seq_length", None)
    return resolve_embed_max_seq_length(code_vec_embedding_device(cfg), configured)


def _build_code_vec_remote(cfg: dict) -> Embedder:
    from codev_platform.core.config import get as _get
    from codev_platform.agent.embed.remote import RemoteEmbedder
    batch, timeout = _code_vec_remote_defaults(cfg)
    url = _get(cfg, "memory.embed.url") or f"http://127.0.0.1:{_chroma_base_port(cfg)}/embed"
    return RemoteEmbedder(
        url,
        timeout=timeout,
        token=_get(cfg, "memory.embed.token"),
        internal_call=_internal_call_secret(cfg),
        batch_size=batch,
    )


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
        return _build_code_vec_remote(cfg)
    if backend != "qwen-local":
        _log.warning("[code_vec] 未知 embed_backend %r(可用: remote, qwen-local)", backend)
        return None
    import importlib.util
    if importlib.util.find_spec("sentence_transformers") is None:
        _log.warning("[code_vec] sentence-transformers 缺, 本机 embedder 不可用")
        return None
    from codev_platform.agent.embed.qwen import QwenLocalEmbedder
    path = _get(cfg, "models.embed_path", str(Path.home() / "models" / "Qwen3-Embedding-0.6B"))
    device = code_vec_embedding_device(cfg)
    # encode batch 与 chroma daemon /embed 共用同一 config 键(models.embed_encode_batch), 单一真值源:
    # 小显存(8GB)调小防 fp32 激活峰值 OOM, 不降精度。默认 8(对 8GB 卡保守, 大显存可在 config 调大)。
    batch = int(_get(cfg, "models.embed_encode_batch", 8))
    return QwenLocalEmbedder(
        path,
        device,
        batch_size=batch,
        max_seq_length=code_vec_embedding_max_seq_length(cfg),
    )
