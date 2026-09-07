"""Chroma collection 通用 ID 集完整性校验测试。"""
from __future__ import annotations

import pytest

import codev_platform.chroma.collection_integrity as collection_integrity
from codev_platform.chroma.collection_integrity import (
    CollectionProbeUnavailableError,
    delete_collection_ids,
    verify_collection_ids,
)


class _PagedCollection:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids
        self.offsets: list[int] = []

    def count(self) -> int:
        return len(self._ids)

    def get(self, *, limit: int, offset: int, include: list[str]):
        assert include == []
        self.offsets.append(offset)
        return {"ids": self._ids[offset:offset + limit]}


class _CountOverrideCollection(_PagedCollection):
    def __init__(self, ids: list[str], count_value) -> None:
        super().__init__(ids)
        self._count_value = count_value

    def count(self):
        return self._count_value


class _RaisingCountCollection(_PagedCollection):
    def count(self) -> int:
        raise OSError("compaction unavailable")


class _BrokenPageCollection(_PagedCollection):
    def __init__(self, ids: list[str], page: list[str]) -> None:
        super().__init__(ids)
        self._page = page

    def get(self, *, limit: int, offset: int, include: list[str]):
        self.offsets.append(offset)
        return {"ids": self._page}


_ChromaInvalidArgumentError = type(
    "InvalidArgumentError",
    (Exception,),
    {"__module__": "chromadb.errors"},
)


class _DeleteSequenceCollection:
    def __init__(self, failures: list[Exception]) -> None:
        self._failures = failures
        self.calls: list[list[str]] = []

    def delete(self, *, ids: list[str]) -> None:
        self.calls.append(ids)
        if self._failures:
            raise self._failures.pop(0)


def test_id_proof_reads_all_pages() -> None:
    collection = _PagedCollection([f"id-{index}" for index in range(5)])

    assert verify_collection_ids(
        collection,
        (f"id-{index}" for index in range(5)),
        label="测试库",
        page_size=2,
    ) == 5
    assert collection.offsets == [0, 2, 4]


def test_same_count_with_wrong_id_fails_and_invalidates_manifest() -> None:
    invalidated: list[bool] = []

    with pytest.raises(RuntimeError, match="ID|完整性"):
        verify_collection_ids(
            _PagedCollection(["expected-1", "stale"]),
            ["expected-1", "expected-2"],
            label="测试库",
            invalidate=lambda: invalidated.append(True),
        )

    assert invalidated == [True]


@pytest.mark.parametrize("count_value", [True, 1.0, -1])
def test_count_must_be_non_negative_plain_int(count_value) -> None:
    with pytest.raises(RuntimeError, match="count|完整性"):
        verify_collection_ids(
            _CountOverrideCollection(["id-1"], count_value),
            ["id-1"],
            label="测试库",
        )


@pytest.mark.parametrize("page_size", [True, 1.0, 0, -1])
def test_page_size_must_be_positive_plain_int(page_size) -> None:
    with pytest.raises(ValueError, match="page_size"):
        verify_collection_ids(
            _PagedCollection(["id-1"]),
            ["id-1"],
            label="测试库",
            page_size=page_size,
        )


def test_oversized_page_cannot_fake_success_with_duplicate_id() -> None:
    collection = _BrokenPageCollection(["id-1", "id-2"], ["id-1", "id-2", "id-1"])

    with pytest.raises(RuntimeError, match="分页|完整性"):
        verify_collection_ids(collection, ["id-1", "id-2"], label="测试库")


def test_duplicate_inside_page_is_reported_as_failure() -> None:
    collection = _BrokenPageCollection(
        ["id-1", "id-2", "id-3"],
        ["id-1", "id-1", "id-3"],
    )

    with pytest.raises(RuntimeError, match="重复"):
        verify_collection_ids(collection, ["id-1", "id-2", "id-3"], label="测试库")


def test_integrity_error_does_not_echo_raw_id() -> None:
    sensitive_id = "D:/external/private-repo/src/secret.py#0"

    with pytest.raises(RuntimeError) as captured:
        verify_collection_ids(
            _PagedCollection(["expected", sensitive_id]),
            ["expected", "missing"],
            label="测试库",
        )

    assert sensitive_id not in str(captured.value)


@pytest.mark.parametrize("expected_ids", [["id-1", "id-1"], ["id-1", 2]])
def test_expected_ids_must_be_unique_plain_strings(expected_ids) -> None:
    with pytest.raises(RuntimeError, match="manifest|ID"):
        verify_collection_ids(
            _PagedCollection(["id-1"]),
            expected_ids,
            label="测试库",
        )


def test_storage_probe_exception_has_distinct_type_and_invalidates() -> None:
    invalidated: list[bool] = []
    collection = _RaisingCountCollection([])

    with pytest.raises(CollectionProbeUnavailableError, match="完整性探针失败"):
        verify_collection_ids(
            collection,
            [],
            label="测试库",
            invalidate=lambda: invalidated.append(True),
        )

    assert invalidated == [True]


@pytest.mark.parametrize("message", sorted(collection_integrity._TRANSIENT_COMPACTION_ERRORS))
def test_delete_精确compaction一次失败后成功且保留manifest(
    monkeypatch,
    tmp_path,
    message,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    collection = _DeleteSequenceCollection([
        _ChromaInvalidArgumentError(message),
    ])
    waits: list[float] = []
    monkeypatch.setattr(collection_integrity.time, "sleep", waits.append)

    delete_collection_ids(collection, ["doc.md#0"], manifest_path)

    assert collection.calls == [["doc.md#0"], ["doc.md#0"]]
    assert waits == [0.5]
    assert manifest_path.exists()


def test_delete_精确compaction耗尽后撤销manifest并失败关闭(monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    collection = _DeleteSequenceCollection([
        _ChromaInvalidArgumentError(
            "Error in compaction: Failed to pull logs from the log store",
        )
        for _ in range(3)
    ])
    waits: list[float] = []
    monkeypatch.setattr(collection_integrity.time, "sleep", waits.append)

    with pytest.raises(RuntimeError, match="Chroma 删除旧 chunk") as captured:
        delete_collection_ids(collection, ["doc.md#0"], manifest_path)

    assert collection.calls == [["doc.md#0"], ["doc.md#0"], ["doc.md#0"]]
    assert waits == [0.5, 1.0]
    assert type(captured.value.__cause__) is _ChromaInvalidArgumentError
    assert not manifest_path.exists()


@pytest.mark.parametrize(
    "failure",
    [
        PermissionError("permission denied"),
        _ChromaInvalidArgumentError("other Chroma error"),
        RuntimeError("Error in compaction: Failed to pull logs from the log store"),
    ],
)
def test_delete_非目标错误不重试并撤销manifest(monkeypatch, tmp_path, failure) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    collection = _DeleteSequenceCollection([failure])
    waits: list[float] = []
    monkeypatch.setattr(collection_integrity.time, "sleep", waits.append)

    with pytest.raises(RuntimeError, match="Chroma 删除旧 chunk"):
        delete_collection_ids(collection, ["doc.md#0"], manifest_path)

    assert collection.calls == [["doc.md#0"]]
    assert waits == []
    assert not manifest_path.exists()
