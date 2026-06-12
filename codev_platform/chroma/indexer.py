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
import os
import re
import sys
import time
from pathlib import Path
from collections.abc import Iterable

# ---- 配置 ----

# PLATFORM_ROOT / DOC_PATTERNS / EXCLUDE_* / logger 抽到 _index_config.py 叶子
# (破 indexer ↔ _discover 双向 import 脆弱; _discover 改从叶子取)。
from codev_platform.chroma._index_config import PLATFORM_ROOT, logger  # noqa: E402,F401

# 多项目命名: <project_id>__platform_docs (与 server.py 共用 resolver)
# PERSIST_DIR 走 codev_platform.core.paths.chroma_dir() — 它读 PLATFORM_DATA_DIR env,
# 确保多 project 写到 SHARED chroma DB, 而非各自 PLATFORM_ROOT/data/chroma.
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.paths import chroma_collection_name, chroma_dir, chroma_docs_dir

try:
    PROJECT_ID = resolve_local(PLATFORM_ROOT)
except ProjectIdError as _pid_exc:
    print(f"[index_docs] FATAL: {_pid_exc!s}", file=sys.stderr, flush=True)
    sys.exit(1)

# chroma_dir() 内部走 _business_repo_root() (从 cwd 向上找 .claude/project.json),
# 但 cwd 此时可能是 platform/tools/chroma/ (post-commit hook 起点), 不是业务仓根.
# 因此 PLATFORM_DATA_DIR env 必须设, 让 data_root() 直接吃 env, 跳过 cwd 推导.
# PERSIST_DIR 每项目独立库 docs/<pid>/ (隔离 chromadb 多 collection compaction 损坏);
# DB / manifest / .last_build 戳都落这; 全局 .reindex.lock 仍在 chroma_dir() 根 (GPU 串行化)。
PERSIST_DIR = chroma_docs_dir(PROJECT_ID)
COLLECTION_NAME = chroma_collection_name(PROJECT_ID, "platform_docs")
# 模型路径优先级: env > config(models.embed_path) > ~/models 共享 > 仓内 MiniLM fallback.
# 不 hardcode 盘符: 实际路径写 ~/.codev-platform/config.json (跨平台/跨机器)。
from codev_platform.core.config import load_config as _load_cfg, get as _cfg_get
_cfg = _load_cfg()
_cfg_embed = _cfg_get(_cfg, "models.embed_path")
_shared = Path(_cfg_embed).expanduser() if _cfg_embed else (Path.home() / "models" / "Qwen3-Embedding-0.6B")
_MINILM_INREPO = PLATFORM_ROOT / "models" / "paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_EMBEDDING_MODEL = _shared if _shared.exists() else _MINILM_INREPO
EMBEDDING_MODEL = str(Path(os.getenv("PLATFORM_EMBED_MODEL_PATH", str(DEFAULT_EMBEDDING_MODEL))).expanduser().resolve())
EMBEDDING_DEVICE = os.getenv("PLATFORM_EMBED_DEVICE", _cfg_get(_cfg, "models.embed_device", "cuda"))
# 索引侧批大小 — 显存敏感(本地小卡 16;服务器大显存可调大提吞吐)。
# env > config models.embed_batch_size > 16(换机器只改 config, 代码不动)。
EMBEDDING_BATCH_SIZE = int(os.getenv("PLATFORM_EMBED_BATCH_SIZE", str(_cfg_get(_cfg, "models.embed_batch_size", 16))))

# DOC_PATTERNS / EXCLUDE_PARTS / EXCLUDE_WHITELIST_SUBPATHS 已抽到 _index_config.py 叶子 (见上方 import)。

# chunk 切分常量 + 函数已迁到 codev_platform.chroma.chunking (跨项目通用算法).
# 本地保留 import 别名兼容已有 manifest 校验逻辑 (params.chunk_*_max 比对).
from codev_platform.chroma.chunking import (  # noqa: E402
    CHUNK_HARD_MAX,
    CHUNK_TARGET_MAX,
    chunk_text,
    file_sha256 as _file_sha256_from_codev,
)

# manifest schema version — 改 chunk 策略 / metadata 结构时升级,自动触发 full rebuild
MANIFEST_VERSION = 1
# per-project manifest：COLLECTION_NAME 是 <project_id>__platform_docs，manifest 也必须按 project 隔离，
# 否则多项目轮流 reindex 会互相覆盖同一份全局 manifest，导致增量退化 / 留孤儿。
# 旧全局 index_manifest.json 自然废弃（首次 per-project reindex 重建，留着无害）。
MANIFEST_PATH = PERSIST_DIR / f"index_manifest.{PROJECT_ID}.json"

# logging 配置 + logger 已抽到 _index_config.py (见上方 import)。


# 文件发现 + 分类 (is_excluded / _load_project_index_config / discover_files / infer_category /
# infer_module / _rel_path) 已抽到 _discover.py。bottom-import: 上方常量 (PLATFORM_ROOT /
# DOC_PATTERNS / EXCLUDE_*) + logger 已定义, _discover 从 indexer 取它们, 故无循环。
from codev_platform.chroma._discover import (  # noqa: E402
    is_excluded,  # noqa: F401
    _load_project_index_config,  # noqa: F401
    discover_files,
    infer_category,
    infer_module,
    _rel_path,
)


# ---- 索引主流程 ----


def _strip_html(html: str) -> str:
    """html → 可索引纯文本(剥 script/style/注释/标签 + 解 HTML 实体)。无外部依赖, 正则够用于检索。"""
    from html import unescape
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)  # 整块去脚本/样式
    html = re.sub(r"(?s)<!--.*?-->", " ", html)                  # 注释
    html = re.sub(r"(?s)<[^>]+>", " ", html)                     # 标签
    text = unescape(html)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n[ \t\n]*\n", "\n\n", text).strip()


def _doc_text(rel: str, raw: str) -> str:
    """文档原文 → 可索引文本。html/htm 剥标签(否则标签是噪声); md/txt/rule 等纯文本原样。
    支持任何**文本类**文档(不止 markdown), 类型由 doc_patterns 决定(.claude/index.json 可声明)。"""
    return _strip_html(raw) if rel.lower().endswith((".html", ".htm")) else raw


def iter_chunks(files: list[Path]) -> Iterable[tuple[str, int, str, dict]]:
    """逐文件 → 逐 chunk 产出 (id, idx, content, metadata)。

    文档类型不限 markdown: md/txt/rule/html 等文本类都吃(chunk_text 对无标题文本走段落/字符切分;
    html 经 _doc_text 剥标签)。文件集由 discover_files(doc_patterns)决定。
    """
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

        chunks = chunk_text(_doc_text(rel, text))
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
    from codev_platform.chroma import ensure_wal  # 写时 search 读不被锁 (chromadb 默认 delete 模式会独占)
    ensure_wal(PERSIST_DIR)

    if force:
        try:
            client.delete_collection(COLLECTION_NAME)
            logger.info("--force：已删除旧 collection %s", COLLECTION_NAME)
        except Exception as exc:
            logger.debug("--force 删除 collection %s 跳过(可能不存在): %s", COLLECTION_NAME, exc)
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
        except Exception as exc:
            logger.debug("legacy collection 探测跳过: %s", exc)
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
        except Exception as exc:
            logger.debug("bootstrap 清空 collection %s 跳过(可能不存在): %s", COLLECTION_NAME, exc)

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
        except Exception as exc:
            logger.debug("params 不一致 rebuild 删 collection %s 跳过(可能不存在): %s", COLLECTION_NAME, exc)
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
    # chromadb 1.5.9 compaction bug: 对同一 collection 做"多次"upsert(flush)、而库里已存在别的
    # collection 时, 其 compaction 会写坏 sqlite(Error purging logs / Failed to pull logs from the
    # log store / database disk image is malformed)。单次 upsert 不触发。故默认 BATCH 调大到 5 万
    # (现实 collection 几千 chunk → 一次性 upsert), 全量 --force 重建多 collection 库才安全。
    # env PLATFORM_INDEX_FLUSH_BATCH 可覆盖: 超大库(> 5 万 chunk)须设到大于其 chunk 数以强制单 upsert,
    # 或显存/内存紧时调小(代价: 多 flush 在多 collection 库上有损坏风险)。
    # 详见 docs/incidents/2026-06-05-chromadb-multiflush-compaction.md。
    BATCH = int(os.getenv("PLATFORM_INDEX_FLUSH_BATCH", "50000"))

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
                ids_buf.clear()
                docs_buf.clear()
                metas_buf.clear()
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
# 锁协议已抽到 codev_platform.chroma._reindex_lock(参数化 lock_dir, recall 代码向量索引复用)。
# 本处保留薄 wrapper 绑定 PERSIST_DIR, 行为与原实现一致。
from codev_platform.chroma._reindex_lock import (  # noqa: E402
    REINDEX_LOCK_STALE_SEC as _REINDEX_LOCK_STALE_SEC,  # noqa: F401
)
from codev_platform.chroma._reindex_lock import (
    release_reindex_lock as _release_reindex_lock,
)
from codev_platform.chroma._reindex_lock import (
    try_acquire_reindex_lock as _try_acquire_shared_lock,
)

# 锁落 chroma_dir() 根 (全局), 非 per-project PERSIST_DIR: 每项目库虽已隔离不会互相损坏,
# 但 indexer 各自加载 GPU embedding 模型, 并发跑会 OOM。全局锁串行化所有项目的 chroma 重建。
_REINDEX_LOCK_DIR = chroma_dir()
_REINDEX_LOCK_PATH = _REINDEX_LOCK_DIR / ".reindex.lock"


def _try_acquire_reindex_lock() -> tuple | None:
    """原子创建 chroma_dir()/.reindex.lock (全局 GPU 串行化); 委托共享实现。返回 (fd, path) 或 None。"""
    return _try_acquire_shared_lock(_REINDEX_LOCK_DIR)


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
