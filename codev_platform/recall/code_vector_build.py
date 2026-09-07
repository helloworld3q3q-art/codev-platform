"""代码向量索引的目标管理、checkpoint 与构建编排。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
import uuid

from codev_platform.chroma.collection_integrity import (
    CollectionIntegrityError,
    delete_ids_with_transient_compaction_retry,
    upsert_with_transient_compaction_retry,
)
from codev_platform.core.index_handoff import (
    begin_build,
    commit_build,
    gc_builds,
    new_build_id,
    resolve_current,
)
from codev_platform.recall.code_vector_checkpoint import (
    build_checkpoint_fingerprint,
    load_checkpoint_meta,
    storage_policy_matches,
    write_checkpoint_meta,
)
from codev_platform.recall.code_vector_checkpoint_candidates import (
    checkpoint_progress as _checkpoint_progress,
    find_resumable_checkpoint,
)
from codev_platform.recall.code_vector_chroma_config import (
    CODE_VEC_CHROMA_STORAGE_POLICY_VERSION,
    open_code_vec_collection,
)
from codev_platform.recall.code_vector_build_state import (
    DEFAULT_SKIP_KINDS,
    existing_chroma_healthy as _existing_chroma_healthy,
    read_enrich_mode as _read_enrich_mode,
    resolve_skip_kinds as _resolve_skip_kinds,
)
from codev_platform.recall.code_vector_build_target import (
    CodeVecBuildTarget as _CodeVecBuildTarget,
    close_writer_before_proof as _close_writer_before_proof,
)
from codev_platform.recall.code_vector_collection import collect_node_chunks
from codev_platform.recall.code_vector_io import write_json_atomic
from codev_platform.recall.code_vector_manifest import (
    load_incremental_manifest,
    valid_code_vector_manifest,
)
from codev_platform.recall.code_vector_paths import (
    MANIFEST_META_NAME,
    MANIFEST_NAME,
    code_vec_collection_name,
    code_vec_persist_dir,
)
from codev_platform.recall.code_vector_proof import _verify_collection_ids
from codev_platform.recall.code_vector_text import (
    _BASE_FIELD_MAX,
    _CHUNK_BODY_CHARS,
    _CHUNK_OVERLAP_LINES,
    _CLASS_HEAD_CHARS,
    _MAX_CHUNKS_PER_NODE,
    _TEXT_FIELDS,
    _diff_manifest,
    _node_chunks,
    _node_hash,
)


logger = logging.getLogger(__name__)
_CODE_VEC_KEEP = 1
_UPSERT_BATCH = 64
_DEFAULT_SKIP_KINDS = DEFAULT_SKIP_KINDS


class CodeVecLockBusy(RuntimeError):
    """同一项目的代码向量写锁正被其它构建持有。"""


class _CheckpointDimensionMismatch(RuntimeError):
    """续建库维度与当前嵌入器不一致。"""


@dataclass(frozen=True)
class BuildHooks:
    """门面注入的可替换构建协作者，避免编排反向依赖兼容层。"""

    checkpoint_fingerprint: Callable[..., dict[str, object]]
    existing_chroma_healthy: Callable[[Path], bool]
    load_manifest: Callable[[Path], tuple[dict, bool]]
    read_enrich_mode: Callable[[Path], bool | None]
    open_target: Callable[..., _CodeVecBuildTarget]
    resumable_manifest: Callable[[_CodeVecBuildTarget], dict]
    discard_target: Callable[[_CodeVecBuildTarget, Path, str], _CodeVecBuildTarget]
    collect_chunks: Callable[..., tuple[dict, dict, dict]]
    delete_stale: Callable[[object, list[str], Path], None]
    upsert: Callable[..., tuple[dict, int | None]]
    verify: Callable[[_CodeVecBuildTarget, dict, Path], int]
    diff_manifest: Callable[[dict, dict], tuple[list[str], list[str]]]
    write_json: Callable[[Path, object], None]
    write_checkpoint: Callable[..., None]
    publish: Callable[[Path, str], None]


def _collect_node_chunks(
    repo_specs,
    skip_kinds: frozenset,
    *,
    project_id: str | None = None,
    chunker: Callable = _node_chunks,
) -> tuple[dict, dict, dict]:
    """把文本切块策略注入多仓节点采集器。"""
    return collect_node_chunks(
        repo_specs,
        skip_kinds,
        project_id=project_id,
        chunker=chunker,
        hasher=_node_hash,
        logger=logger,
    )


def _collect_verified_node_chunks(
    repo_specs,
    skip_kinds: frozenset,
    manifest_path: Path | None,
    *,
    project_id: str,
    collector: Callable[..., tuple[dict, dict, dict]] = _collect_node_chunks,
    validator: Callable[[object], bool] = valid_code_vector_manifest,
) -> tuple[dict, dict, dict]:
    """采集节点并校验 manifest；失败时撤销可能被原地修改的成功标记。"""
    try:
        result = collector(repo_specs, skip_kinds, project_id=project_id)
        if not validator(result[0]):
            raise RuntimeError("code_vec manifest 超出资源或 schema 上限")
        return result
    except Exception:
        if manifest_path is not None:
            manifest_path.unlink(missing_ok=True)
        raise


def _find_resumable_full_build(
    base: Path,
    expected_fingerprint: dict[str, object] | None = None,
    *,
    health_check: Callable[[Path], bool] = _existing_chroma_healthy,
) -> Path | None:
    """返回与当前策略匹配且健康的最新未发布 side-build。"""
    return find_resumable_checkpoint(
        base, expected_fingerprint, health_check=health_check,
    )


def has_resumable_full_build(
    project_id: str,
    *,
    persist_dir_resolver: Callable[[str], Path] = code_vec_persist_dir,
    finder: Callable[..., Path | None] = _find_resumable_full_build,
) -> bool:
    """判断项目是否存在可恢复的全量 side-build。"""
    return finder(persist_dir_resolver(project_id)) is not None


def code_vec_checkpoint_progress(
    project_id: str,
    *,
    persist_dir_resolver: Callable[[str], Path] = code_vec_persist_dir,
    health_check: Callable[[Path], bool] = _existing_chroma_healthy,
) -> dict[str, int]:
    """按严格构建指纹返回最新可恢复 side-build 的 checkpoint。"""
    return _checkpoint_progress(
        persist_dir_resolver(project_id), health_check=health_check,
    )


def _open_code_vec_build_target(
    base: Path,
    project_id: str,
    *,
    full: bool,
    fingerprint: dict[str, object],
    allow_resume: bool = True,
    finder: Callable[..., Path | None] = _find_resumable_full_build,
) -> _CodeVecBuildTarget:
    """全量构建打开 side-build，增量构建打开当前目录。"""
    import chromadb
    from codev_platform.chroma import ensure_wal

    resumed = False
    embedding_dimension = None
    if full:
        resumable = finder(base, fingerprint) if allow_resume else None
        if resumable is not None:
            build_dir = resumable
            build_id = resumable.name
            resumed = True
            meta = load_checkpoint_meta(resumable / MANIFEST_META_NAME)
            embedding_dimension = int(meta["embedding_dimension"]) if meta else None
        else:
            gc_builds(base, keep=_CODE_VEC_KEEP)
            build_id = new_build_id(None, uuid.uuid4().hex)
            build_dir = begin_build(base, build_id)
    else:
        build_id = None
        build_dir = resolve_current(base)
    build_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = build_dir / MANIFEST_NAME
    meta_path = build_dir / MANIFEST_META_NAME
    client = chromadb.PersistentClient(path=str(build_dir))
    ensure_wal(build_dir)
    collection = open_code_vec_collection(client, project_id)
    close_writer = getattr(client, "close", None)
    if not callable(close_writer):
        raise RuntimeError("code_vec 持久化客户端缺少关闭接口，拒绝跨进程完整性证明")
    return _CodeVecBuildTarget(
        build_dir=build_dir,
        manifest_path=manifest_path,
        meta_path=meta_path,
        collection=collection,
        build_id=build_id,
        resumed=resumed,
        fingerprint=fingerprint,
        embedding_dimension=embedding_dimension,
        close_writer=close_writer,
    )


def _resumable_manifest(
    target: _CodeVecBuildTarget,
    *,
    verifier: Callable[[_CodeVecBuildTarget, dict, Path], int] = _verify_collection_ids,
) -> dict:
    """证明 side-build checkpoint 与向量库一致后返回其 manifest。"""
    if not target.resumed:
        return {}
    manifest, valid = load_incremental_manifest(target.manifest_path)
    if not valid:
        raise RuntimeError("code_vec 续跑 checkpoint 无效")
    verifier(target, manifest, target.manifest_path)
    return manifest


def _discard_and_open_clean_full_target(
    target: _CodeVecBuildTarget,
    base: Path,
    project_id: str,
    *,
    opener: Callable[..., _CodeVecBuildTarget] = _open_code_vec_build_target,
) -> _CodeVecBuildTarget:
    """删除未发布 side-build 并创建全新目标。"""
    import shutil

    if target.build_id is None:
        raise RuntimeError("code_vec full side-build 身份缺失")
    _close_writer_before_proof(target)
    shutil.rmtree(target.build_dir, ignore_errors=True)
    return opener(
        base,
        project_id,
        full=True,
        fingerprint=target.fingerprint,
        allow_resume=False,
    )


def _embedding_batch_dimension(embeddings: list[list[float]], expected: int) -> int:
    """验证一批嵌入的数量与维度并返回统一维度。"""
    if len(embeddings) != expected or not embeddings:
        raise RuntimeError("code_vec 嵌入返回数量无效")
    if any(not isinstance(vector, list) or not vector for vector in embeddings):
        raise RuntimeError("code_vec 嵌入向量无效")
    dimensions = {len(vector) for vector in embeddings}
    if len(dimensions) != 1:
        raise RuntimeError("code_vec 嵌入维度不一致")
    return next(iter(dimensions))


def _upsert_with_checkpoints(
    target: _CodeVecBuildTarget,
    embedder,
    *,
    changed: list[str],
    persisted: dict,
    manifest_by_id: dict,
    text_by_id: dict,
    meta_by_id: dict,
    writer: Callable[[Path, object], None] = write_json_atomic,
    checkpoint_writer: Callable[..., None] = write_checkpoint_meta,
    batch_size: int = _UPSERT_BATCH,
) -> tuple[dict, int | None]:
    """分批嵌入、写库并持久化可恢复 checkpoint。"""
    embedding_dimension = target.embedding_dimension
    for index in range(0, len(changed), batch_size):
        chunk = changed[index : index + batch_size]
        embeddings = embedder.encode_batch([text_by_id[node_id] for node_id in chunk])
        batch_dimension = _embedding_batch_dimension(embeddings, len(chunk))
        if embedding_dimension is None:
            embedding_dimension = batch_dimension
        elif batch_dimension != embedding_dimension:
            raise _CheckpointDimensionMismatch("code_vec checkpoint 与当前嵌入维度不一致")
        upsert_with_transient_compaction_retry(
            target.collection, chunk, embeddings,
            [text_by_id[node_id] for node_id in chunk],
            [meta_by_id[node_id] for node_id in chunk],
        )
        for node_id in chunk:
            persisted[node_id] = manifest_by_id[node_id]
        if target.build_id is not None:
            writer(target.manifest_path, persisted)
            checkpoint_writer(target.meta_path, target.fingerprint,
                              embedding_dimension=embedding_dimension,
                              checkpoint_entries=len(persisted))
        suffix = " (manifest checkpointed)" if target.build_id is not None else ""
        logger.info("[code_vec] upserted %d/%d%s", min(index + batch_size, len(changed)),
                    len(changed), suffix)
    return persisted, embedding_dimension


def _delete_stale_nodes(collection, node_ids: list[str], manifest_path: Path) -> None:
    """删除失效向量；存储失败时撤销成功 manifest。"""
    if not node_ids:
        return
    try:
        delete_ids_with_transient_compaction_retry(collection, node_ids)
    except Exception as exc:  # noqa: BLE001 -- 删除失败必须使本次构建失败
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError("code_vec 删除旧节点失败") from exc


def _default_hooks() -> BuildHooks:
    """组装不经过兼容门面的默认构建协作者。"""
    return BuildHooks(
        checkpoint_fingerprint=build_checkpoint_fingerprint,
        existing_chroma_healthy=_existing_chroma_healthy,
        load_manifest=load_incremental_manifest,
        read_enrich_mode=_read_enrich_mode,
        open_target=_open_code_vec_build_target,
        resumable_manifest=_resumable_manifest,
        discard_target=_discard_and_open_clean_full_target,
        collect_chunks=_collect_verified_node_chunks,
        delete_stale=_delete_stale_nodes,
        upsert=_upsert_with_checkpoints,
        verify=_verify_collection_ids,
        diff_manifest=_diff_manifest,
        write_json=write_json_atomic,
        write_checkpoint=write_checkpoint_meta,
        publish=commit_build,
    )


def _open_clean_target_after_bad_resume(
    target: _CodeVecBuildTarget,
    base: Path,
    project_id: str,
    hooks: BuildHooks,
) -> tuple[_CodeVecBuildTarget, dict]:
    """读取续跑 manifest；未发布目标无法证明完整时改用干净目标。"""
    try:
        return target, hooks.resumable_manifest(target)
    except (CollectionIntegrityError, RuntimeError):
        if target.build_id is None:
            raise
        return hooks.discard_target(target, base, project_id), {}


def _recover_incremental_delete_failure(
    target: _CodeVecBuildTarget,
    base: Path,
    project_id: str,
    fingerprint: dict[str, object],
    new_manifest: dict,
    hooks: BuildHooks,
) -> tuple[_CodeVecBuildTarget, dict, list[str], list[str]]:
    """增量删除失败时转 side-build，并废弃无法清理的续跑库。"""
    _close_writer_before_proof(target)
    target = hooks.open_target(base, project_id, full=True, fingerprint=fingerprint)
    target, persisted = _open_clean_target_after_bad_resume(target, base, project_id, hooks)
    changed, deleted = hooks.diff_manifest(persisted, new_manifest)
    try:
        hooks.delete_stale(target.collection, deleted, target.manifest_path)
    except RuntimeError:
        target = hooks.discard_target(target, base, project_id)
        persisted = {}
        changed, deleted = hooks.diff_manifest({}, new_manifest)
    return target, persisted, changed, deleted


def _build_locked(
    project_id: str,
    persist,
    *,
    incremental: bool,
    hooks: BuildHooks | None = None,
) -> int:
    """在已持项目写锁的前提下构建并发布代码向量索引。"""
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.core.config import load_config
    from codev_platform.core.repos import project_repo_specs

    operations = hooks or _default_hooks()
    config = load_config()
    skip_kinds = _resolve_skip_kinds(config)
    embedder = build_code_vec_embedder(config)
    if embedder is None:
        raise RuntimeError(
            "embedder 不可用: 装 sentence-transformers + 配 models.embed_path(qwen-local), "
            "或设 recall.code_vec.embed_backend=remote 接 chroma daemon /embed。",
        )

    base = Path(persist)
    current_dir = resolve_current(base)
    current_manifest_path = current_dir / MANIFEST_NAME
    current_meta_path = current_dir / MANIFEST_META_NAME
    repo_specs = project_repo_specs(project_id)
    enrich_now = bool(repo_specs)
    fingerprint = operations.checkpoint_fingerprint(
        config,
        skip_kinds=skip_kinds,
        enrich=enrich_now,
        chunk_policy={
            "schema_version": 1,
            "chroma_storage_policy_version": CODE_VEC_CHROMA_STORAGE_POLICY_VERSION,
            "text_fields": list(_TEXT_FIELDS),
            "base_field_max": _BASE_FIELD_MAX,
            "body_chars": _CHUNK_BODY_CHARS,
            "overlap_lines": _CHUNK_OVERLAP_LINES,
            "class_head_chars": _CLASS_HEAD_CHARS,
            "max_chunks_per_node": _MAX_CHUNKS_PER_NODE,
        },
        repo_root=repo_specs[0].root if repo_specs else None,
    )
    full = (not incremental) or (not current_manifest_path.exists())
    old_manifest: dict = {}
    if not full:
        old_manifest, loaded = operations.load_manifest(current_manifest_path)
        full = not loaded
    if not full and incremental and not storage_policy_matches(current_meta_path):
        logger.warning("[code_vec] %s: Chroma 存储策略已升级，转全量 side-build", project_id)
        full = True
    if not full and incremental:
        old_enrich = operations.read_enrich_mode(current_meta_path)
        if old_enrich is not None and old_enrich != enrich_now:
            logger.warning(
                "[code_vec] %s: 源码富化模式 %s->%s(repo 解析翻转), 强制全量重建",
                project_id,
                old_enrich,
                enrich_now,
            )
            full = True
    if not full and incremental and not operations.existing_chroma_healthy(current_dir):
        logger.warning("[code_vec] %s: 当前 build 探活失败，转全量重建", project_id)
        full = True

    target = operations.open_target(base, project_id, full=full, fingerprint=fingerprint)
    if full:
        target, persisted_manifest = _open_clean_target_after_bad_resume(
            target,
            base,
            project_id,
            operations,
        )
    else:
        persisted_manifest = old_manifest

    logger.info(
        "[code_vec] %s: repos=%s (源码富化 %s)",
        project_id,
        [str(spec.root) for spec in repo_specs],
        "on" if repo_specs else "off",
    )
    new_manifest, text_by_id, meta_by_id = operations.collect_chunks(
        repo_specs,
        skip_kinds,
        None if full else target.manifest_path,
        project_id=project_id,
    )
    changed, deleted = operations.diff_manifest(persisted_manifest, new_manifest)
    # current 是原地增量：写前撤销成功标记，确保 SIGKILL 也不会暴露半写库。
    if not full and (changed or deleted):
        target.manifest_path.unlink(missing_ok=True)
    try:
        operations.delete_stale(target.collection, deleted, target.manifest_path)
    except RuntimeError:
        if full:
            raise
        logger.warning("[code_vec] %s: 增量删除旧节点失败，转 full side-build 自愈", project_id)
        full = True
        target, persisted_manifest, changed, deleted = _recover_incremental_delete_failure(
            target,
            base,
            project_id,
            fingerprint,
            new_manifest,
            operations,
        )

    deleted_set = set(deleted)
    persisted = {
        node_id: digest
        for node_id, digest in persisted_manifest.items()
        if node_id not in deleted_set
    }
    try:
        persisted, embedding_dimension = operations.upsert(
            target,
            embedder,
            changed=changed,
            persisted=persisted,
            manifest_by_id=new_manifest,
            text_by_id=text_by_id,
            meta_by_id=meta_by_id,
        )
    except _CheckpointDimensionMismatch:
        if not full:
            target.manifest_path.unlink(missing_ok=True)
            raise
        logger.warning("[code_vec] %s: checkpoint 维度漂移，废弃 side-build 后全量自愈", project_id)
        target = operations.discard_target(target, base, project_id)
        changed, deleted, persisted = list(new_manifest), [], {}
        persisted, embedding_dimension = operations.upsert(
            target,
            embedder,
            changed=changed,
            persisted=persisted,
            manifest_by_id=new_manifest,
            text_by_id=text_by_id,
            meta_by_id=meta_by_id,
        )
    except Exception:
        if not full:
            target.manifest_path.unlink(missing_ok=True)
        raise

    try:
        _close_writer_before_proof(target)
        operations.verify(target, new_manifest, target.manifest_path)
        if new_manifest and embedding_dimension is not None:
            operations.write_checkpoint(
                target.meta_path, target.fingerprint,
                embedding_dimension=embedding_dimension,
                checkpoint_entries=len(new_manifest),
            )
        else:
            operations.write_json(target.meta_path, {"enrich": enrich_now,
                                  "storage_policy_version": CODE_VEC_CHROMA_STORAGE_POLICY_VERSION})
        # manifest 是 current 的成功 marker，必须最后恢复；full 还需随后原子切 pointer。
        operations.write_json(target.manifest_path, new_manifest)
        if full and target.build_id is not None:
            operations.publish(base, target.build_id)
    except Exception:
        if not full:
            target.manifest_path.unlink(missing_ok=True)
        raise
    logger.info("[code_vec] %s: %s, 变更 %d / 删除 %d / 总 %d 节点 → %s", project_id,
                "full" if full else "incremental", len(changed), len(deleted),
                len(new_manifest), code_vec_collection_name(project_id))
    return len(changed)
