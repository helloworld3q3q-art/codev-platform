"""chroma daemon —— 配置常量叶子模块 (从 server.py 抽出, file-discipline §1 + 无环分层 DAG)。

只依赖 `core.*`, 不依赖 server / 任何 chroma 逻辑模块。所有逻辑模块 (server / _models /
_reranker / _tools / _http) 从这里拿常量 → 打破 reranker 等子系统对 server 的循环依赖
(见 docs/plans/roadmap-2026-06-01/refactor-largefile-errorcode-2026-06-01.md §A.2b)。

常量 import 期定死、从不 rebind → 各模块 `from ._config import X` 按值安全 (可变运行态在 _state)。
优先级: env var > ~/.codev-platform/config.json > 代码默认。
"""
from __future__ import annotations

import os
from pathlib import Path

from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.paths import chroma_collection_name, chroma_dir
from codev_platform.core.config import load_config, env_or_config
from codev_platform.core.obslog import logging_mode
from codev_platform.chroma.embedding_limits import resolve_embed_max_seq_length

# BM25 可用性 (jieba + rank_bm25 缺失时自动 disable)。仅取布尔; BM25Index 类 / rrf_fuse 由
# server / _tools 各自按需 guarded import (避免 _config 混入 bm25 实现)。
try:
    import codev_platform.chroma.bm25  # noqa: F401
    _BM25_IMPORT_OK = True
except ImportError as _bm25_imp_err:
    _BM25_IMPORT_OK = False
    _BM25_IMPORT_ERR = str(_bm25_imp_err)

# DATA_DIR 走 core.paths.chroma_dir() (基于业务项目 cwd, 非 __file__ 推导)。
DATA_DIR = chroma_dir()
# 构建戳路径: indexer 完成索引时写, server 每次 query 前 stat (mtime 变新只重连 collection)。
_STAMP_PATH = DATA_DIR / ".last_build.json"


def _resolve_optional_project_id() -> str | None:
    """解析 stdio 默认项目；多租户回退不在导入期输出。"""
    try:
        return resolve_local()
    except ProjectIdError:
        return None


# import 期 best-effort 解析单 project (stdio 默认); daemon 多租户按 ?project_id= 路由,
# 故解析失败不退出或输出，保持模块导入可组合且不污染受限子进程回执。
PROJECT_ID = _resolve_optional_project_id()
COLLECTION_BASE = "platform_docs"
# PROJECT_ID 为 None 时 (无默认 project) 不预生成 collection 名, 每请求带 project_id 时再算。
COLLECTION_NAME = chroma_collection_name(PROJECT_ID, COLLECTION_BASE) if PROJECT_ID else None
# backward compat: 旧索引存在 unprefixed `platform_docs`; daemon 启动若新命名 collection 不存在
# 自动 fallback 到旧名 (_load_project_state 内处理)。
LEGACY_COLLECTION_NAME = "platform_docs"

_CFG = load_config()
# dev (默认全量) / prod (脱敏) —— 见 core.obslog。模块加载时解析一次。
_LOG_MODE = logging_mode(_CFG)
EMBED_MODEL = str(Path(env_or_config("PLATFORM_EMBED_MODEL_PATH", _CFG, "models.embed_path")).expanduser().resolve())
EMBED_DEVICE = env_or_config("PLATFORM_EMBED_DEVICE", _CFG, "models.embed_device", "cuda")
# 单次 encode 的内部分批大小 = encode 激活显存峰值的上界(模型保持 fp32 不降精度)。小显存(8GB)
# 跑大项目 code_vec 时调小(8/16)把 fp32 的激活峰值压进显存防 OOM; 默认 8(对 8GB 卡保守安全)。
EMBED_ENCODE_BATCH = int(env_or_config("PLATFORM_EMBED_ENCODE_BATCH", _CFG, "models.embed_encode_batch", 8))
# Qwen3 模型原始上限是 32768；CPU 默认收紧到 512，GPU 默认沿用模型能力。
EMBED_MAX_SEQ_LENGTH_OVERRIDE = env_or_config(
    "PLATFORM_EMBED_MAX_SEQ_LENGTH", _CFG, "models.embed_max_seq_length", None,
)
EMBED_MAX_SEQ_LENGTH = resolve_embed_max_seq_length(
    EMBED_DEVICE, EMBED_MAX_SEQ_LENGTH_OVERRIDE,
)

# ---------- Reranker config (Qwen3-Reranker-0.6B chat-template + yes/no logits) ----------
RERANKER_MODEL = env_or_config("PLATFORM_RERANKER_MODEL_PATH", _CFG, "models.reranker_path", "")
RERANKER_DEVICE = env_or_config("PLATFORM_RERANKER_DEVICE", _CFG, "models.reranker_device", "cuda")
# 精度配置化: 消费卡 fp16 / Ampere+ 服务器卡 bf16 / 大显存或 CPU fp32。auto = cuda 系 fp16 否则 fp32。
RERANKER_DTYPE = env_or_config("PLATFORM_RERANKER_DTYPE", _CFG, "models.reranker_dtype", "auto")
_rer_enabled_raw = env_or_config("PLATFORM_RERANKER_ENABLED", _CFG, "models.reranker_enabled", True)
RERANKER_ENABLED = (
    _rer_enabled_raw.lower() in ("true", "1", "yes")
    if isinstance(_rer_enabled_raw, str) else bool(_rer_enabled_raw)
)
# 重排前从 Chroma 取多少候选 (rerank 后取 k 返回); 兼容旧 env 名 PLATFORM_RERANKER_TOP_K。
RERANKER_TOP_K = int(
    os.getenv("PLATFORM_SEARCH_RECALL_K") or os.getenv("PLATFORM_RERANKER_TOP_K")
    or env_or_config("", _CFG, "search.recall_k", 30)
)
DEFAULT_RETURN_K = int(env_or_config("PLATFORM_SEARCH_RETURN_K", _CFG, "search.return_k", 5))

# ---------- BM25 hybrid config ----------
BM25_TOP_K = int(os.getenv("PLATFORM_BM25_TOP_K", str(RERANKER_TOP_K)))
_bm25_enabled_raw = env_or_config("PLATFORM_BM25_ENABLED", _CFG, "search.bm25_enabled", True)
BM25_ENABLED = (
    _BM25_IMPORT_OK
    and ((_bm25_enabled_raw.lower() in ("true", "1", "yes")) if isinstance(_bm25_enabled_raw, str) else bool(_bm25_enabled_raw))
)
RRF_K_CONST = int(env_or_config("PLATFORM_RRF_K_CONST", _CFG, "search.rrf_k_const", 60))

# GPU concurrency: 多 session 同时 search_docs 时 encode/rerank 串行化的最大并发 (8GB 卡用 1)。
GPU_CONCURRENCY = int(env_or_config("PLATFORM_GPU_CONCURRENCY", _CFG, "search.gpu_concurrency", 1))
# /embed /rerank 的 GPU 算子超时(秒)。正常 encode/rerank 一批 < 数秒; 超过 = 卡住/极端争用。
# 超时则**释放 GPU 信号量**返 503(不再让一次卡死的 encode 永久持锁拖死整个 daemon /embed,
# 连带打挂在线 search_docs)。0/负 = 不限(回退旧行为)。大批量索引在云上走 remote 时尤其关键。
GPU_OP_TIMEOUT = float(env_or_config("PLATFORM_GPU_OP_TIMEOUT", _CFG, "search.gpu_op_timeout", 120.0))
