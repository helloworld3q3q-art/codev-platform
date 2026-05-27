"""Claude Code MCP server — 暴露 Chroma 文档检索给 Claude Code

提供 3 个 tools：
- search_docs       语义搜索 platform markdown 文档
- list_collections  查看 Chroma 知识库统计
- get_by_file       按文件路径精确获取所有 chunks

启动方式：
    python tools/chroma/mcp_server.py

stdio 通信；启动日志写 stderr 不污染协议。
依赖：mcp >= 0.9, chromadb, sentence-transformers
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import chromadb
# 注:不再用 Chroma 的 SentenceTransformerEmbeddingFunction —— 查询侧需要
# prompt_name='query'(Qwen3 instruction-aware),必须自己持有 SentenceTransformer。
# 索引侧同样自己 encode(见 index_docs.py)。

# codev-platform 包内 import (pip install -e codev-platform 后)
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.paths import chroma_collection_name, chroma_dir

# BM25 hybrid retrieval 伴侣(可选,jieba + rank_bm25 缺失时自动 disable)
try:
    from codev_platform.chroma.bm25 import BM25Index, rrf_fuse
    _BM25_IMPORT_OK = True
except ImportError as _bm25_imp_err:
    _BM25_IMPORT_OK = False
    _BM25_IMPORT_ERR = str(_bm25_imp_err)


# ----------------------------------------------------------------------
# 全局：Chroma 客户端 + collection（lazy init，启动失败也不挂 server）
# ----------------------------------------------------------------------

# DATA_DIR 走 codev_platform.core.paths.chroma_dir() (基于业务项目 cwd)
# 不再用 __file__.parents 推导 (那是 codev-platform package 自身位置, 错)
DATA_DIR = chroma_dir()
# 多项目命名: <project_id>__platform_docs
# 启动时解析 project_id (env / .claude/project.json), 失败硬退出
try:
    PROJECT_ID = resolve_local()
except ProjectIdError as _pid_exc:
    print(f"[codev_platform.chroma.server] FATAL: {_pid_exc!s}", file=sys.stderr, flush=True)
    sys.exit(1)
COLLECTION_BASE = "platform_docs"
COLLECTION_NAME = chroma_collection_name(PROJECT_ID, COLLECTION_BASE)
# backward compat: 旧索引存在 unprefixed `platform_docs`, daemon 启动时若新命名 collection
# 不存在, 自动 fallback 到旧名 + 警告 (_load_project_state 内处理)
LEGACY_COLLECTION_NAME = "platform_docs"  # 字面量明示, 与历史不带 project_id 前缀的 collection 名一致
# 优先级: env var > ~/.codev-platform/config.json > 代码默认.
# 不再 hardcode D:\models\... 路径 — 用户跑 `codev-platform config init` 生成 config 文件.
from codev_platform.core.config import load_config, env_or_config  # noqa: E402

_CFG = load_config()
EMBED_MODEL = str(Path(env_or_config("PLATFORM_EMBED_MODEL_PATH", _CFG, "models.embed_path")).expanduser().resolve())
EMBED_DEVICE = env_or_config("PLATFORM_EMBED_DEVICE", _CFG, "models.embed_device", "cuda")

# ---------- Reranker config (Qwen3-Reranker-0.6B chat-template + yes/no logits) ----------
RERANKER_MODEL = env_or_config("PLATFORM_RERANKER_MODEL_PATH", _CFG, "models.reranker_path", "")
RERANKER_DEVICE = env_or_config("PLATFORM_RERANKER_DEVICE", _CFG, "models.reranker_device", "cuda")
_rer_enabled_raw = env_or_config("PLATFORM_RERANKER_ENABLED", _CFG, "models.reranker_enabled", True)
RERANKER_ENABLED = (
    _rer_enabled_raw.lower() in ("true", "1", "yes")
    if isinstance(_rer_enabled_raw, str) else bool(_rer_enabled_raw)
)

# 重排前从 Chroma 取多少候选(rerank 后取 k 返回); 兼容旧 env 名 PLATFORM_RERANKER_TOP_K
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

# GPU concurrency: 多 session 同时 search_docs 时, encode / rerank 串行化的最大并发.
# 默认 1 = 完全串行 (8GB GPU 安全), 调高仅在 >= 24GB VRAM 时考虑.
GPU_CONCURRENCY = int(env_or_config("PLATFORM_GPU_CONCURRENCY", _CFG, "search.gpu_concurrency", 1))
_gpu_sem: "asyncio.Semaphore | None" = None  # lazy init in event loop

# 额外把启动 / 每次 query 日志写到固定文件，便于"观察模型起作用"
_LOG_FILE = Path(__file__).resolve().parent / "mcp_server.log"
# 召回质量分析日志:每次 search_docs 一行 JSON,后续可 jq 分析 top-5 distance 漂移
_RECALL_LOG = Path(__file__).resolve().parent / "search_recall.jsonl"


def _flog(msg: str) -> None:
    """同时写文件 + stderr。文件路径：tools/chroma/mcp_server.log"""
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # Windows MCP clients may close or replace stderr during startup. Logging
    # must never poison model initialization or stdio protocol handling.
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass


def _log_recall(record: dict) -> None:
    """JSONL 召回日志:每行一个 query。失败静默(不阻塞查询)。"""
    try:
        import json as _json
        with _RECALL_LOG.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

import contextvars
from dataclasses import dataclass, field

_client = None
_model = None  # shared embedding model (multi-tenant: 同模型服务所有 project)
_reranker_model = None  # populated lazily by reranker loader
_use_query_prompt = False
_global_init_error: str | None = None  # model load failure (跨 project 共享)

# 构建戳路径:index_docs.py 完成索引时写,server 每次 query 前 stat,
# mtime 变新就只重连 Chroma collection(模型不重载)
_STAMP_PATH = DATA_DIR / ".last_build.json"


@dataclass
class _ProjectState:
    """Per-project runtime state for multi-tenant daemon."""
    project_id: str
    collection: Any = None  # chroma Collection
    bm25_index: "BM25Index | None" = None
    last_stamp_mtime: float = 0.0
    init_error: str | None = None
    active_collection_name: str | None = None  # 实际使用的 collection 名 (prefixed 或 legacy)
    last_request_at: float | None = None  # tool 调用时间戳 (epoch sec), widget 三态点用


# project_id -> state. 用 dict, 不上锁 — asyncio 单线程, dict 操作原子。
_projects: dict[str, _ProjectState] = {}


# 全局 stats 累加器 (跨 project 共享 GPU model, 全部 project 调用一起统计)
_stats: dict[str, dict[str, float]] = {
    "embedding": {"calls": 0, "ms_total": 0.0},
    "reranker": {"calls": 0, "ms_total": 0.0},
}


def _record_stat(kind: str, elapsed_ms: float) -> None:
    """累加调用次数 + 总耗时。 kind: embedding / reranker.

    Notes:
    - _stats 是 in-memory, daemon 重启归零 (不持久化, widget 仅做实时观测用)
    - 线程安全: 仅在 asyncio event loop 单线程操作 (encode/rerank 都在 coroutine 内直调).
      若未来改 asyncio.to_thread 把 GPU 跑后台线程, 必须加 threading.Lock 保护
    """
    s = _stats.get(kind)
    if s is None:
        return
    s["calls"] += 1
    s["ms_total"] += elapsed_ms


def _gpu_memory_mb() -> float | None:
    """返回 CUDA 当前已分配显存 (MiB), 不可用 / 非 CUDA 返回 None。

    口径限制: 仅统计 daemon 当前 Python 进程 — 不含其它进程 (cross-link MCP / 别的占用).
    用于 widget 观测 daemon 自身负载, 不等同整卡占用. 整卡 free/used 走
    torch.cuda.mem_get_info(), 后续 Phase 2 可暴露。
    """
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        return round(torch.cuda.memory_allocated() / (1024 * 1024), 1)
    except Exception:
        return None


def _project_last_indexed_iso(project_id: str) -> str | None:
    """读 chroma per-project .last_build.<pid>.json mtime, 转 ISO8601 字符串。

    优先 per-project stamp (indexer 2026-05-28 起写入),
    回退全局 .last_build.json (老索引未升级时,仅当 project_id == daemon 启动默认时有效)。
    """
    from datetime import datetime, timezone
    # per-project stamp (新, 推荐)
    pp = _STAMP_PATH.parent / f".last_build.{project_id}.json"
    if pp.exists():
        try:
            return datetime.fromtimestamp(pp.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        except Exception:
            pass
    # fallback: 全局 stamp 仅对启动默认 project 准确, 其它返 None (避免误导)
    if project_id != PROJECT_ID:
        return None
    if not _STAMP_PATH.exists():
        return None
    try:
        return datetime.fromtimestamp(_STAMP_PATH.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:
        return None


def _to_iso(epoch_sec: float | None) -> str | None:
    if epoch_sec is None:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(epoch_sec, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception:
        return None

# contextvar 把 SSE session 跟 project_id 绑定; tool handler 通过它路由
_current_project_id: contextvars.ContextVar["str | None"] = contextvars.ContextVar(
    "_current_project_id", default=None
)


def _ensure_model():
    """Lazy 加载 SentenceTransformer 模型(只加载一次,hot-reload 不重载)。"""
    global _model, _use_query_prompt, _global_init_error
    if _model is not None:
        return _model
    if _global_init_error is not None:
        return None
    try:
        _flog(f"[init] model_path={EMBED_MODEL}")
        _flog(f"[init] device={EMBED_DEVICE} data_dir={DATA_DIR}")
        try:
            import torch
            cuda_ok = torch.cuda.is_available()
            cuda_name = torch.cuda.get_device_name(0) if cuda_ok else "n/a"
            _flog(f"[init] torch={torch.__version__} cuda_available={cuda_ok} gpu={cuda_name}")
        except Exception as e:
            _flog(f"[init] torch import failed: {e}")

        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)
        prompts = getattr(_model, "prompts", None) or {}
        _use_query_prompt = "query" in prompts and bool(prompts.get("query"))
        get_dim = _model.get_embedding_dimension if hasattr(_model, "get_embedding_dimension") else _model.get_sentence_embedding_dimension
        dim = get_dim()
        _flog(f"[init] model loaded dim={dim} max_seq={getattr(_model, 'max_seq_length', '?')} use_query_prompt={_use_query_prompt}")
        return _model
    except Exception as exc:  # noqa: BLE001
        _global_init_error = f"模型加载失败: {exc!s}"
        _flog(f"[init] ERROR: {_global_init_error}")
        return None


def _get_client():
    """Lazy chroma client (跨 project 共享单实例)."""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(DATA_DIR))
    return _client


def _load_project_state(project_id: str, reason: str) -> _ProjectState:
    """加载 project_id 对应的 collection + bm25, 失败抛 (调用方包 try)。

    backward compat: 若 project_id == 启动默认 PROJECT_ID 且 prefixed collection 不存在,
    fallback 到 legacy 'platform_docs'。其它 project_id 缺 prefixed collection 直接 raise。
    """
    client = _get_client()
    prefixed_name = chroma_collection_name(project_id, COLLECTION_BASE)
    col = None
    active_name = None
    try:
        col = client.get_collection(prefixed_name)
        active_name = prefixed_name
    except Exception as exc:  # noqa: BLE001
        if project_id == PROJECT_ID:
            # legacy fallback 仅对 daemon 启动默认 project 生效
            try:
                col = client.get_collection(LEGACY_COLLECTION_NAME)
                active_name = LEGACY_COLLECTION_NAME
                _flog(
                    f"[{reason}] WARN: prefixed '{prefixed_name}' 不存在, "
                    f"fallback legacy '{LEGACY_COLLECTION_NAME}' (project_id={project_id})。"
                    f"重跑 index_docs.py 后会写入新命名 collection。"
                )
            except Exception:
                raise exc
        else:
            raise

    cmeta = col.metadata or {}
    _flog(
        f"[{reason}] collection '{active_name}' loaded (project_id={project_id}), "
        f"chunks={col.count()} "
        f"meta_model={cmeta.get('embed_model_name')} meta_dim={cmeta.get('embedding_dim')}"
    )

    bm25 = None
    if BM25_ENABLED and _BM25_IMPORT_OK:
        try:
            import time as _t
            _t0 = _t.perf_counter()
            bm25 = BM25Index()
            n = bm25.build(col)
            _flog(
                f"[{reason}] bm25 built for {project_id}: {n} chunks, "
                f"took={(_t.perf_counter()-_t0)*1000:.0f}ms"
            )
        except Exception as exc:  # noqa: BLE001
            _flog(f"[{reason}] bm25 build FAILED for {project_id}: {exc!s} (fallback vector-only)")
            bm25 = None

    return _ProjectState(
        project_id=project_id,
        collection=col,
        bm25_index=bm25,
        active_collection_name=active_name,
    )


def _maybe_reload_project(state: _ProjectState) -> bool:
    """探测构建戳, mtime 变新则把该 project 从 _projects 移除让下次 _ensure_project 重建。
    返回 True = 已置失效 (caller 应重新 ensure)。"""
    if not _STAMP_PATH.exists():
        return False
    try:
        cur = _STAMP_PATH.stat().st_mtime
    except OSError:
        return False
    if cur <= state.last_stamp_mtime:
        return False
    # 首次见戳 → 只记录, 不重建 (state 已经 fresh)
    if state.last_stamp_mtime > 0:
        _flog(
            f"[reload] stamp mtime {state.last_stamp_mtime:.0f} -> {cur:.0f}, "
            f"dropping project {state.project_id} (model kept)"
        )
        _projects.pop(state.project_id, None)
        return True
    state.last_stamp_mtime = cur
    return False


def _ensure_project(project_id: str) -> _ProjectState | None:
    """模型 + 该 project 的 collection 都就绪。失败返回 None。"""
    if _ensure_model() is None:
        return None
    state = _projects.get(project_id)
    if state is not None:
        # 已加载 → 探测重载戳
        if _maybe_reload_project(state):
            state = None  # 已 evict, 走加载分支
    if state is not None:
        return state
    try:
        state = _load_project_state(project_id, "init")
        _projects[project_id] = state
        return state
    except Exception as exc:  # noqa: BLE001
        msg = (
            f"Chroma collection 加载失败 (project_id={project_id}): {exc!s}. "
            f"请先跑 tools/chroma/index_docs.py 索引文档 (PLATFORM_PROJECT_ID={project_id})。"
        )
        _flog(f"[ensure] ERROR: {msg}")
        # 记录到一个临时 state 让后续查询能拿到错误信息
        _projects[project_id] = _ProjectState(project_id=project_id, init_error=msg)
        return None


def _encode_query(query: str):
    """查询侧 encode:Qwen3 用 prompt_name='query',MiniLM 不用。"""
    kwargs: dict[str, Any] = {"normalize_embeddings": True, "convert_to_numpy": True}
    if _use_query_prompt:
        kwargs["prompt_name"] = "query"
    import time as _t
    _t0 = _t.perf_counter()
    vec = _model.encode([query], **kwargs)[0]
    _record_stat("embedding", (_t.perf_counter() - _t0) * 1000)
    return vec.tolist()


def _get_gpu_sem() -> "asyncio.Semaphore":
    """Lazy init GPU semaphore (must be inside event loop)."""
    global _gpu_sem
    if _gpu_sem is None:
        _gpu_sem = asyncio.Semaphore(GPU_CONCURRENCY)
    return _gpu_sem


# ---------- Reranker (Qwen3-Reranker-0.6B) ----------
# 走 yes/no token logits, 不是 sentence-transformers CrossEncoder
_reranker_tok = None
_reranker_model = None
_reranker_yes_id = None
_reranker_no_id = None
_reranker_load_err: str | None = None

_RERANKER_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based "
    "on the Query and the Instruct provided. Note that the answer can only be "
    "\"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n"
)
_RERANKER_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_RERANKER_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"


def _ensure_reranker():
    """Lazy 加载 Qwen3-Reranker. 模型 + tokenizer 一次性驻留 GPU.

    返回 (tok, model, yes_id, no_id) 或 None (未启用 / 加载失败).
    """
    global _reranker_tok, _reranker_model, _reranker_yes_id, _reranker_no_id, _reranker_load_err
    if not RERANKER_ENABLED:
        return None
    if not RERANKER_MODEL or not Path(RERANKER_MODEL).exists():
        if _reranker_load_err is None:
            _reranker_load_err = f"reranker path missing: {RERANKER_MODEL}"
            _flog(f"[reranker] disabled: {_reranker_load_err}")
        return None
    if _reranker_load_err is not None:
        return None
    if _reranker_tok is not None and _reranker_model is not None:
        return (_reranker_tok, _reranker_model, _reranker_yes_id, _reranker_no_id)
    try:
        import torch  # noqa: F401
        from transformers import AutoTokenizer, AutoModelForCausalLM
        _flog(f"[reranker] loading {RERANKER_MODEL} device={RERANKER_DEVICE}")
        _reranker_tok = AutoTokenizer.from_pretrained(RERANKER_MODEL, padding_side="left")
        import torch
        dtype = torch.float16 if RERANKER_DEVICE == "cuda" else torch.float32
        m = AutoModelForCausalLM.from_pretrained(RERANKER_MODEL, dtype=dtype)
        if RERANKER_DEVICE == "cuda":
            m = m.cuda()
        m = m.eval()
        _reranker_model = m
        _reranker_yes_id = _reranker_tok.convert_tokens_to_ids("yes")
        _reranker_no_id = _reranker_tok.convert_tokens_to_ids("no")
        _flog(f"[reranker] loaded, yes_id={_reranker_yes_id} no_id={_reranker_no_id}")
        return (_reranker_tok, _reranker_model, _reranker_yes_id, _reranker_no_id)
    except Exception as exc:  # noqa: BLE001
        _reranker_load_err = f"{type(exc).__name__}: {exc}"
        _flog(f"[reranker] load FAIL: {_reranker_load_err}")
        return None


def _rerank_scores(query: str, docs: list[str]) -> list[float] | None:
    """对 (query, doc) 对打分. 返回 [0,1] 浮点分数 (yes 概率).
    Reranker 不可用时返回 None, 调用方降级走原 embedding 顺序.
    """
    pack = _ensure_reranker()
    if pack is None or not docs:
        return None
    tok, model, yes_id, no_id = pack
    try:
        import time as _t
        import torch
        # 防御:截断对齐 CHUNK_HARD_MAX=1500(index_docs.py),避免 30 pair padded
        # sequence 拉满推理时延 +30~50%(2026-05-23 验证发现:>2000 字符 chunk 让
        # rerank P95 从 ~3s 升到 ~6.7s)
        prompts = [
            f"{_RERANKER_PREFIX}<Instruct>: {_RERANKER_INSTRUCT}\n<Query>: {query}\n<Document>: {d[:1500]}{_RERANKER_SUFFIX}"
            for d in docs
        ]
        inputs = tok(prompts, padding=True, truncation=True, return_tensors="pt", max_length=4096)
        if RERANKER_DEVICE == "cuda":
            inputs = {k: v.cuda() for k, v in inputs.items()}
        _t0 = _t.perf_counter()
        with torch.no_grad():
            logits = model(**inputs).logits[:, -1, :]
            scores = torch.softmax(logits[:, [no_id, yes_id]], dim=-1)[:, 1].cpu().tolist()
        _record_stat("reranker", (_t.perf_counter() - _t0) * 1000)
        return scores
    except Exception as exc:  # noqa: BLE001
        _flog(f"[reranker] score FAIL: {type(exc).__name__}: {exc}")
        return None


# ----------------------------------------------------------------------
# MCP server 定义
# ----------------------------------------------------------------------

server: Server = Server("platform-docs")


CATEGORIES = ["rule", "incident", "tooling_incident", "design", "operations", "claude_md", "skill", "doc", "tool_doc", "memory", "dev_log", "all"]
MODULES = ["platform", "stock-admin-api", "stock-admin-web", "stock-pipeline", "all"]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_docs",
            description=(
                "语义搜索平台 markdown 文档（规则 / 事故 / 设计 / 运维 / CLAUDE.md / skill）。"
                "返回 top-k 相关 chunk，可按 category / module 过滤。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然语言查询"},
                    "k": {
                        "type": "integer",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                        "description": "返回 chunk 数",
                    },
                    "category": {
                        "type": "string",
                        "enum": CATEGORIES,
                        "default": "all",
                        "description": "文档类别过滤",
                    },
                    "module": {
                        "type": "string",
                        "enum": MODULES,
                        "default": "all",
                        "description": "子模块过滤",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_collections",
            description="查看 Chroma 知识库统计（总文档数 / 各 category 数 / 各 module 数）",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_by_file",
            description="按文件路径精确获取该文件的所有 chunks（用于读全文，非语义检索）",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "如 '.claude/rules/pct-sign-convention.md'（相对仓库根路径）",
                    },
                },
                "required": ["file"],
            },
        ),
    ]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}, ensure_ascii=False))]


def _ok(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _build_where(category: str, module: str) -> dict | None:
    """构造 Chroma where 过滤（单字段直传，多字段用 $and）。"""
    clauses: list[dict] = []
    if category and category != "all":
        clauses.append({"category": category})
    if module and module != "all":
        clauses.append({"module": module})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    # Multi-tenant routing: contextvar 由 SSE handler 在 session 起来时 set
    pid = _current_project_id.get()
    if pid is None:
        # stdio mode 或 SSE 没传 ?project_id= 时, fallback 到 daemon 启动默认
        pid = PROJECT_ID
    state = _ensure_project(pid)
    if state is None or state.collection is None:
        err = (
            _projects.get(pid).init_error if pid in _projects else None
        ) or _global_init_error or f"project {pid} 未初始化"
        return _err(err)
    col = state.collection
    _bm25 = state.bm25_index

    import time as _t
    _t0 = _t.perf_counter()
    # 记录 project last_request_at (widget 三态点用)
    state.last_request_at = _t.time()
    try:
        if name == "search_docs":
            query = args.get("query", "").strip()
            if not query:
                return _err("query 不能为空")
            _flog(f"[search_docs] q={query!r} k={args.get('k', 5)} cat={args.get('category', 'all')} mod={args.get('module', 'all')}")
            k = int(args.get("k", DEFAULT_RETURN_K))
            k = max(1, min(20, k))
            category = args.get("category", "all")
            module = args.get("module", "all")
            where = _build_where(category, module)

            # Stage 1: embedding 召回. Reranker 启用时取 top-K 候选, 否则直接 top-k.
            use_rerank = RERANKER_ENABLED and Path(RERANKER_MODEL or "").exists() and _reranker_load_err is None
            n_candidates = max(k, RERANKER_TOP_K) if use_rerank else k

            # GPU 串行: 多 session 并发 search_docs 时 encode 排队 (防 8GB VRAM 抖动)
            async with _get_gpu_sem():
                qvec = _encode_query(query)
            kwargs: dict[str, Any] = {"query_embeddings": [qvec], "n_results": n_candidates}
            if where is not None:
                kwargs["where"] = where
            res = col.query(**kwargs)

            ids = (res.get("ids") or [[]])[0]
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]

            # Stage 1.5: BM25 并行召回 + RRF 融合(可选,失败降级纯向量).
            # 精确符号 / 版本号 / 罕见术语用 BM25 比向量准,中文段语义用向量.
            bm25_used = False
            bm25_hits_n = 0
            if _bm25 is not None and _bm25.ready():
                try:
                    _tb = _t.perf_counter()
                    bm25_hits = _bm25.search(query, n=BM25_TOP_K, where=where)
                    bm25_hits_n = len(bm25_hits)
                    if bm25_hits:
                        # RRF 融合两路 rank
                        vector_ids = list(ids)
                        bm25_ids = [h[0] for h in bm25_hits]
                        fused = rrf_fuse(vector_ids, bm25_ids, k_const=RRF_K_CONST)

                        # 重组 ids/docs/metas/dists:按融合 rank,缺失字段从 BM25 索引补
                        # BM25-only 命中没有 chroma distance,标 None
                        vec_pos = {cid: i for i, cid in enumerate(vector_ids)}
                        bm25_lookup = {h[0]: (h[2], h[3]) for h in bm25_hits}

                        new_ids: list[str] = []
                        new_docs: list[str] = []
                        new_metas: list[dict] = []
                        new_dists: list[float | None] = []
                        for cid, _ in fused[:n_candidates]:
                            new_ids.append(cid)
                            if cid in vec_pos:
                                i = vec_pos[cid]
                                new_docs.append(docs[i] if i < len(docs) else "")
                                new_metas.append(metas[i] if i < len(metas) else {})
                                new_dists.append(dists[i] if i < len(dists) else None)
                            elif cid in bm25_lookup:
                                bdoc, bmeta = bm25_lookup[cid]
                                new_docs.append(bdoc)
                                new_metas.append(bmeta)
                                new_dists.append(None)  # BM25-only,无向量距离
                        ids, docs, metas, dists = new_ids, new_docs, new_metas, new_dists
                        bm25_used = True
                        _flog(
                            f"[bm25] vec={len(vector_ids)} bm25={bm25_hits_n} "
                            f"fused={len(fused)} -> top {n_candidates}, took={(_t.perf_counter()-_tb)*1000:.1f}ms"
                        )
                except Exception as exc:  # noqa: BLE001
                    _flog(f"[bm25] search FAILED, fallback to vector-only: {exc!s}")

            # Stage 2: Reranker 重排(可选). 失败降级用 embedding 顺序.
            rerank_scores_arr: list[float] | None = None
            rerank_used = False
            if use_rerank and docs:
                _tr = _t.perf_counter()
                # GPU 串行 (reranker 同享 GPU 与 embed model)
                async with _get_gpu_sem():
                    scores = _rerank_scores(query, docs)
                if scores is not None:
                    rerank_scores_arr = scores
                    rerank_used = True
                    # 按 rerank score 降序重排所有数组
                    order = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
                    ids = [ids[i] for i in order]
                    docs = [docs[i] for i in order]
                    metas = [metas[i] for i in order]
                    dists = [dists[i] for i in order]
                    rerank_scores_arr = [scores[i] for i in order]
                    _flog(f"[rerank] {len(scores)} pairs, top_score={rerank_scores_arr[0]:.4f}, took={(_t.perf_counter()-_tr)*1000:.1f}ms")

            # 取 top-k 返回
            ids = ids[:k]
            docs = docs[:k]
            metas = metas[:k]
            dists = dists[:k]
            if rerank_scores_arr is not None:
                rerank_scores_arr = rerank_scores_arr[:k]

            out = []
            for idx, _id in enumerate(ids):
                meta = metas[idx] if idx < len(metas) else {}
                item = {
                    "file": (meta or {}).get("file"),
                    "category": (meta or {}).get("category"),
                    "module": (meta or {}).get("module"),
                    "chunk_index": (meta or {}).get("chunk_index"),
                    "distance": dists[idx] if idx < len(dists) else None,
                    "chunk": docs[idx] if idx < len(docs) else "",
                }
                if rerank_scores_arr is not None:
                    item["rerank_score"] = rerank_scores_arr[idx]
                out.append(item)

            _ms = (_t.perf_counter() - _t0) * 1000
            top1 = (metas[0] or {}).get("file") if metas else None
            top1_d = dists[0] if dists else None
            top1_rs = rerank_scores_arr[0] if rerank_scores_arr else None
            _flog(f"[search_docs] hit={len(out)} top1={top1} dist={top1_d} rerank={rerank_used} top1_score={top1_rs} took={_ms:.1f}ms")
            # JSONL 召回日志:记录 top-5 完整 metadata + distance + rerank_score
            import datetime as _dt
            top5 = []
            for i in range(min(5, len(out))):
                m = metas[i] if i < len(metas) else {}
                entry = {
                    "file": (m or {}).get("file"),
                    "category": (m or {}).get("category"),
                    "module": (m or {}).get("module"),
                    "chunk_index": (m or {}).get("chunk_index"),
                    "distance": dists[i] if i < len(dists) else None,
                }
                if rerank_scores_arr is not None and i < len(rerank_scores_arr):
                    entry["rerank_score"] = rerank_scores_arr[i]
                top5.append(entry)
            _log_recall({
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "query": query,
                "k": k,
                "category": category,
                "module": module,
                "hit": len(out),
                "rerank_used": rerank_used,
                "bm25_used": bm25_used,
                "bm25_hits": bm25_hits_n,
                "n_candidates": n_candidates if use_rerank else None,
                "elapsed_ms": round(_ms, 1),
                "top5": top5,
            })
            return _ok(out)

        if name == "list_collections":
            total = col.count()
            # 拉全部 metadata（不要 documents 节省内存）
            all_data = col.get(include=["metadatas"])
            metas = all_data.get("metadatas") or []
            by_category: dict[str, int] = {}
            by_module: dict[str, int] = {}
            for m in metas:
                if not m:
                    continue
                c = m.get("category") or "unknown"
                mod = m.get("module") or "unknown"
                by_category[c] = by_category.get(c, 0) + 1
                by_module[mod] = by_module.get(mod, 0) + 1
            return _ok(
                {
                    "project_id": pid,
                    "collection": state.active_collection_name,
                    "data_dir": str(DATA_DIR),
                    "embed_model": EMBED_MODEL,
                    "collection_metadata": col.metadata or {},
                    "total_chunks": total,
                    "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
                    "by_module": dict(sorted(by_module.items(), key=lambda x: -x[1])),
                    "tenant_mode": "multi",
                    "loaded_projects": list(_projects.keys()),
                }
            )

        if name == "get_by_file":
            file_path = (args.get("file") or "").strip()
            if not file_path:
                return _err("file 不能为空")
            res = col.get(where={"file": file_path}, include=["documents", "metadatas"])
            ids = res.get("ids") or []
            docs = res.get("documents") or []
            metas = res.get("metadatas") or []
            if not ids:
                return _err(f"未找到文件 '{file_path}' 的任何 chunk（检查路径是否相对仓库根）")
            pairs = []
            for idx, _id in enumerate(ids):
                meta = metas[idx] if idx < len(metas) else {}
                pairs.append(
                    {
                        "chunk_index": (meta or {}).get("chunk_index", idx),
                        "category": (meta or {}).get("category"),
                        "module": (meta or {}).get("module"),
                        "chunk": docs[idx] if idx < len(docs) else "",
                    }
                )
            pairs.sort(key=lambda x: x.get("chunk_index") or 0)
            return _ok({"file": file_path, "chunk_count": len(pairs), "chunks": pairs})

        return _err(f"未知 tool: {name}")
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        print(f"[chroma-mcp] tool '{name}' error: {tb}", file=sys.stderr)
        return _err(f"tool '{name}' 执行失败: {exc!s}")


async def _run_stdio() -> None:
    """传统 stdio transport: Claude Code 直接 spawn cmd 当 stdio server。"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def _run_http(port: int) -> None:
    """HTTP/SSE transport: daemon 模式, 供多 Claude Code 会话共享。

    单 daemon 常驻, N 个会话各自通过 platform_docs_launcher.py 的 stdio↔HTTP
    proxy 连过来。8GB GPU 上多会话场景必走此模式 (每进程独立 load Qwen 模型
    会立刻 OOM)。

    端口默认 18083 (PLATFORM_DOCS_DAEMON_PORT 环境变量覆盖)。绑 127.0.0.1
    仅本地访问, 不暴露网络。
    """
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route
    import uvicorn

    sse_transport = SseServerTransport("/messages/")

    async def handle_sse(request):
        # MCP SSE 双向流: GET /sse?project_id=<pid> 建立 stream + 绑定 project_id 到 contextvar
        # multi-tenant: 同一 daemon 服务多个 project_id, 每 session 独立路由
        pid_raw = request.query_params.get("project_id")
        if not pid_raw:
            # backward compat: legacy launcher 不传 ?project_id= 时 fallback 启动默认
            pid = PROJECT_ID
            _flog(f"[sse] no ?project_id= in query, fallback to default {pid}")
        else:
            try:
                from codev_platform.core.project_id import validate as _pid_validate
                pid = _pid_validate(pid_raw)
            except Exception as exc:  # noqa: BLE001
                _flog(f"[sse] reject: invalid project_id {pid_raw!r}: {exc!s}")
                return  # SSE connect 中止

        # eager load 让 SSE 建立前发现 collection 缺失类问题
        state = _ensure_project(pid)
        if state is None or state.collection is None:
            err = (state.init_error if state else None) or "project init failed"
            _flog(f"[sse] reject: cannot load project {pid}: {err}")
            return

        token = _current_project_id.set(pid)
        _flog(f"[sse] session start project_id={pid}")
        try:
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        finally:
            _current_project_id.reset(token)
            _flog(f"[sse] session end project_id={pid}")

    async def health(_request):
        # daemon ready 判定: launcher 用此判定是否需要等模型加载完
        # multi-tenant: 报告所有 loaded projects + 默认 project_id (向后兼容字段保留)
        model_ready = _model is not None
        loaded = []
        stale_pids: list[str] = []
        for p in _projects.values():
            chunks: int | None
            init_error = p.init_error
            if p.collection is None:
                chunks = 0
            else:
                try:
                    chunks = p.collection.count()
                except Exception as exc:  # noqa: BLE001
                    # 常见: chromadb.errors.NotFoundError 来自 reindex 期间底层 collection 被重建
                    # daemon 仍持有 stale handle。退化为 None + 标错 + 排队 evict, 下次 ensure 重新加载。
                    chunks = None
                    if not init_error:
                        init_error = f"count failed: {type(exc).__name__}: {exc}"
                    stale_pids.append(p.project_id)
                    _flog(
                        f"[health] stale collection for {p.project_id}: {type(exc).__name__}: {exc}"
                    )
            loaded.append(
                {
                    "project_id": p.project_id,
                    "chunks": chunks,
                    "collection_name": p.active_collection_name,
                    "bm25": (p.bm25_index.ready() if p.bm25_index is not None else False),
                    "init_error": init_error,
                    "last_request_at": _to_iso(p.last_request_at),
                    "last_indexed_at": _project_last_indexed_iso(p.project_id),
                }
            )
        for pid in stale_pids:
            _projects.pop(pid, None)
        any_collection_ready = any(p.collection is not None for p in _projects.values())
        reranker_ready = _reranker_model is not None
        all_ready = model_ready and any_collection_ready
        # stats avg = ms_total / calls (None when calls=0)
        emb = _stats["embedding"]
        rer = _stats["reranker"]
        stats_payload = {
            "embedding": {
                "calls": int(emb["calls"]),
                "ms_avg": round(emb["ms_total"] / emb["calls"], 2) if emb["calls"] else None,
            },
            "reranker": {
                "calls": int(rer["calls"]),
                "ms_avg": round(rer["ms_total"] / rer["calls"], 2) if rer["calls"] else None,
            },
            "gpu_memory_mb": _gpu_memory_mb(),
        }
        return JSONResponse(
            {
                "status": "ok" if all_ready else "starting",
                "model": "loaded" if model_ready else "loading",
                "collection": "ready" if any_collection_ready else "init",
                "reranker": "loaded" if reranker_ready else "loading_or_disabled",
                "init_error": _global_init_error,
                "project_id": PROJECT_ID,  # backward-compat: 启动默认 project_id
                "tenant_mode": "multi",
                "loaded_projects": loaded,
                "stats": stats_payload,
            },
            status_code=200 if all_ready else 503,
        )

    app = Starlette(
        debug=False,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/sse", handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse_transport.handle_post_message),
        ],
    )

    _flog(f"[daemon] HTTP server starting on 127.0.0.1:{port}")
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server_inst = uvicorn.Server(config)
    await server_inst.serve()


async def main() -> None:
    # 两种 transport:
    #   stdio (默认): 每个 Claude Code 会话各起一份 mcp_server, lazy load 模型
    #   --http (daemon 模式): 单进程跑, 多会话通过 launcher 共享 (8GB GPU 必走)
    is_http = "--http" in sys.argv

    # Prewarm: HTTP daemon 模式下强制开启 (cold-start 风险只 daemon 启动时承担一次);
    # stdio 模式下按 PLATFORM_DOCS_PREWARM 环境变量决定 (兼容旧行为)
    should_prewarm = is_http or os.getenv("PLATFORM_DOCS_PREWARM", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    if should_prewarm:
        # Embedding 模型 + collection 一次加载 (lazy 兜底仍存在, 这里只是提前)
        _ensure_project(PROJECT_ID)  # prewarm daemon 启动默认 project
        # Reranker 必须显式 prewarm: _ensure_project 不会调它, 不 prewarm 会让
        # 首次 search_docs 命中 reranker 二次冷启动 (~15-30s), 加上 embedding 已
        # 经 warmed 但 reranker 还没 → 整体超过 60s client timeout 概率高。
        try:
            _ensure_reranker()
        except Exception as exc:  # noqa: BLE001
            _flog(f"[init] reranker prewarm skipped: {exc!s}")

    if is_http:
        port = int(os.getenv("PLATFORM_DOCS_DAEMON_PORT", "18083"))
        await _run_http(port)
    else:
        await _run_stdio()


if __name__ == "__main__":
    asyncio.run(main())
