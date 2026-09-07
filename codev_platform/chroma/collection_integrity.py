"""Chroma collection 的通用分页 ID 集完整性证明。"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Protocol, TypeVar


_TRANSIENT_COMPACTION_ERRORS = frozenset({
    "Error in compaction: Failed to pull logs from the log store",
    "Error in compaction: Error purging logs",
})
_DELETE_RETRY_DELAYS = (0.5, 1.0)
_ResultT = TypeVar("_ResultT")


class _CollectionReader(Protocol):
    """完整性证明只依赖的最小 collection 读契约。"""

    def count(self) -> int: ...

    def get(self, *, limit: int, offset: int, include: list[str]) -> Mapping[str, object]: ...


class _CollectionDeleter(Protocol):
    """删除旧 chunk 时依赖的最小 collection 写契约。"""

    def delete(self, *, ids: list[str]) -> None: ...


class _CollectionUpserter(Protocol):
    def upsert(self, **kwargs: object) -> None: ...


class CollectionIntegrityError(RuntimeError):
    """collection 与期望 ID 集不一致。"""


class CollectionProbeUnavailableError(CollectionIntegrityError):
    """存储暂时无法完成完整性探针。"""


def iter_manifest_chunk_ids(manifest: object) -> Iterable[str]:
    """严格展开文档 manifest 的 rel/chunk_count，不接受动态或负数计数。"""
    from codev_platform.chroma.document_manifest import valid_document_manifest

    if not valid_document_manifest(manifest):
        raise CollectionIntegrityError("文档 manifest 结构无效")
    for rel, entry in manifest["files"].items():
        count = entry.get("chunk_count")
        for index in range(count):
            yield f"{rel}#{index}"


def _is_transient_compaction_error(exc: Exception) -> bool:
    """仅识别已在生产复现的 Chroma 写入瞬态，不扩大失败豁免范围。"""
    error_type = type(exc)
    # Chroma 是可选的重型运行依赖；以其精确限定名识别，避免完整性层为此顶层导入。
    return (
        error_type.__module__ == "chromadb.errors"
        and error_type.__qualname__ == "InvalidArgumentError"
        and str(exc) in _TRANSIENT_COMPACTION_ERRORS
    )


def run_with_transient_compaction_retry(
    operation: Callable[[], _ResultT],
    *,
    sleeper: Callable[[float], None] | None = None,
) -> _ResultT:
    """对精确识别的 Chroma compaction 写失败执行小次数有界重试。"""
    wait = time.sleep if sleeper is None else sleeper
    for delay in (*_DELETE_RETRY_DELAYS, None):
        try:
            return operation()
        except Exception as exc:  # noqa: BLE001 - 调用方保留原失败关闭语义
            if delay is None or not _is_transient_compaction_error(exc):
                raise
            wait(delay)
    raise AssertionError("unreachable compaction retry state")


def delete_ids_with_transient_compaction_retry(
    collection: _CollectionDeleter,
    ids: list[str],
    *,
    sleeper: Callable[[float], None] | None = None,
) -> None:
    """对唯一可判定的 Chroma compaction 瞬态错误执行有界重试。"""
    run_with_transient_compaction_retry(
        lambda: collection.delete(ids=ids),
        sleeper=sleeper,
    )


def upsert_with_transient_compaction_retry(
    collection: _CollectionUpserter,
    ids: list[str],
    embeddings: object,
    documents: list[str],
    metadatas: list[dict],
) -> None:
    run_with_transient_compaction_retry(
        lambda: collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )
    )


def delete_collection_ids(
    collection: _CollectionDeleter, ids: list[str], manifest_path: Path,
) -> None:
    """删除异常即撤销 manifest 并失败；已知短暂 compaction 错误有限自愈。"""
    try:
        delete_ids_with_transient_compaction_retry(collection, ids)
    except Exception as exc:  # noqa: BLE001 - 存储写失败必须失败关闭
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError("Chroma 删除旧 chunk 失败") from exc


def open_collection_for_proof(
    client_factory, directory: Path, manifest_path: Path, collection_name: str,
):
    """打开当前 collection；入口损坏也撤销 manifest 以触发下次全量自愈。"""
    try:
        return client_factory(path=str(directory)).get_collection(collection_name)
    except Exception as exc:  # noqa: BLE001 - 存储入口异常必须失败关闭
        manifest_path.unlink(missing_ok=True)
        raise RuntimeError("Chroma 完整性探针打开失败") from exc


def _expected_id_set(expected_ids: Iterable[str]) -> set[str]:
    remaining: set[str] = set()
    for item in expected_ids:
        if type(item) is not str:
            raise CollectionIntegrityError("manifest ID 类型无效")
        if item in remaining:
            raise CollectionIntegrityError("manifest 存在重复 ID")
        remaining.add(item)
    return remaining


def _page_ids(collection: _CollectionReader, *, limit: int, offset: int) -> list[str]:
    result = collection.get(limit=limit, offset=offset, include=[])
    ids = result.get("ids") if isinstance(result, Mapping) else None
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise CollectionIntegrityError("collection 分页结果缺少合法 ids")
    if len(ids) > limit:
        raise CollectionIntegrityError("collection 分页结果超过请求上限")
    if len(ids) != len(set(ids)):
        raise CollectionIntegrityError("collection 分页结果存在重复 ID")
    return ids


def _verify_pages(
    collection: _CollectionReader,
    remaining: set[str],
    actual_count: int,
    *,
    label: str,
    page_size: int,
) -> None:
    offset = 0
    while offset < actual_count:
        page = _page_ids(collection, limit=min(page_size, actual_count - offset), offset=offset)
        if not page:
            raise CollectionIntegrityError(f"{label} 完整性失败: ID 分页提前结束")
        unexpected = next((item for item in page if item not in remaining), None)
        if unexpected is not None:
            raise CollectionIntegrityError(f"{label} 完整性失败: 存在意外或跨页重复 ID")
        remaining.difference_update(page)
        offset += len(page)
    if remaining:
        raise CollectionIntegrityError(f"{label} 完整性失败: 缺少 manifest ID")


def _invalidate(invalidate: Callable[[], None] | None, *, label: str, failure: Exception) -> None:
    if invalidate is None:
        return
    try:
        invalidate()
    except Exception:  # noqa: BLE001 - 撤销失败不能掩盖构建失败
        raise CollectionIntegrityError(f"{label} 完整性失败，且 manifest 撤销失败") from failure


def verify_collection_ids(
    collection: _CollectionReader,
    expected_ids: Iterable[str],
    *,
    label: str,
    invalidate: Callable[[], None] | None = None,
    page_size: int = 2_000,
) -> int:
    """分页证明 collection ID 集与期望完全相等；失败时可撤销发布状态。"""
    if type(page_size) is not int or page_size < 1:
        raise ValueError("page_size 必须是正整数")
    try:
        remaining = _expected_id_set(expected_ids)
        actual_count = collection.count()
        if type(actual_count) is not int or actual_count < 0:
            raise CollectionIntegrityError(f"{label} 完整性失败: collection count 无效")
        if actual_count != len(remaining):
            raise CollectionIntegrityError(
                f"{label} 完整性失败: collection={actual_count}, manifest IDs={len(remaining)}"
            )
        _verify_pages(collection, remaining, actual_count, label=label, page_size=page_size)
    except Exception as exc:  # noqa: BLE001 - 存储与 manifest 异常统一失败关闭
        _invalidate(invalidate, label=label, failure=exc)
        if isinstance(exc, CollectionIntegrityError):
            raise
        raise CollectionProbeUnavailableError(f"{label} 完整性探针失败") from exc
    return actual_count


__all__ = [
    "CollectionIntegrityError",
    "CollectionProbeUnavailableError",
    "delete_ids_with_transient_compaction_retry",
    "delete_collection_ids",
    "iter_manifest_chunk_ids",
    "open_collection_for_proof",
    "run_with_transient_compaction_retry",
    "upsert_with_transient_compaction_retry",
    "verify_collection_ids",
]
