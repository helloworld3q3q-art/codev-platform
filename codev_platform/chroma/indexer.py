"""
Chroma 平台文档全量索引器
将 CLAUDE.md / .claude/rules / docs / 子模块 CLAUDE.md 等 markdown
切块后写入本地 Chroma 持久化 collection 'platform_docs'，
供 Brother A/B/C 兄弟 RAG 查询规则与设计文档使用。

用法：
    python tools/chroma/index_docs.py                # 全量增量 upsert
    python tools/chroma/index_docs.py --dry-run      # 只数文件 + chunk 数，不入库
    python tools/chroma/index_docs.py --force        # 删除已有 collection 重建

不要直接跑 —— 等 Brother A 验证 Chroma 跑通后由人工触发。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable

# ---- 配置 ----

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_ROOT = Path(os.getenv("PLATFORM_ROOT", str(_DEFAULT_ROOT))).expanduser().resolve()

# 多项目命名: <project_id>__platform_docs (与 server.py 共用 resolver)
# PERSIST_DIR 走 codev_platform.core.paths.chroma_dir() — 它读 PLATFORM_DATA_DIR env,
# 确保多 project 写到 SHARED chroma DB, 而非各自 PLATFORM_ROOT/data/chroma.
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.paths import chroma_collection_name, chroma_dir

try:
    PROJECT_ID = resolve_local(PLATFORM_ROOT)
except ProjectIdError as _pid_exc:
    print(f"[index_docs] FATAL: {_pid_exc!s}", file=sys.stderr, flush=True)
    sys.exit(1)

# chroma_dir() 内部走 _business_repo_root() (从 cwd 向上找 .claude/project.json),
# 但 cwd 此时可能是 platform/tools/chroma/ (post-commit hook 起点), 不是业务仓根.
# 因此 PLATFORM_DATA_DIR env 必须设, 让 data_root() 直接吃 env, 跳过 cwd 推导.
PERSIST_DIR = chroma_dir()
COLLECTION_NAME = chroma_collection_name(PROJECT_ID, "platform_docs")
# 默认走机器共享路径 D:\models\Qwen3-Embedding-0.6B(跨项目复用),仓库内 models/ 是 fallback
_QWEN3_SHARED = Path(r"D:\models\Qwen3-Embedding-0.6B")
_MINILM_INREPO = PLATFORM_ROOT / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_EMBEDDING_MODEL = _QWEN3_SHARED if _QWEN3_SHARED.exists() else _MINILM_INREPO
EMBEDDING_MODEL = str(Path(os.getenv("PLATFORM_EMBED_MODEL_PATH", str(DEFAULT_EMBEDDING_MODEL))).expanduser().resolve())
EMBEDDING_DEVICE = os.getenv("PLATFORM_EMBED_DEVICE", "cuda")
# 索引侧批大小 — Qwen3-0.6B 在 RTX 5060 上 batch=16 较稳;MiniLM 可以更大
EMBEDDING_BATCH_SIZE = int(os.getenv("PLATFORM_EMBED_BATCH_SIZE", "16"))

# DEFAULT_DOC_PATTERNS: 任何项目通用的 markdown 位置 (不含业务专属路径).
# 业务专属路径 (apps/stock-admin-* / python/stock-pipeline 等) 走各业务仓
# <repo>/.claude/index.json:doc_patterns override (见 _load_project_index_config).
DOC_PATTERNS = [
    "CLAUDE.md",
    "AGENTS.md",
    "README.md",
    ".claude/rules/*.md",
    ".claude/skills/**/*.md",
    "docs/**/*.md",
]

EXCLUDE_PARTS = {"archive", "node_modules", "__pycache__", "target"}
# 白名单:即使路径含 EXCLUDE_PARTS 也保留(N10 incident 复盘需可检索)
EXCLUDE_WHITELIST_SUBPATHS = ("archive/incidents/",)

# chunk 切分常量 + 函数已迁到 codev_platform.chroma.chunking (跨项目通用算法).
# 本地保留 import 别名兼容已有 manifest 校验逻辑 (params.chunk_*_max 比对).
from codev_platform.chroma.chunking import (  # noqa: E402
    CHUNK_HARD_MAX,
    CHUNK_TARGET_MAX,
    chunk_text,
    file_sha256 as _file_sha256_from_codev,
    hard_split,
    split_by_heading,
    split_by_paragraph,
)

# manifest schema version — 改 chunk 策略 / metadata 结构时升级,自动触发 full rebuild
MANIFEST_VERSION = 1
MANIFEST_PATH = PERSIST_DIR / "index_manifest.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("chroma_index")


# ---- 文件发现 ----

def is_excluded(path: Path) -> bool:
    """命中 archive / node_modules / __pycache__ / target 任一目录则排除;
    archive/incidents/ 白名单豁免(N10:复盘文档需可检索)"""
    rel = str(path).replace("\\", "/")
    for whitelist in EXCLUDE_WHITELIST_SUBPATHS:
        if whitelist in rel:
            return False
    return any(part in EXCLUDE_PARTS for part in path.parts)


def _load_project_index_config() -> tuple[list[str], list[str]]:
    """读 <PLATFORM_ROOT>/.claude/index.json (若存在), 返回 (doc_patterns, external_doc_paths).

    - doc_patterns: 相对 PLATFORM_ROOT 的 glob (默认走 DOC_PATTERNS hardcode 列表)
    - external_doc_paths: 跨仓真值源 glob, 支持相对路径 (基于 PLATFORM_ROOT) 或绝对路径
      例(相对, 推荐): "../codev-platform/rules/*.md"  → 跨机器 portable
      例(绝对, 兼容): "D:/WorkSpace/codev-platform/rules/*.md"  → 机器绑定 (违反 feedback_no_absolute_paths)
    """
    cfg = PLATFORM_ROOT / ".claude" / "index.json"
    patterns: list[str] = list(DOC_PATTERNS)
    external: list[str] = []
    if not cfg.is_file():
        return patterns, external
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
        if isinstance(data.get("doc_patterns"), list) and data["doc_patterns"]:
            patterns = list(data["doc_patterns"])
            logger.info("loaded %d doc_patterns from %s (override default %d)",
                        len(patterns), cfg, len(DOC_PATTERNS))
        if isinstance(data.get("external_doc_paths"), list):
            external = [str(p) for p in data["external_doc_paths"]]
            if external:
                logger.info("loaded %d external_doc_paths from %s", len(external), cfg)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("failed to parse %s: %s, fallback to default", cfg, exc)
    return patterns, external


def discover_files() -> list[Path]:
    """按 DOC_PATTERNS glob 全平台 markdown，去重 + 排除黑名单, 含 external_doc_paths 跨仓真值源."""
    import glob as _glob
    patterns, external = _load_project_index_config()
    seen: set[Path] = set()
    # 本仓 glob (相对 PLATFORM_ROOT)
    for pattern in patterns:
        for p in PLATFORM_ROOT.glob(pattern):
            if not p.is_file():
                continue
            if is_excluded(p.relative_to(PLATFORM_ROOT)):
                continue
            seen.add(p.resolve())
    # 外部 glob (cross-repo 真值源). 相对路径 anchor 到 PLATFORM_ROOT, 绝对路径直用.
    for ext_pattern in external:
        if Path(ext_pattern).is_absolute():
            anchored = ext_pattern
        else:
            anchored = str(PLATFORM_ROOT / ext_pattern)
        for s in _glob.glob(anchored, recursive=True):
            p = Path(s)
            if p.is_file():
                seen.add(p.resolve())
    return sorted(seen)


# ---- chunk 切分 ----

# split_by_heading / split_by_paragraph / hard_split / chunk_text 已迁出, import 自
# codev_platform.chroma.chunking. _HEADING_RE / 常量同步 (见上).


# ---- metadata 推断 ----

def infer_category(path: str) -> str:
    """路径到分类的映射"""
    p = path.replace("\\", "/")
    # memory 用户偏好优先级最高(2026-05-26),内容紧凑直接召回
    # 注意:相对路径 "docs/memory/xxx.md" 没前导 "/",不能写 "/docs/memory/" in p
    if "docs/memory/" in p:
        return "memory"
    # dev-evolution 开发流程演化:log/ 决策日志 + incidents/ 工具栈事故复盘
    if "docs/dev-evolution/incidents/" in p:
        return "tooling_incident"
    if "docs/dev-evolution/" in p:
        return "dev_log"
    if "/rules/" in p:
        return "rule"
    # incident 优先级高于 operations(N10):incident-*.md / archive/incidents/ / daily-summary
    if "/archive/incidents/" in p or "/incident-" in p or "daily-summary" in p:
        return "incident"
    if "roadmap" in p or "/architecture/" in p:
        return "design"
    if "/operations/" in p:
        return "operations"
    if "/skills/" in p:
        return "skill"
    if p.endswith("CLAUDE.md") or p.endswith("AGENTS.md"):
        return "claude_md"
    if p.startswith("tools/"):
        return "tool_doc"
    return "doc"


def infer_module(path: str) -> str:
    """子模块归属"""
    p = path.replace("\\", "/")
    if p.startswith("apps/stock-admin-api/"):
        return "stock-admin-api"
    if p.startswith("apps/stock-admin-web/"):
        return "stock-admin-web"
    if p.startswith("python/stock-pipeline/"):
        return "stock-pipeline"
    return "platform"


# ---- 索引主流程 ----

def _rel_path(f: Path) -> str:
    """文件路径的稳定标识. 本仓内 -> relative_to(PLATFORM_ROOT); 跨仓 external -> 绝对路径 (as posix)."""
    try:
        return f.relative_to(PLATFORM_ROOT).as_posix()
    except ValueError:
        return f.as_posix()


def iter_chunks(files: list[Path]) -> Iterable[tuple[str, int, str, dict]]:
    """逐文件 → 逐 chunk 产出 (id, idx, content, metadata)"""
    for f in files:
        rel = _rel_path(f)
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = f.read_text(encoding="gbk")
            except Exception as exc:
                logger.warning("跳过无法解码文件 %s: %s", rel, exc)
                continue
        except Exception as exc:
            logger.warning("跳过读取失败文件 %s: %s", rel, exc)
            continue

        chunks = chunk_text(text)
        category = infer_category(rel)
        module = infer_module(rel)

        for idx, chunk in enumerate(chunks):
            meta = {
                "file": rel,
                "chunk_index": idx,
                "category": category,
                "module": module,
                "lang": "zh",
            }
            chunk_id = f"{rel}#{idx}"
            yield chunk_id, idx, chunk, meta


# ---- manifest 增量 ----

# _file_sha256 别名指向 codev_platform.chroma.chunking.file_sha256 (跨项目通用算法)
_file_sha256 = _file_sha256_from_codev


def _empty_manifest() -> dict:
    """返回空 manifest 骨架。"""
    return {"version": MANIFEST_VERSION, "params": {}, "files": {}}


def _load_manifest() -> tuple[dict, bool]:
    """读 manifest,返回 (manifest, 是否来自有效文件)。"""
    if not MANIFEST_PATH.exists():
        return _empty_manifest(), False
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest root is not object")
        data.setdefault("version", MANIFEST_VERSION)
        data.setdefault("params", {})
        data.setdefault("files", {})
        return data, True
    except Exception as exc:
        logger.warning("manifest 损坏(%s),按全量重建", exc)
        return _empty_manifest(), False


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _current_params(dim: int, model_name: str) -> dict:
    """当前 embed/chunk 参数指纹 — 不一致触发 full rebuild"""
    return {
        "manifest_version": MANIFEST_VERSION,
        "embed_model": model_name,
        "embed_dim": dim,
        "chunk_target_max": CHUNK_TARGET_MAX,
        "chunk_hard_max": CHUNK_HARD_MAX,
    }


def _manifest_static_params_match(manifest: dict) -> bool:
    """不加载模型即可判断的参数是否匹配。"""
    params = manifest.get("params") or {}
    return (
        params.get("manifest_version") == MANIFEST_VERSION
        and params.get("embed_model") == Path(EMBEDDING_MODEL).name
        and params.get("chunk_target_max") == CHUNK_TARGET_MAX
        and params.get("chunk_hard_max") == CHUNK_HARD_MAX
    )


def _scan_changes(files: list[Path], manifest: dict) -> tuple[list[Path], list[str], dict[str, str]]:
    """对比当前文件与 manifest,返回 (变更文件列表, 已删除文件 rel 列表, rel→sha256 map)

    变更 = 新增 + sha256 不一致;未变 = 跳过(零成本)
    """
    changed: list[Path] = []
    new_sha: dict[str, str] = {}
    current_rels: set[str] = set()
    for f in files:
        rel = _rel_path(f)
        current_rels.add(rel)
        try:
            sha = _file_sha256(f)
        except Exception as exc:
            logger.warning("跳过 sha256 失败 %s: %s", rel, exc)
            continue
        new_sha[rel] = sha
        old = manifest.get("files", {}).get(rel)
        if not old or old.get("sha256") != sha:
            changed.append(f)
    deleted = [rel for rel in manifest.get("files", {}) if rel not in current_rels]
    return changed, deleted, new_sha


def index(force: bool = False) -> tuple[int, int]:
    """执行增量索引；返回 (本次处理文件数, 本次处理 chunk 数)

    增量策略(2026-05-23 起,manifest 驱动):
      - manifest 记每文件 sha256 + chunk_count
      - 未变文件直接跳过(零成本)
      - 变更文件:删旧 chunks → 切新 chunks → encode → upsert → 更新 manifest
      - 已删文件:按旧 chunk_count 删 Chroma 残留 chunks + 移出 manifest
      - 参数指纹(embed_model/dim/chunk_max)变化 → 自动 full rebuild
      - --force 等同 full rebuild + 清空 manifest

    手工 encode + col.add(embeddings=...) 绕开 Chroma 内置 EmbeddingFunction
    —— 为了让查询侧能用 prompt_name='query'(Qwen3 instruction-aware),两侧
    必须分离。
    """
    import chromadb

    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(PERSIST_DIR))

    if force:
        try:
            client.delete_collection(COLLECTION_NAME)
            logger.info("--force：已删除旧 collection %s", COLLECTION_NAME)
        except Exception:
            pass
        # 清 manifest,后续走 full rebuild 路径
        manifest = _empty_manifest()
        manifest_loaded = False
    else:
        # 多项目迁移期: 当前 project 写 prefixed collection, legacy unprefixed `platform_docs`
        # 可能仍存在 (旧索引备份)。提示用户确认无问题后手动 prune,避免占双倍磁盘。
        try:
            legacy_names = [c.name for c in client.list_collections()
                            if c.name == "platform_docs" and c.name != COLLECTION_NAME]
            if legacy_names:
                logger.info(
                    "提示: legacy collection 'platform_docs' 仍存在 (与本次写入 %s 不同)。"
                    "确认 prefixed collection 工作正常后可手动 prune: "
                    "python -c \"import chromadb; "
                    "chromadb.PersistentClient(path='data/chroma').delete_collection('platform_docs')\"",
                    COLLECTION_NAME,
                )
        except Exception:
            pass
        manifest, manifest_loaded = _load_manifest()

    files = discover_files()
    logger.info("发现 %d 个 markdown 文件", len(files))

    # 首次接入 manifest 或 manifest 损坏时,旧 collection 可能包含已删除文件 /
    # 旧 chunk 策略留下的残留 ids。清 collection 后全量回填,避免旧知识继续可搜。
    bootstrap_full_rebuild = force or not manifest_loaded
    if bootstrap_full_rebuild and not force:
        try:
            client.delete_collection(COLLECTION_NAME)
            logger.info("manifest 不存在或不可用: 已清空旧 collection,准备全量回填")
        except Exception:
            pass

    # 先用文件内容 sha256 算变更 — 不加载模型,纯 IO 操作 ~毫秒级
    changed_files, deleted_rels, new_sha_map = _scan_changes(files, manifest)
    if bootstrap_full_rebuild:
        changed_files = list(files)
        deleted_rels = []
    elif not changed_files and not deleted_rels and not _manifest_static_params_match(manifest):
        logger.warning("manifest 静态参数不一致,准备全量校验并重建")
        changed_files = list(files)
    logger.info("增量: 变更 %d / 删除 %d / 未变 %d",
                len(changed_files), len(deleted_rels), len(files) - len(changed_files))

    # 早返回 — 完全无变更时不加载模型 / 不连 Chroma 写侧(注意 collection 元数据已存在,跳过即可)
    if not changed_files and not deleted_rels:
        # 即便 0 变更也要写构建戳,让 MCP server 知道"已最新"(touch mtime)
        # 但 manifest 中的 params/embed_dim 在第一次完整 build 后就稳定,直接复用
        cached_params = manifest.get("params") or {}
        _write_build_stamp(
            files_count=len(files),
            chunks=sum(v.get("chunk_count", 0) for v in manifest.get("files", {}).values()),
            dim=int(cached_params.get("embed_dim", 0)) or 1024,
            model_name=cached_params.get("embed_model") or Path(EMBEDDING_MODEL).name,
        )
        logger.info("无变更,跳过模型加载 + Chroma 写入")
        return 0, 0

    # 有变更才加载模型
    from sentence_transformers import SentenceTransformer
    logger.info("加载模型 %s (device=%s)", EMBEDDING_MODEL, EMBEDDING_DEVICE)
    model = SentenceTransformer(EMBEDDING_MODEL, device=EMBEDDING_DEVICE)
    get_dim = model.get_embedding_dimension if hasattr(model, "get_embedding_dimension") else model.get_sentence_embedding_dimension
    dim = get_dim()
    prompts = getattr(model, "prompts", None) or {}
    query_prompt_enabled = "query" in prompts and bool(prompts.get("query"))
    max_seq_length = getattr(model, "max_seq_length", None)
    model_name = Path(EMBEDDING_MODEL).name
    logger.info("embedding 维度=%d, max_seq_length=%s, prompts=%s",
                dim, max_seq_length or "?", bool(prompts))

    # 参数指纹检查 — 不一致退化为 full rebuild(全部 files 当变更处理)
    current_params = _current_params(dim, model_name)
    manifest_params = manifest.get("params") or {}
    if not force and manifest_params and manifest_params != current_params:
        logger.warning("manifest params 不一致(可能升级模型 / 改 chunk 配置),触发 full rebuild")
        logger.warning("  旧: %s", manifest_params)
        logger.warning("  新: %s", current_params)
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        manifest = _empty_manifest()
        changed_files = list(files)
        deleted_rels = []
        new_sha_map = {_rel_path(f): _file_sha256(f) for f in files}

    collection_metadata = {
        "hnsw:space": "cosine",
        "embed_model": EMBEDDING_MODEL,
        "embed_model_name": model_name,
        "embed_device": EMBEDDING_DEVICE,
        "embedding_dim": dim,
        "max_seq_length": int(max_seq_length) if max_seq_length else 0,
        "query_prompt_enabled": query_prompt_enabled,
    }

    col = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata=collection_metadata,
    )

    # 步骤 1: 删已删除文件的所有残留 chunks
    for rel in deleted_rels:
        old_count = manifest["files"][rel].get("chunk_count", 0)
        if old_count > 0:
            try:
                col.delete(ids=[f"{rel}#{i}" for i in range(old_count)])
                logger.info("删除文件 %s 的 %d 个旧 chunks", rel, old_count)
            except Exception as exc:
                logger.warning("删除 %s 旧 chunks 失败: %s", rel, exc)
        manifest["files"].pop(rel, None)

    # 步骤 2: 变更文件先删旧 chunks(防 chunk 数变少时残留),再写新 chunks
    ids_buf: list[str] = []
    docs_buf: list[str] = []
    metas_buf: list[dict] = []
    total = 0
    BATCH = 100

    def _flush():
        if not ids_buf:
            return
        embeddings = model.encode(
            docs_buf,
            batch_size=EMBEDDING_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        col.upsert(
            ids=list(ids_buf),
            documents=list(docs_buf),
            metadatas=list(metas_buf),
            embeddings=embeddings.tolist(),
        )

    # 按文件分组写入 — 一文件一次 delete(旧 chunks) + 多次 upsert(新 chunks 走 batch)
    file_chunk_counts: dict[str, int] = {}
    for f in changed_files:
        rel = _rel_path(f)
        # 删旧 chunks(若有)
        old_count = manifest.get("files", {}).get(rel, {}).get("chunk_count", 0)
        if old_count > 0:
            try:
                col.delete(ids=[f"{rel}#{i}" for i in range(old_count)])
            except Exception as exc:
                logger.warning("删 %s 旧 chunks 失败(忽略): %s", rel, exc)

        # 切新 chunks → 入 batch
        per_file_count = 0
        for chunk_id, _idx, content, meta in iter_chunks([f]):
            ids_buf.append(chunk_id)
            docs_buf.append(content)
            metas_buf.append(meta)
            per_file_count += 1
            total += 1
            if len(ids_buf) >= BATCH:
                _flush()
                logger.info("已处理 %d chunks", total)
                ids_buf.clear(); docs_buf.clear(); metas_buf.clear()
        file_chunk_counts[rel] = per_file_count

    if ids_buf:
        _flush()
        logger.info("已处理 %d chunks (末批)", total)

    # 步骤 3: 更新 manifest 中变更文件的 sha256 + chunk_count
    for rel, count in file_chunk_counts.items():
        manifest["files"][rel] = {
            "sha256": new_sha_map[rel],
            "chunk_count": count,
        }
    manifest["params"] = current_params
    manifest["version"] = MANIFEST_VERSION
    _save_manifest(manifest)

    # 写构建戳:总文件 / 总 chunks 从 manifest 算(不只是本次变更的)
    total_files = len(manifest["files"])
    total_chunks = sum(v.get("chunk_count", 0) for v in manifest["files"].values())

    # F2 完整性探针: manifest 期望 chunk 数 vs collection 实际 count。
    # 偏差常见于 upsert 部分失败 / 残留孤儿 chunk / delete 未对齐。只告警不阻塞 (索引仍可用)。
    try:
        actual = col.count()
        if actual != total_chunks:
            logger.warning(
                "[integrity] collection count=%d != manifest chunks=%d (差 %+d)。"
                "可能 upsert 部分失败或残留孤儿, 建议 --force 重建确认。",
                actual, total_chunks, actual - total_chunks,
            )
        else:
            logger.info("[integrity] collection count=%d == manifest, OK", actual)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[integrity] count 探针失败(忽略): %s", exc)

    _write_build_stamp(files_count=total_files, chunks=total_chunks,
                       dim=dim, model_name=model_name)

    return len(changed_files), total


def _write_build_stamp(files_count: int, chunks: int, dim: int, model_name: str) -> None:
    """写 build stamp: 全局 .last_build.json (server reload 探测) + per-project .last_build.<pid>.json
    (multi-tenant /health 上报 per-project last_indexed_at, 多 project 各自显示真实索引时间)。
    """
    import json
    payload = {
        "built_at": time.time(),
        "built_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "files": files_count,
        "chunks": chunks,
        "embed_dim": dim,
        "embed_model": model_name,
        "project_id": PROJECT_ID,
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    # 全局 stamp (向后兼容, daemon hot-reload 探测)
    (PERSIST_DIR / ".last_build.json").write_text(serialized, encoding="utf-8")
    # per-project stamp (widget 每行真实时间戳)
    (PERSIST_DIR / f".last_build.{PROJECT_ID}.json").write_text(serialized, encoding="utf-8")
    logger.info("写入构建戳 .last_build.json + .last_build.%s.json (chunks=%d)", PROJECT_ID, chunks)


def dry_run() -> tuple[int, int]:
    """只数文件 + chunk，不调 chroma / embedding"""
    files = discover_files()
    total = 0
    by_cat: dict[str, int] = {}
    for f in files:
        rel = _rel_path(f)
        try:
            text = f.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("跳过 %s: %s", rel, exc)
            continue
        chunks = chunk_text(text)
        total += len(chunks)
        cat = infer_category(rel)
        by_cat[cat] = by_cat.get(cat, 0) + len(chunks)
    logger.info("dry-run：%d 文件 / %d chunks", len(files), total)
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        logger.info("  category=%s  chunks=%d", cat, n)
    return len(files), total


# ---- CLI ----

# Reindex mutex: 防多个 update-local-ai.ps1 并发跑 index_docs.py 撞坏 chroma。
# 参考 scripts/codegraph/rebuild_index.ps1 的 .rebuild.lock 模式; reindex 并发
# 无意义不必等, 后到者直接报错退出。stale lock (> 30 分钟) 抢占。
_REINDEX_LOCK_PATH = PERSIST_DIR / ".reindex.lock"
_REINDEX_LOCK_STALE_SEC = 30 * 60


def _try_acquire_reindex_lock() -> tuple | None:
    """原子创建 .reindex.lock; 返回 (fd, path) 表示拿到, None 表示拒绝。"""
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(_REINDEX_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
        return (fd, _REINDEX_LOCK_PATH)
    except FileExistsError:
        # Stale lock 抢占
        try:
            age = time.time() - _REINDEX_LOCK_PATH.stat().st_mtime
            if age > _REINDEX_LOCK_STALE_SEC:
                logger.warning("removing stale reindex lock (age=%ds)", int(age))
                _REINDEX_LOCK_PATH.unlink(missing_ok=True)
                try:
                    fd = os.open(str(_REINDEX_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
                    return (fd, _REINDEX_LOCK_PATH)
                except FileExistsError:
                    pass
        except OSError:
            pass
        return None


def _release_reindex_lock(lock) -> None:
    if lock is None:
        return
    fd, path = lock
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Chroma 平台文档索引器")
    parser.add_argument("--dry-run", action="store_true", help="只数文件不入库")
    parser.add_argument("--force", action="store_true", help="删除已有 collection 重建")
    args = parser.parse_args()

    # dry-run 不写库, 不需要互斥
    lock = None
    if not args.dry_run:
        lock = _try_acquire_reindex_lock()
        if lock is None:
            logger.error(
                "另一个 reindex 已在跑 (lock=%s); 并发写 chroma 会损坏数据库, 拒绝执行。"
                " 等先前 reindex 完成或确认 stale 后手动删 lock 重试。",
                _REINDEX_LOCK_PATH,
            )
            return 2

    try:
        t0 = time.time()
        if args.dry_run:
            files, chunks = dry_run()
        else:
            files, chunks = index(force=args.force)
        elapsed = time.time() - t0
        if args.dry_run:
            logger.info("完成：%d 文件 / %d chunks / 耗时 %.2fs（dry-run）", files, chunks, elapsed)
        else:
            logger.info("完成: 本次处理 %d 文件 / %d chunks / 耗时 %.2fs", files, chunks, elapsed)
        return 0
    finally:
        _release_reindex_lock(lock)


if __name__ == "__main__":
    sys.exit(main())
