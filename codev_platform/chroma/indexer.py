"""把平台规则与设计文档切块写入 Chroma，供 RAG 查询使用。

用法：
    python tools/chroma/index_docs.py                # 全量增量 upsert
    python tools/chroma/index_docs.py --dry-run      # 只数文件 + chunk 数，不入库
    python tools/chroma/index_docs.py --force        # 删除已有 collection 重建

"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from collections.abc import Iterable, Mapping

# PLATFORM_ROOT / DOC_PATTERNS / EXCLUDE_* / logger 抽到 _index_config.py 叶子
# (破 indexer ↔ _discover 双向 import 脆弱; _discover 改从叶子取)。
from codev_platform.chroma._index_config import PLATFORM_ROOT, logger  # noqa: E402,F401
from codev_platform.chroma.batch_policy import resolve_flush_batch_size

# PERSIST_DIR 走 codev_platform.core.paths.chroma_dir() — 它读 PLATFORM_DATA_DIR env,
# 确保多 project 写到 SHARED chroma DB, 而非各自 PLATFORM_ROOT/data/chroma.
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.paths import chroma_collection_name, chroma_dir, chroma_docs_dir
from codev_platform.chroma.collection_integrity import (
    delete_collection_ids as _delete_chunks,
    iter_manifest_chunk_ids,
    open_collection_for_proof,
    verify_collection_ids,
)
from codev_platform.chroma.document_manifest import (
    MANIFEST_VERSION,
    empty_manifest as _empty_manifest,
    load_manifest as _load_manifest,
    save_manifest as _save_manifest,
)
from codev_platform.chroma.document_proof import verify_document_collection_in_subprocess
from codev_platform.chroma.embedding_limits import (
    apply_embed_max_seq_length,
    resolve_embed_max_seq_length,
)
from codev_platform.chroma.index_input import (
    hash_index_file,
    proven_index_input,
    read_index_text,
    read_verified_index_text,
)
from codev_platform.core.runtime_interpreter import verify_reindex_runtime_revision
from codev_platform.core.index_handoff import begin_build, commit_build, gc_builds, new_build_id, resolve_current
from codev_platform.index_manifest import git_head

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
EMBEDDING_MAX_SEQ_LENGTH = resolve_embed_max_seq_length(
    EMBEDDING_DEVICE,
    os.getenv("PLATFORM_EMBED_MAX_SEQ_LENGTH")
    or _cfg_get(_cfg, "models.embed_max_seq_length"),
)
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

# per-project manifest：COLLECTION_NAME 是 <project_id>__platform_docs，manifest 也必须按 project 隔离，
# 否则多项目轮流 reindex 会互相覆盖同一份全局 manifest，导致增量退化 / 留孤儿。
# 旧全局 index_manifest.json 自然废弃（首次 per-project reindex 重建，留着无害）。
_MANIFEST_NAME = f"index_manifest.{PROJECT_ID}.json"   # manifest 跟 build dir 走(atomic handoff)
_DOCS_KEEP = 2   # full handoff 保最近 2 个 build(current + 上代, 给 reader 旧连接读完)

# logging 配置 + logger 已抽到 _index_config.py (见上方 import)。


# 文件发现与分类已抽到 _discover.py；配置常量统一来自叶子 _index_config.py。
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


def iter_chunks(
    files: list[Path], *, manifest_path: Path | None = None,
    expected_sha: Mapping[str, str] | None = None,
) -> Iterable[tuple[str, int, str, dict]]:
    """逐文件 → 逐 chunk 产出 (id, idx, content, metadata)。

    文档类型不限 markdown: md/txt/rule/html 等文本类都吃(chunk_text 对无标题文本走段落/字符切分;
    html 经 _doc_text 剥标签)。文件集由 discover_files(doc_patterns)决定。
    """
    for f in files:
        rel = _rel_path(f)
        if expected_sha is not None:
            text = read_verified_index_text(
                f, label=rel, expected_sha=expected_sha, manifest_path=manifest_path,
            )
        else:
            text = read_index_text(
                f, label=rel, logger=logger, manifest_path=manifest_path,
            )
        if text is None:
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


def _current_params(dim: int, model_name: str) -> dict:
    """当前 embed/chunk 参数指纹 — 不一致触发 full rebuild"""
    return {
        "manifest_version": MANIFEST_VERSION,
        "embed_model": model_name,
        "embed_dim": dim,
        "embed_max_seq_length": EMBEDDING_MAX_SEQ_LENGTH,
        "chunk_target_max": CHUNK_TARGET_MAX,
        "chunk_hard_max": CHUNK_HARD_MAX,
    }


def _manifest_static_params_match(manifest: dict) -> bool:
    """不加载模型即可判断的参数是否匹配。"""
    params = manifest.get("params") or {}
    return (
        params.get("manifest_version") == MANIFEST_VERSION
        and params.get("embed_model") == Path(EMBEDDING_MODEL).name
        and params.get("embed_max_seq_length") == EMBEDDING_MAX_SEQ_LENGTH
        and params.get("chunk_target_max") == CHUNK_TARGET_MAX
        and params.get("chunk_hard_max") == CHUNK_HARD_MAX
    )


def _scan_changes(
    files: list[Path], manifest: dict, *, manifest_path: Path | None = None,
) -> tuple[list[Path], list[str], dict[str, str]]:
    """对比当前文件与 manifest,返回 (变更文件列表, 已删除文件 rel 列表, rel→sha256 map)

    变更 = 新增 + sha256 不一致;未变 = 跳过(零成本)
    """
    changed: list[Path] = []
    new_sha: dict[str, str] = {}
    current_rels: set[str] = set()
    for f in files:
        rel = _rel_path(f)
        current_rels.add(rel)
        sha = hash_index_file(
            f, label=rel, hasher=_file_sha256, logger=logger, manifest_path=manifest_path,
        )
        if sha is None:
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

    base = PERSIST_DIR                        # handoff base 根(.last_build 戳 / .reindex.lock 落这)
    base.mkdir(parents=True, exist_ok=True)
    from codev_platform.chroma import ensure_wal  # 写时 search 读不被锁(chromadb 默认 delete 模式会独占)

    # 读 manifest 从**当前 build**(handoff: 无 pointer 退 base); force 直接全量不读。
    # full → 新 side 本空, 故全程**无 delete_collection**(旧 collection 留旧 build, commit 后由 gc 清)。
    current_dir = resolve_current(base)
    current_manifest_path = current_dir / _MANIFEST_NAME
    if force:
        manifest, manifest_loaded = _empty_manifest(), False
    else:
        manifest, manifest_loaded = _load_manifest(current_manifest_path)

    files = discover_files()
    logger.info("发现 %d 个 markdown 文件", len(files))

    # 首次接入 manifest 或 manifest 损坏时,旧 collection 可能包含已删除文件 /
    # 旧 chunk 策略留下的残留 ids。清 collection 后全量回填,避免旧知识继续可搜。
    full_rebuild = force or not manifest_loaded   # 统一 full 标志(后续 params 不一致再置位)

    # 先用文件内容 sha256 算变更 — 不加载模型,纯 IO 操作 ~毫秒级
    changed_files, deleted_rels, new_sha_map = _scan_changes(
        files, manifest, manifest_path=current_manifest_path,
    )
    if full_rebuild:
        changed_files = list(files)
        deleted_rels = []
    elif not changed_files and not deleted_rels and not _manifest_static_params_match(manifest):
        logger.warning("manifest 静态参数不一致,准备全量校验并重建")
        full_rebuild = True
        changed_files = list(files)
    logger.info("增量: 变更 %d / 删除 %d / 未变 %d",
                len(changed_files), len(deleted_rels), len(files) - len(changed_files))

    # 早返回 — 完全无变更时不加载模型 / 不连 Chroma 写侧(注意 collection 元数据已存在,跳过即可)
    if not full_rebuild and not changed_files and not deleted_rels:
        # 即便 0 变更也要写构建戳,让 MCP server 知道"已最新"(touch mtime)
        # 但 manifest 中的 params/embed_dim 在第一次完整 build 后就稳定,直接复用
        cached_params = manifest.get("params") or {}
        collection = _open_collection_for_proof(
            chromadb.PersistentClient, current_dir, current_manifest_path,
        )
        expected_chunks = _verify_collection_ids(collection, manifest, current_manifest_path)
        _publish_build(base, None,
            files_count=len(files),
            chunks=expected_chunks,
            dim=int(cached_params.get("embed_dim", 0)) or 1024,
            model_name=cached_params.get("embed_model") or Path(EMBEDDING_MODEL).name,
        )
        logger.info("无变更,跳过模型加载 + Chroma 写入")
        return 0, 0

    # 有变更才加载模型
    from sentence_transformers import SentenceTransformer
    logger.info("加载模型 %s (device=%s)", EMBEDDING_MODEL, EMBEDDING_DEVICE)
    model = SentenceTransformer(EMBEDDING_MODEL, device=EMBEDDING_DEVICE)
    apply_embed_max_seq_length(model, EMBEDDING_MAX_SEQ_LENGTH)
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
        full_rebuild = True
        manifest = _empty_manifest()
        changed_files = list(files)
        deleted_rels = []

    collection_metadata = {
        "hnsw:space": "cosine",
        "embed_model": EMBEDDING_MODEL,
        "embed_model_name": model_name,
        "embed_device": EMBEDDING_DEVICE,
        "embedding_dim": dim,
        "max_seq_length": int(max_seq_length) if max_seq_length else 0,
        "query_prompt_enabled": query_prompt_enabled,
    }

    # ---- 唯一一处决定 build_dir(full 判定全部完成后)----
    if full_rebuild:
        gc_builds(base, keep=_DOCS_KEEP)          # 建新 side 前清旧(reader 那时已 reload)
        bid = new_build_id(git_head(PLATFORM_ROOT), str(int(time.time())))
        build_dir = begin_build(base, bid)        # full → 干净 side; reader 仍读旧 current
    else:
        bid = None
        build_dir = resolve_current(base)         # 增量 → 当前 build 原地改, 不 commit
    build_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = build_dir / _MANIFEST_NAME
    client = chromadb.PersistentClient(path=str(build_dir))
    ensure_wal(build_dir)

    # 增量更新会先删旧 chunks；待重写文件必须在任何 collection 变更前证明写入能力。
    configured_batch = int(os.getenv("PLATFORM_INDEX_FLUSH_BATCH", "50000"))
    batch = resolve_flush_batch_size(client, configured_batch) if changed_files else None
    if batch is not None:
        logger.info("Chroma 写入批量: 配置上限=%d, 后端钳制后=%d",
                    configured_batch, batch)

    col = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata=collection_metadata,
    )

    # 步骤 1: 删已删除文件的所有残留 chunks
    for rel in deleted_rels:
        old_count = manifest["files"][rel].get("chunk_count", 0)
        if old_count > 0:
            _delete_chunks(col, [f"{rel}#{i}" for i in range(old_count)], manifest_path)
            logger.info("删除文件 %s 的 %d 个旧 chunks", rel, old_count)
        manifest["files"].pop(rel, None)

    # 步骤 2: 变更文件先删旧 chunks(防 chunk 数变少时残留),再写新 chunks
    ids_buf: list[str] = []
    docs_buf: list[str] = []
    metas_buf: list[dict] = []
    total = 0
    # 旧版同库多 collection 的多次 flush 会触发 Chroma 1.5.9 compaction 损坏；当前已按
    # project/build 独立数据库，side build 内只有一个 collection，可以安全分批。批量仍必须
    # 服从 Chroma 根据 SQLite 编译参数给出的动态硬上限，不能写死某台机器的数值。
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
            _delete_chunks(col, [f"{rel}#{i}" for i in range(old_count)], manifest_path)

        # 切新 chunks → 入 batch
        per_file_count = 0
        for chunk_id, _idx, content, meta in iter_chunks(
            [f], manifest_path=manifest_path,
            expected_sha=new_sha_map if proven_index_input() else None,
        ):
            ids_buf.append(chunk_id)
            docs_buf.append(content)
            metas_buf.append(meta)
            per_file_count += 1
            total += 1
            if batch is None:  # changed_files 非空时已在 collection 变更前证明
                raise RuntimeError("Chroma 写入批量尚未完成能力证明")
            if len(ids_buf) >= batch:
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
    _save_manifest(manifest, manifest_path)

    # 写构建戳:总文件 / 总 chunks 从 manifest 算(不只是本次变更的)
    total_files = len(manifest["files"])
    total_chunks = _verify_persisted_collection(
        client,
        build_dir,
        COLLECTION_NAME,
        manifest_path,
    )
    _publish_build(
        base, bid if full_rebuild else None,
        files_count=total_files, chunks=total_chunks, dim=dim, model_name=model_name,
    )

    return len(changed_files), total


def _verify_collection_ids(collection, manifest: dict, manifest_path: Path) -> int:
    """把文档 manifest 适配为通用 collection ID 集证明。"""
    return verify_collection_ids(
        collection, iter_manifest_chunk_ids(manifest), label="Chroma",
        invalidate=lambda: manifest_path.unlink(missing_ok=True),
    )


def _close_writer_before_proof(client: object, manifest_path: Path) -> None:
    """关闭本次 Chroma 写端，拒绝以同进程内存视图作为发布依据。"""
    closer = getattr(client, "close", None)
    if not callable(closer):
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError("Chroma 写入客户端缺少关闭接口，拒绝持久化完整性证明")
    try:
        closer()
    except Exception as exc:  # noqa: BLE001 - 未释放写端时不得发布 manifest 或 current
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError("Chroma 写入客户端关闭失败，拒绝持久化完整性证明") from exc


def _verify_persisted_collection(
    client: object,
    directory: Path,
    collection_name: str,
    manifest_path: Path,
) -> int:
    """关闭写端后由独立短进程读取持久化 collection 并证明完整性。"""
    _close_writer_before_proof(client, manifest_path)
    return verify_document_collection_in_subprocess(directory, collection_name, manifest_path)


def _open_collection_for_proof(client_factory, directory: Path, manifest_path: Path):
    return open_collection_for_proof(
        client_factory, directory, manifest_path, COLLECTION_NAME,
    )


def _write_text_atomic(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _write_build_stamp(files_count: int, chunks: int, dim: int, model_name: str) -> None:
    """写全局 reload stamp 与 per-project 健康状态 stamp。"""
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
    # 兼容戳先落，daemon 监听的 reload 戳最后原子替换，避免看见旧 current。
    _write_text_atomic(PERSIST_DIR / f".last_build.{PROJECT_ID}.json", serialized)
    _write_text_atomic(PERSIST_DIR / ".last_build.json", serialized)
    logger.info("写入构建戳 .last_build.json + .last_build.%s.json (chunks=%d)", PROJECT_ID, chunks)


def _publish_build(base: Path, build_id: str | None, **stamp) -> None:
    """先切 current，再用 reload stamp 通知 reader。"""
    if build_id is not None:
        commit_build(base, build_id)
    _write_build_stamp(**stamp)


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

# Reindex mutex 防并发写坏 Chroma；共享锁协议在 _reindex_lock，本处只绑定目录。
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
    verify_reindex_runtime_revision()

    if args.dry_run:
        return _run_indexer(args)
    from codev_platform.reindex.maintenance_gate import (
        maintenance_reindex_operation_permit,
    )

    with maintenance_reindex_operation_permit() as permitted:
        if permitted is not True:
            logger.error("reindex 维护窗口已启用；Chroma 索引写入被拒绝")
            return 1
        return _run_indexer(args)


def _run_indexer(args: argparse.Namespace) -> int:
    """在调用方已取得维护写许可后执行 Chroma 写锁与索引流程。"""

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
            print("proof: chroma ok", flush=True)
        return 0
    finally:
        _release_reindex_lock(lock)


if __name__ == "__main__":
    sys.exit(main())
