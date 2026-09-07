"""代码向量索引兼容门面与命令行入口。

查询、文本切块、构建编排和独立进程完整性证明分别位于同目录的专职模块。本门面保留仓内使用
的公共导入路径，并把构建失败注入点显式传入编排层。稳定公共面以 ``__all__`` 为准；下划线
入口只兼容仓内既有测试，不承诺任意模块全局重绑定都会跨模块传播。
"""

from __future__ import annotations

import logging
from pathlib import Path
import time

from codev_platform.chroma import collection_integrity as _integrity
from codev_platform.recall import code_vector_build as _build
from codev_platform.recall import code_vector_manifest as _manifest
from codev_platform.recall import code_vector_paths as _paths
from codev_platform.recall import code_vector_proof as _proof
from codev_platform.recall import code_vector_query as _query
from codev_platform.recall import code_vector_text as _text
from codev_platform.core.index_handoff import commit_build
from codev_platform.recall.code_vector_checkpoint import (
    build_checkpoint_fingerprint,
    write_checkpoint_meta,
)
from codev_platform.recall.code_vector_io import write_json_atomic as _write_json_atomic


logger = logging.getLogger(__name__)

CollectionIntegrityError = _integrity.CollectionIntegrityError
CollectionProbeUnavailableError = _integrity.CollectionProbeUnavailableError
CodeVecLockBusy = _build.CodeVecLockBusy
_CheckpointDimensionMismatch = _build._CheckpointDimensionMismatch
_CodeVecBuildTarget = _build._CodeVecBuildTarget
_CollectionProofResult = _proof._CollectionProofResult

_CODE_VEC_KEEP = _build._CODE_VEC_KEEP
_UPSERT_BATCH = _build._UPSERT_BATCH
_DEFAULT_SKIP_KINDS = _build._DEFAULT_SKIP_KINDS
_PROBE_ATTEMPTS = _proof._PROBE_ATTEMPTS
_PROBE_BACKOFF_SECONDS = _proof._PROBE_BACKOFF_SECONDS
_PROBE_PROCESS_TIMEOUT_SECONDS = _proof._PROBE_PROCESS_TIMEOUT_SECONDS
_QUERY_CLIENTS = _query._QUERY_CLIENTS

_load_incremental_manifest = _manifest.load_incremental_manifest
_valid_manifest = _manifest.valid_code_vector_manifest
_MANIFEST_NAME = _paths.MANIFEST_NAME
_MANIFEST_META_NAME = _paths.MANIFEST_META_NAME
_CODE_VEC_SUBDIR = _paths.CODE_VEC_SUBDIR
_default_code_vec_persist_dir = _paths.code_vec_persist_dir
code_vec_collection_name = _paths.code_vec_collection_name

_BASE_FIELD_MAX = _text._BASE_FIELD_MAX
_BODY_KINDS = _text._BODY_KINDS
_CHUNK_BODY_CHARS = _text._CHUNK_BODY_CHARS
_CHUNK_OVERLAP_LINES = _text._CHUNK_OVERLAP_LINES
_CLASS_HEAD_CHARS = _text._CLASS_HEAD_CHARS
_CONTAINER_KINDS = _text._CONTAINER_KINDS
_MAX_CHUNKS_PER_NODE = _text._MAX_CHUNKS_PER_NODE
_SNIPPET_MAX_CHARS = _text._SNIPPET_MAX_CHARS
_TEXT_FIELDS = _text._TEXT_FIELDS
_diff_manifest = _text._diff_manifest
_embed_text = _text._embed_text
_head_at_line_boundary = _text._head_at_line_boundary
_node_chunks = _text._node_chunks
_node_hash = _text._node_hash
_node_id_of = _text._node_id_of
_node_source_lines = _text._node_source_lines
_source_snippet = _text._source_snippet
_window_lines = _text._window_lines
build_text = _text.build_text


def _code_vec_persist_dir(project_id: str):
    """兼容旧私有入口：返回项目代码向量目录。"""
    return _default_code_vec_persist_dir(project_id)


def _resolve_repo(project_id: str):
    """兼容旧单仓调用：返回登记的第一个仓根。"""
    from codev_platform.core.repos import project_repo_specs

    specs = project_repo_specs(project_id)
    return specs[0].root if specs else None


def _resolve_skip_kinds(cfg: dict) -> frozenset:
    return _build._resolve_skip_kinds(cfg)


def _existing_chroma_healthy(persist) -> bool:
    return _build._existing_chroma_healthy(Path(persist))


def _read_enrich_mode(meta_path) -> bool | None:
    return _build._read_enrich_mode(Path(meta_path))


def _get_query_client(persist_path: str):
    return _query._get_query_client(persist_path, clients=_QUERY_CLIENTS)


def _parse_query_result(res: dict) -> tuple[list[str], dict]:
    return _query._parse_query_result(res)


def query_code_vectors(project_id: str, query: str, k: int) -> tuple[list[str], dict]:
    """通过专职查询模块召回代码节点，并保留门面替换点。"""
    return _query.query_code_vectors(
        project_id,
        query,
        k,
        persist_dir_resolver=_code_vec_persist_dir,
        collection_name_resolver=code_vec_collection_name,
        client_getter=_get_query_client,
        result_parser=_parse_query_result,
        clients=_QUERY_CLIENTS,
    )


def _collect_node_chunks(
    repo_specs,
    skip_kinds: frozenset,
    *,
    project_id: str | None = None,
) -> tuple[dict, dict, dict]:
    return _build._collect_node_chunks(
        repo_specs,
        skip_kinds,
        project_id=project_id,
        chunker=_node_chunks,
    )


def _collect_verified_node_chunks(
    repo_specs,
    skip_kinds: frozenset,
    manifest_path: Path | None,
    *,
    project_id: str,
) -> tuple[dict, dict, dict]:
    return _build._collect_verified_node_chunks(
        repo_specs,
        skip_kinds,
        manifest_path,
        project_id=project_id,
        collector=_collect_node_chunks,
        validator=_valid_manifest,
    )


def _find_resumable_full_build(
    base: Path,
    expected_fingerprint: dict[str, object] | None = None,
) -> Path | None:
    return _build._find_resumable_full_build(
        base,
        expected_fingerprint,
        health_check=_existing_chroma_healthy,
    )


def has_resumable_full_build(project_id: str) -> bool:
    """返回项目是否存在可安全恢复的全量 checkpoint。"""
    return _find_resumable_full_build(_code_vec_persist_dir(project_id)) is not None


def code_vec_checkpoint_progress(project_id: str) -> dict[str, int]:
    """返回项目未发布 checkpoint 的指纹高水位。"""
    return _build.code_vec_checkpoint_progress(
        project_id,
        persist_dir_resolver=_code_vec_persist_dir,
        health_check=_existing_chroma_healthy,
    )


def code_vec_current_manifest_exists(project_id: str) -> bool:
    """当前可读 build 是否仍有成功 manifest；只读，供 runner 判定一次性恢复。"""
    from codev_platform.core.index_handoff import resolve_current

    return (resolve_current(_code_vec_persist_dir(project_id)) / _MANIFEST_NAME).is_file()


def _open_code_vec_build_target(
    base: Path,
    project_id: str,
    *,
    full: bool,
    fingerprint: dict[str, object],
    allow_resume: bool = True,
) -> _CodeVecBuildTarget:
    return _build._open_code_vec_build_target(
        base,
        project_id,
        full=full,
        fingerprint=fingerprint,
        allow_resume=allow_resume,
        finder=_find_resumable_full_build,
    )


def _probe_collection_ids_in_subprocess(
    directory: Path,
    collection_name: str,
    manifest: dict,
) -> _CollectionProofResult:
    return _proof._probe_collection_ids_in_subprocess(
        directory,
        collection_name,
        manifest,
        atomic_writer=_write_json_atomic,
    )


def _run_collection_proof_with_retry(
    probe,
    manifest_path: Path,
) -> int:
    return _proof._run_collection_proof_with_retry(
        probe,
        manifest_path,
        sleeper=time.sleep,
    )


def _verify_collection_ids(
    target: _CodeVecBuildTarget,
    manifest: dict,
    manifest_path: Path,
) -> int:
    return _proof._verify_collection_ids(
        target,
        manifest,
        manifest_path,
        subprocess_probe=_probe_collection_ids_in_subprocess,
        proof_runner=_run_collection_proof_with_retry,
    )


def _resumable_manifest(target: _CodeVecBuildTarget) -> dict:
    return _build._resumable_manifest(target, verifier=_verify_collection_ids)


def _discard_and_open_clean_full_target(
    target: _CodeVecBuildTarget,
    base: Path,
    project_id: str,
) -> _CodeVecBuildTarget:
    return _build._discard_and_open_clean_full_target(
        target,
        base,
        project_id,
        opener=_open_code_vec_build_target,
    )


def _embedding_batch_dimension(embeddings: list[list[float]], expected: int) -> int:
    return _build._embedding_batch_dimension(embeddings, expected)


def _upsert_with_checkpoints(
    target: _CodeVecBuildTarget,
    embedder,
    *,
    changed: list[str],
    persisted: dict,
    manifest_by_id: dict,
    text_by_id: dict,
    meta_by_id: dict,
) -> tuple[dict, int | None]:
    return _build._upsert_with_checkpoints(
        target,
        embedder,
        changed=changed,
        persisted=persisted,
        manifest_by_id=manifest_by_id,
        text_by_id=text_by_id,
        meta_by_id=meta_by_id,
        writer=_write_json_atomic,
        checkpoint_writer=write_checkpoint_meta,
        batch_size=_UPSERT_BATCH,
    )


def _delete_stale_nodes(collection, node_ids: list[str], manifest_path: Path) -> None:
    _build._delete_stale_nodes(collection, node_ids, manifest_path)


def _build_hooks() -> _build.BuildHooks:
    """从门面当前绑定组装构建依赖，兼容既有替换点。"""
    return _build.BuildHooks(
        checkpoint_fingerprint=build_checkpoint_fingerprint,
        existing_chroma_healthy=_existing_chroma_healthy,
        load_manifest=_load_incremental_manifest,
        read_enrich_mode=_read_enrich_mode,
        open_target=_open_code_vec_build_target,
        resumable_manifest=_resumable_manifest,
        discard_target=_discard_and_open_clean_full_target,
        collect_chunks=_collect_verified_node_chunks,
        delete_stale=_delete_stale_nodes,
        upsert=_upsert_with_checkpoints,
        verify=_verify_collection_ids,
        diff_manifest=_diff_manifest,
        write_json=_write_json_atomic,
        write_checkpoint=write_checkpoint_meta,
        publish=commit_build,
    )


def build_code_vector_index(project_id: str, *, incremental: bool = False) -> int:
    """校验项目并在每项目写锁内构建代码向量索引。"""
    from codev_platform.chroma._reindex_lock import release_reindex_lock, try_acquire_reindex_lock
    from codev_platform.core.project_id import validate as validate_project_id

    project_id = validate_project_id(project_id)
    persist = _code_vec_persist_dir(project_id)
    lock = try_acquire_reindex_lock(persist)
    if lock is None:
        raise CodeVecLockBusy(f"另一个 code_vec 重建正在跑, 跳过: {persist}")
    try:
        return _build_locked(project_id, persist, incremental=incremental)
    finally:
        release_reindex_lock(lock)


def _build_locked(project_id: str, persist, *, incremental: bool) -> int:
    return _build._build_locked(
        project_id,
        persist,
        incremental=incremental,
        hooks=_build_hooks(),
    )


def main() -> int:
    """执行代码向量索引命令行入口。"""
    import argparse

    from codev_platform.core.project_id import ProjectIdError, resolve_local

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="构建代码向量索引 (Phase 6 vector lane)")
    parser.add_argument("--project", help="project_id(缺省从 cwd .claude/project.json 解析)")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="增量(只重嵌变更节点; 无 manifest 自动退全量)。缺省=全量重建",
    )
    args = parser.parse_args()

    project_id = args.project
    if not project_id:
        try:
            project_id = resolve_local()
        except ProjectIdError as exc:
            print(f"[code_vec] FATAL: 无法解析 project_id: {exc}", flush=True)
            return 1

    from codev_platform.reindex.maintenance_gate import maintenance_reindex_operation_permit

    with maintenance_reindex_operation_permit() as permitted:
        if permitted is not True:
            print("[code_vec] FATAL: reindex 维护窗口已启用；代码向量索引写入被拒绝", flush=True)
            return 1
        count = build_code_vector_index(project_id, incremental=args.incremental)
    print(f"[code_vec] done: {count} nodes (re)embedded for {project_id}", flush=True)
    return 0


__all__ = [
    "CodeVecLockBusy",
    "build_code_vector_index",
    "build_text",
    "code_vec_checkpoint_progress",
    "code_vec_collection_name",
    "code_vec_current_manifest_exists",
    "has_resumable_full_build",
    "main",
    "query_code_vectors",
]


if __name__ == "__main__":
    raise SystemExit(main())
