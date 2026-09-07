"""Chroma 受管重建的存储后验、发布顺序与成功证明测试。"""
from __future__ import annotations

import json
import os
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def indexer():
    """在 autouse 宿主配置隔离生效后再导入含路径常量的 indexer。"""
    from codev_platform.chroma import indexer as module

    return module


class _Collection:
    def __init__(self, ids: list[str], *, count_error: Exception | None = None) -> None:
        self._ids = ids
        self._count_error = count_error

    def count(self) -> int:
        if self._count_error is not None:
            raise self._count_error
        return len(self._ids)

    def get(self, *, limit: int, offset: int, include: list[str]):
        return {"ids": self._ids[offset:offset + limit]}


class _DeleteFailureCollection:
    def __init__(self) -> None:
        self.calls = 0

    def delete(self, *, ids: list[str]) -> None:
        self.calls += 1
        raise OSError("delete failed")


@pytest.mark.parametrize(
    ("configured", "client_limit", "expected"),
    [(50000, 5461, 5461), (128, 5461, 128)],
)
def test_chroma写入批量服从客户端硬上限(
    indexer, configured: int, client_limit: int, expected: int,
) -> None:
    """业务配置只能收紧批量，不能突破当前 Chroma 后端的硬限制。"""
    client = SimpleNamespace(get_max_batch_size=lambda: client_limit)

    assert indexer.resolve_flush_batch_size(client, configured) == expected


@pytest.mark.parametrize("configured", [True, 0, -1])
def test_chroma拒绝非法业务批量(indexer, configured) -> None:
    client = SimpleNamespace(get_max_batch_size=lambda: 5461)

    with pytest.raises(ValueError, match="正整数"):
        indexer.resolve_flush_batch_size(client, configured)


@pytest.mark.parametrize("client_limit", [True, 0, -1, "5461"])
def test_chroma拒绝非法后端批量(indexer, client_limit) -> None:
    client = SimpleNamespace(get_max_batch_size=lambda: client_limit)

    with pytest.raises(RuntimeError, match="上限无效"):
        indexer.resolve_flush_batch_size(client, 50000)


def test_chroma真实写入循环按后端上限拆批(indexer, monkeypatch, tmp_path) -> None:
    """复现生产事故的 5698 条输入，必须拆为 5461 与 237 两批。"""
    source = tmp_path / "doc.md"
    source.write_text("# doc", encoding="utf-8")
    side = tmp_path / "side"
    upsert_sizes: list[int] = []
    encode_sizes: list[int] = []
    stored_ids: list[str] = []
    published: list[str | None] = []
    client_paths: list[str] = []

    class _Collection:
        def upsert(self, **payload) -> None:
            sizes = {len(payload[key]) for key in ("ids", "documents", "metadatas", "embeddings")}
            assert len(sizes) == 1
            upsert_sizes.append(len(payload["ids"]))
            stored_ids.extend(payload["ids"])

        def count(self) -> int:
            return len(stored_ids)

        def get(self, *, limit: int, offset: int, include: list[str]):
            return {"ids": stored_ids[offset:offset + limit]}

    collection = _Collection()

    class _Client:
        def __init__(self, *, path: str) -> None:
            client_paths.append(path)

        def get_max_batch_size(self) -> int:
            return 5461

        def get_or_create_collection(self, **_kwargs):
            return collection

    class _Embeddings:
        def __init__(self, size: int) -> None:
            self.size = size

        def tolist(self) -> list[list[float]]:
            return [[0.0, 0.0, 0.0] for _ in range(self.size)]

    class _Model:
        prompts = {}
        max_seq_length = 32768

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_embedding_dimension(self) -> int:
            return 3

        def encode(self, documents: list[str], **_kwargs) -> _Embeddings:
            encode_sizes.append(len(documents))
            return _Embeddings(len(documents))

    def _chunks(*_args, **_kwargs):
        for offset in range(5698):
            yield f"doc.md#{offset}", offset, f"chunk-{offset}", {"source": "doc.md"}

    monkeypatch.setenv("PLATFORM_INDEX_FLUSH_BATCH", "50000")
    monkeypatch.setattr(indexer, "PERSIST_DIR", tmp_path)
    monkeypatch.setattr(indexer, "discover_files", lambda: [source])
    monkeypatch.setattr(
        indexer, "_scan_changes",
        lambda *_args, **_kwargs: ([source], [], {"doc.md": "a" * 64}),
    )
    monkeypatch.setattr(indexer, "_rel_path", lambda _path: "doc.md")
    monkeypatch.setattr(indexer, "iter_chunks", _chunks)
    monkeypatch.setattr(indexer, "proven_index_input", lambda: False)
    monkeypatch.setattr(indexer, "gc_builds", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(indexer, "new_build_id", lambda *_args: "build-batched")
    monkeypatch.setattr(indexer, "begin_build", lambda *_args: side)
    monkeypatch.setattr(indexer, "git_head", lambda _root: "a" * 40)
    monkeypatch.setattr(
        indexer, "_publish_build",
        lambda _base, build_id, **_stamp: published.append(build_id),
    )
    monkeypatch.setattr(
        indexer,
        "_verify_persisted_collection",
        lambda *_args: len(stored_ids),
    )
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(PersistentClient=_Client))
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=_Model),
    )
    import codev_platform.chroma as chroma_package
    monkeypatch.setattr(chroma_package, "ensure_wal", lambda _path: None)

    assert indexer.index(force=True) == (1, 5698)
    assert upsert_sizes == [5461, 237]
    assert encode_sizes == [5461, 237]
    assert client_paths == [str(side)]
    assert published == ["build-batched"]


def test_持久化证明先关闭写端再调用独立探针(indexer, monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    events: list[str] = []

    class _Client:
        def close(self) -> None:
            events.append("close")

    def proof(directory: Path, collection_name: str, path: Path) -> int:
        assert directory == tmp_path
        assert collection_name == "docs"
        assert path == manifest_path
        events.append("proof")
        return 7

    monkeypatch.setattr(indexer, "verify_document_collection_in_subprocess", proof)

    assert indexer._verify_persisted_collection(_Client(), tmp_path, "docs", manifest_path) == 7
    assert events == ["close", "proof"]


def test_写端不能关闭时撤销_manifest(indexer, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="缺少关闭接口"):
        indexer._close_writer_before_proof(object(), manifest_path)

    assert not manifest_path.exists()


def test_chroma能力证明失败发生在collection变更前(indexer, monkeypatch, tmp_path) -> None:
    source = tmp_path / "doc.md"
    source.write_text("# doc", encoding="utf-8")
    collection_opened: list[bool] = []

    class _Client:
        def __init__(self, *, path: str) -> None:
            pass

        def get_max_batch_size(self) -> int:
            raise OSError("backend unavailable")

        def get_or_create_collection(self, **_kwargs):
            collection_opened.append(True)
            raise AssertionError("能力证明失败后不得打开 collection")

    class _Model:
        prompts = {}
        max_seq_length = 32768

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_embedding_dimension(self) -> int:
            return 3

    monkeypatch.setattr(indexer, "PERSIST_DIR", tmp_path)
    monkeypatch.setattr(indexer, "discover_files", lambda: [source])
    monkeypatch.setattr(
        indexer, "_scan_changes",
        lambda *_args, **_kwargs: ([source], [], {"doc.md": "a" * 64}),
    )
    monkeypatch.setattr(indexer, "gc_builds", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(indexer, "new_build_id", lambda *_args: "build-failed")
    monkeypatch.setattr(indexer, "begin_build", lambda *_args: tmp_path / "side")
    monkeypatch.setattr(indexer, "git_head", lambda _root: "a" * 40)
    monkeypatch.setattr(
        indexer, "_publish_build",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得发布")),
    )
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(PersistentClient=_Client))
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=_Model),
    )
    import codev_platform.chroma as chroma_package
    monkeypatch.setattr(chroma_package, "ensure_wal", lambda _path: None)

    with pytest.raises(RuntimeError, match="无法取得"):
        indexer.index(force=True)
    assert collection_opened == []


def _doc_manifest(chunk_count) -> dict:
    return {
        "version": 2,
        "params": {
            "manifest_version": 2,
            "embed_model": "model",
            "embed_dim": 3,
            "embed_max_seq_length": 512,
            "chunk_target_max": 100,
            "chunk_hard_max": 200,
        },
        "files": {
            "a.md": {
                "sha256": "a" * 64,
                "chunk_count": chunk_count,
            },
        },
    }


def test_manifest_schema_version_separates_sequence_length_policy() -> None:
    """新旧运行版本必须互相拒绝 manifest，避免回滚后复用不同截断策略的向量。"""
    from codev_platform.chroma.document_manifest import (
        MANIFEST_VERSION,
        valid_document_manifest,
    )

    assert MANIFEST_VERSION == 2
    legacy = _doc_manifest(0)
    legacy["version"] = 1
    legacy["params"]["manifest_version"] = 1
    assert valid_document_manifest(legacy) is False


def test_collection_count_mismatch_or_probe_error_fails(indexer, tmp_path) -> None:
    manifest = _doc_manifest(3)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="完整性|chunk"):
        indexer._verify_collection_ids(_Collection(["a.md#0", "a.md#1"]), manifest, manifest_path)
    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="完整性|探针"):
        indexer._verify_collection_ids(
            _Collection([], count_error=OSError("db busy")), manifest, manifest_path,
        )

    assert not manifest_path.exists()


@pytest.mark.parametrize("value", [True, -1, "512"])
def test_manifest_rejects_invalid_embed_max_seq_length(value) -> None:
    """索引参数证明必须包含普通非负整数，防止错误配置与旧库被误判兼容。"""
    from codev_platform.chroma.document_manifest import valid_document_manifest

    manifest = _doc_manifest(0)
    manifest["params"]["embed_max_seq_length"] = value

    assert valid_document_manifest(manifest) is False


def test_collection_same_count_with_stale_id_fails(indexer, tmp_path) -> None:
    manifest = _doc_manifest(2)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="ID|完整性"):
        indexer._verify_collection_ids(
            _Collection(["a.md#0", "stale#0"]), manifest, manifest_path,
        )

    assert not manifest_path.exists()


@pytest.mark.parametrize(
    ("chunk_count", "actual_ids"),
    [(True, ["a.md#0"]), (-1, []), (1.0, [])],
)
def test_manifest_chunk_count_must_be_non_negative_plain_int(
    indexer, tmp_path, chunk_count, actual_ids,
) -> None:
    manifest = _doc_manifest(chunk_count)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="manifest|完整性|探针"):
        indexer._verify_collection_ids(_Collection(actual_ids), manifest, manifest_path)

    assert not manifest_path.exists()


def test_delete_failure_invalidates_manifest_and_fails(indexer, tmp_path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    collection = _DeleteFailureCollection()

    with pytest.raises(RuntimeError, match="删除"):
        indexer._delete_chunks(
            collection, ["a.md#0"], manifest_path,
        )

    assert collection.calls == 1
    assert not manifest_path.exists()


@pytest.mark.parametrize("failure_at", ["client", "collection"])
def test_open_collection_failure_invalidates_manifest(indexer, tmp_path, failure_at) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    class _Client:
        def get_collection(self, _name):
            raise OSError("collection missing")

    def _client_factory(*, path: str):
        if failure_at == "client":
            raise OSError("db missing")
        return _Client()

    with pytest.raises(RuntimeError, match="打开|探针"):
        indexer._open_collection_for_proof(
            _client_factory, tmp_path, manifest_path,
        )

    assert not manifest_path.exists()


def test_proven_sha_failure_invalidates_manifest_without_path_leak(
    indexer, monkeypatch, tmp_path, caplog,
) -> None:
    sensitive_rel = "external/private-repo/secret.md"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    source = tmp_path / "secret.md"
    source.write_text("secret", encoding="utf-8")
    monkeypatch.setenv("CODEV_REINDEX_PROVEN_INPUTS", "1")
    monkeypatch.setattr(indexer, "_rel_path", lambda _path: sensitive_rel)
    monkeypatch.setattr(indexer, "_file_sha256", lambda _path: (_ for _ in ()).throw(OSError()))

    with pytest.raises(RuntimeError) as captured:
        indexer._scan_changes([source], {"files": {}}, manifest_path=manifest_path)

    assert not manifest_path.exists()
    assert sensitive_rel not in str(captured.value)
    assert sensitive_rel not in caplog.text


@pytest.mark.parametrize("failure_kind", ["read", "decode"])
def test_proven_read_failure_invalidates_manifest_without_path_leak(
    indexer, monkeypatch, tmp_path, caplog, failure_kind,
) -> None:
    sensitive_rel = "external/private-repo/secret.md"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEV_REINDEX_PROVEN_INPUTS", "1")
    monkeypatch.setattr(indexer, "_rel_path", lambda _path: sensitive_rel)

    class _Unreadable:
        def read_text(self, *, encoding: str) -> str:
            if failure_kind == "decode" and encoding == "utf-8":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
            raise OSError()

    with pytest.raises(RuntimeError) as captured:
        list(indexer.iter_chunks([_Unreadable()], manifest_path=manifest_path))

    assert not manifest_path.exists()
    assert sensitive_rel not in str(captured.value)
    assert sensitive_rel not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        '{"files":[]}',
        '{"version":true,"params":{},"files":{}}',
        '{"version":1.0,"params":{},"files":{}}',
        '{"version":1,"params":{},"files":{}}',
        '{"version":1,"params":{"manifest_version":1,"embed_model":"model","embed_dim":[],"chunk_target_max":100,"chunk_hard_max":200},"files":{}}',
        '{"params":{},"files":{}}',
        '{"version":1,"files":{}}',
        '{"files":{"a.md":1}}',
        '{"files":{"a.md":{"sha256":"bad","chunk_count":1}}}',
        '{"files":{"a.md":{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","chunk_count":true}}}',
    ],
)
def test_invalid_chroma_manifest_is_revoked_for_full_rebuild(indexer, tmp_path, payload) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(payload, encoding="utf-8")

    manifest, loaded = indexer._load_manifest(manifest_path)

    assert loaded is False
    assert manifest == indexer._empty_manifest()
    assert not manifest_path.exists()


def test_chroma_manifest_rejects_chunk_resource_amplification(indexer, tmp_path) -> None:
    from codev_platform.chroma.document_manifest import (
        MAX_CHUNKS_PER_FILE,
        MAX_TOTAL_CHUNKS,
    )

    entry = {"sha256": "a" * 64, "chunk_count": MAX_CHUNKS_PER_FILE}
    params = _doc_manifest(0)["params"]
    manifests = [
        {"version": 1, "params": params, "files": {
            "a.md": {**entry, "chunk_count": MAX_CHUNKS_PER_FILE + 1},
        }},
        {"version": 1, "params": params, "files": {
            f"{index}.md": entry
            for index in range(MAX_TOTAL_CHUNKS // MAX_CHUNKS_PER_FILE + 1)
        }},
    ]
    for manifest in manifests:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        _, loaded = indexer._load_manifest(manifest_path)

        assert loaded is False
        assert not manifest_path.exists()


def test_proven_write_rejects_file_changed_after_scan(indexer, monkeypatch, tmp_path) -> None:
    source = tmp_path / "doc.md"
    original = "# title\noriginal-A"
    source.write_text(original, encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEV_REINDEX_PROVEN_INPUTS", "1")
    monkeypatch.setattr(indexer, "_rel_path", lambda _path: "doc.md")

    expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    changed, deleted, sha_map = indexer._scan_changes(
        [source], {"files": {}}, manifest_path=manifest_path,
    )
    source.write_text("# title\nmutated-B", encoding="utf-8")

    assert deleted == []
    assert sha_map["doc.md"] == expected_digest
    with pytest.raises(RuntimeError, match="发生变化"):
        list(indexer.iter_chunks(
            changed, manifest_path=manifest_path, expected_sha=sha_map,
        ))
    assert not manifest_path.exists()


def test_force_empty_project_builds_and_publishes_empty_collection(
    indexer, monkeypatch, tmp_path,
) -> None:
    published: list[tuple[str | None, dict]] = []

    class _EmptyCollection:
        def count(self) -> int:
            return 0

        def get(self, **_kwargs):
            return {"ids": []}

    class _Client:
        def __init__(self, *, path: str) -> None:
            self.path = path

        def get_or_create_collection(self, **_kwargs):
            return _EmptyCollection()

    models = []

    class _Model:
        prompts = {}
        max_seq_length = 32768

        def __init__(self, *_args, **_kwargs) -> None:
            self.max_seq_length = 32768
            models.append(self)

        def get_embedding_dimension(self) -> int:
            return 3

    side = tmp_path / "side"
    monkeypatch.setattr(indexer, "PERSIST_DIR", tmp_path)
    monkeypatch.setattr(indexer, "EMBEDDING_MAX_SEQ_LENGTH", 512)
    monkeypatch.setattr(indexer, "discover_files", lambda: [])
    monkeypatch.setattr(
        indexer,
        "_open_collection_for_proof",
        lambda *_args: (_ for _ in ()).throw(AssertionError("full 不得打开旧 collection")),
    )
    monkeypatch.setattr(indexer, "gc_builds", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(indexer, "new_build_id", lambda *_args: "build-empty")
    monkeypatch.setattr(indexer, "begin_build", lambda *_args: side)
    monkeypatch.setattr(indexer, "git_head", lambda _root: "a" * 40)
    monkeypatch.setattr(
        indexer, "_publish_build",
        lambda _base, build_id, **stamp: published.append((build_id, stamp)),
    )
    monkeypatch.setattr(indexer, "_verify_persisted_collection", lambda *_args: 0)
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(PersistentClient=_Client))
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=_Model),
    )
    import codev_platform.chroma as chroma_package
    monkeypatch.setattr(chroma_package, "ensure_wal", lambda _path: None)

    assert indexer.index(force=True) == (0, 0)
    assert models[0].max_seq_length == 512
    assert published == [("build-empty", {
        "files_count": 0,
        "chunks": 0,
        "dim": 3,
        "model_name": indexer.Path(indexer.EMBEDDING_MODEL).name,
    })]


def test_stale_static_params_empty_project_must_full_rebuild(
    indexer, monkeypatch, tmp_path,
) -> None:
    published: list[tuple[str | None, dict]] = []
    stale_manifest = _doc_manifest(0)
    stale_manifest["files"] = {}
    stale_manifest["params"]["embed_model"] = "stale-model"
    (tmp_path / indexer._MANIFEST_NAME).write_text(
        json.dumps(stale_manifest), encoding="utf-8",
    )

    class _EmptyCollection:
        def count(self) -> int:
            return 0

        def get(self, **_kwargs):
            return {"ids": []}

    class _Client:
        def __init__(self, *, path: str) -> None:
            self.path = path

        def get_or_create_collection(self, **_kwargs):
            return _EmptyCollection()

    class _Model:
        prompts = {}
        max_seq_length = 512

        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def get_embedding_dimension(self) -> int:
            return 3

    side = tmp_path / "side-stale"
    monkeypatch.setattr(indexer, "PERSIST_DIR", tmp_path)
    monkeypatch.setattr(indexer, "discover_files", lambda: [])
    monkeypatch.setattr(
        indexer,
        "_open_collection_for_proof",
        lambda *_args: (_ for _ in ()).throw(AssertionError("过期参数不得走无变更早返")),
    )
    monkeypatch.setattr(indexer, "gc_builds", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(indexer, "new_build_id", lambda *_args: "build-stale")
    monkeypatch.setattr(indexer, "begin_build", lambda *_args: side)
    monkeypatch.setattr(indexer, "git_head", lambda _root: "a" * 40)
    monkeypatch.setattr(
        indexer, "_publish_build",
        lambda _base, build_id, **stamp: published.append((build_id, stamp)),
    )
    monkeypatch.setattr(indexer, "_verify_persisted_collection", lambda *_args: 0)
    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(PersistentClient=_Client))
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=_Model),
    )
    import codev_platform.chroma as chroma_package
    monkeypatch.setattr(chroma_package, "ensure_wal", lambda _path: None)

    assert indexer.index(force=False) == (0, 0)
    assert published == [("build-stale", {
        "files_count": 0,
        "chunks": 0,
        "dim": 3,
        "model_name": indexer.Path(indexer.EMBEDDING_MODEL).name,
    })]


def test_full_publish_commits_current_before_reload_stamp(indexer, monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(indexer, "commit_build", lambda _base, _bid: events.append("commit"))
    monkeypatch.setattr(indexer, "_write_build_stamp", lambda **_kw: events.append("stamp"))

    indexer._publish_build(object(), "build-1", files_count=1, chunks=2, dim=3, model_name="m")

    assert events == ["commit", "stamp"]


def test_reload_stamp_uses_atomic_replace_and_notification_file_is_last(
    indexer,
    monkeypatch,
    tmp_path,
) -> None:
    destinations = []
    real_replace = os.replace

    def _replace(source, destination) -> None:
        destinations.append(destination)
        real_replace(source, destination)

    monkeypatch.setattr(indexer, "PERSIST_DIR", tmp_path)
    monkeypatch.setattr(indexer, "PROJECT_ID", "demo")
    monkeypatch.setattr(indexer.os, "replace", _replace)

    indexer._write_build_stamp(files_count=1, chunks=2, dim=3, model_name="m")

    assert destinations == [
        tmp_path / ".last_build.demo.json",
        tmp_path / ".last_build.json",
    ]
    assert json.loads((tmp_path / ".last_build.json").read_text(encoding="utf-8"))["chunks"] == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_managed_main_emits_one_success_marker_only_after_index(indexer, monkeypatch, capsys) -> None:
    events: list[str] = []
    monkeypatch.setattr(indexer.sys, "argv", ["indexer"])
    monkeypatch.setattr(indexer, "_try_acquire_reindex_lock", lambda: (1, object()))
    monkeypatch.setattr(indexer, "_release_reindex_lock", lambda _lock: events.append("release"))
    monkeypatch.setattr(indexer, "index", lambda force=False: events.append("index") or (1, 2))

    assert indexer.main() == 0

    assert events == ["index", "release"]
    assert capsys.readouterr().out.count("proof: chroma ok") == 1


def test_managed_main_storage_failure_has_no_success_marker(indexer, monkeypatch, capsys) -> None:
    released: list[object] = []
    monkeypatch.setattr(indexer.sys, "argv", ["indexer"])
    monkeypatch.setattr(indexer, "_try_acquire_reindex_lock", lambda: (1, object()))
    monkeypatch.setattr(indexer, "_release_reindex_lock", released.append)
    monkeypatch.setattr(indexer, "index", lambda force=False: (_ for _ in ()).throw(
        RuntimeError("Chroma 完整性失败")
    ))

    with pytest.raises(RuntimeError, match="完整性"):
        indexer.main()

    assert len(released) == 1
    assert "proof: chroma ok" not in capsys.readouterr().out


def test_runtime_revision_mismatch_stops_before_lock_and_index(indexer, monkeypatch, capsys) -> None:
    events: list[str] = []

    def _reject_revision() -> None:
        raise RuntimeError("隔离 reindex 运行版本发生漂移")

    monkeypatch.setattr(indexer.sys, "argv", ["indexer"])
    monkeypatch.setattr(indexer, "verify_reindex_runtime_revision", _reject_revision, raising=False)
    monkeypatch.setattr(
        indexer, "_try_acquire_reindex_lock", lambda: events.append("lock") or (1, object()),
    )
    monkeypatch.setattr(indexer, "index", lambda force=False: events.append("index") or (1, 2))

    with pytest.raises(RuntimeError, match="版本"):
        indexer.main()

    assert events == []
    assert "proof: chroma ok" not in capsys.readouterr().out
