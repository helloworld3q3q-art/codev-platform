"""chroma daemon —— 配置常量叶子模块 (从 server.py 抽出, file-discipline §1 + 无环分层 DAG)。

只依赖 `core.*`, 不依赖 server / 任何 chroma 逻辑模块。所有逻辑模块 (server / _models /
_reranker / _tools / _http) 从这里拿常量 → 打破 reranker 等子系统对 server 的循环依赖
(见 docs/plans/roadmap-2026-06-01/refactor-largefile-errorcode-2026-06-01.md §A.2b)。

常量 import 期定死、从不 rebind → 各模块 `from ._config import X` 按值安全 (可变运行态在 _state)。
优先级: env var > ~/.codev-platform/config.json > 代码默认。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.paths import chroma_collection_name, chroma_dir
from codev_platform.core.config import load_config, env_or_config
from codev_platform.core.obslog import logging_mode

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

# import 期 best-effort 解析单 project (stdio 默认); daemon 多租户按 ?project_id= 路由,
# 故解析失败不退出 (与 cross_link/codegraph server 同款), 保证 import 始终可成功。
try:
    PROJECT_ID = resolve_local()
except ProjectIdError as _pid_exc:
    print(f"[codev_platform.chroma.server] WARN: project_id 未解析 ({_pid_exc!s}); "
          "daemon 多租户模式按请求 ?project_id= 路由, PROJECT_ID=None。", file=sys.stderr, flush=True)
    PROJECT_ID = None
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
