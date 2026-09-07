"""代码向量索引删除与存储后验失败关闭测试。"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

import codev_platform.chroma.collection_integrity as collection_integrity
from codev_platform.recall import code_vector_store
from codev_platform.chroma.collection_integrity import (
    CollectionIntegrityError,
    CollectionProbeUnavailableError,
    verify_collection_ids,
)
from codev_platform.recall.code_vector_checkpoint import write_checkpoint_meta
from codev_platform.recall.code_vector_checkpoint import checkpoint_fingerprint_digest


def _checkpoint_fingerprint() -> dict[str, object]:
    return {
        "schema_version": 1,
        "target_commit": "a" * 40,
        "runtime_revision": "b" * 40,
        "config_digest": "c" * 64,
        "embed_backend": "remote",
        "embed_model_stamp": "d" * 64,
        "embed_device": "cpu",
        "embed_max_seq_length": 512,
        "chunk_policy": "e" * 64,
        "skip_kinds": ["file", "import", "variable"],
        "enrich": False,
    }


class _Collection:
    def __init__(
        self,
        ids: list[str] | None = None,
        *,
        count_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self._ids = ids or []
        self._count_error = count_error
        self._delete_error = delete_error
        self.deleted: list[list[str]] = []

    def delete(self, *, ids) -> None:
        self.deleted.append(list(ids))
        if self._delete_error is not None:
            raise self._delete_error

    def count(self) -> int:
        if self._count_error is not None:
            raise self._count_error
        return len(self._ids)

    def get(self, *, limit: int, offset: int, include: list[str]):
        return {"ids": self._ids[offset : offset + limit]}


class _FlakyProbeCollection(_Collection):
    def __init__(self, ids: list[str], failures: int) -> None:
        super().__init__(ids)
        self._failures = failures
        self.count_calls = 0

    def count(self) -> int:
        self.count_calls += 1
        if self.count_calls <= self._failures:
            raise OSError("Error in compaction: segments unavailable")
        return super().count()


_ChromaInvalidArgumentError = type(
    "InvalidArgumentError",
    (Exception,),
    {"__module__": "chromadb.errors"},
)


class _DeleteSequenceCollection:
    def __init__(self, failures: list[Exception]) -> None:
        self._failures = failures
        self.deleted: list[list[str]] = []

    def delete(self, *, ids) -> None:
        self.deleted.append(list(ids))
        if self._failures:
            raise self._failures.pop(0)


def _manifest(tmp_path):
    path = tmp_path / ".manifest.json"
    path.write_text('{"old":"hash"}', encoding="utf-8")
    return path


def _proof(collection, expected):
    return lambda: verify_collection_ids(collection, expected, label="code_vec")


def test_兼容门面保留旧关键字常量与精确签名() -> None:
    """模块拆分不能让历史关键字调用或依赖注入签名静默漂移。"""
    import inspect

    assert code_vector_store._CODE_VEC_SUBDIR == "code_vec"
    assert code_vector_store._parse_query_result(res={"ids": [[]]}) == ([], {})
    assert list(inspect.signature(code_vector_store._upsert_with_checkpoints).parameters) == [
        "target",
        "embedder",
        "changed",
        "persisted",
        "manifest_by_id",
        "text_by_id",
        "meta_by_id",
    ]


def test_兼容门面把最终发布协作者注入构建编排(monkeypatch) -> None:
    """失败注入必须替换真实执行路径，不能只替换门面上的闲置别名。"""

    def writer(*_args, **_kwargs) -> None:
        return None

    def checkpoint_writer(*_args, **_kwargs) -> None:
        return None

    def publisher(*_args, **_kwargs) -> None:
        return None

    def differ(_old, _new) -> tuple[list[str], list[str]]:
        return [], []

    monkeypatch.setattr(code_vector_store, "_write_json_atomic", writer)
    monkeypatch.setattr(code_vector_store, "write_checkpoint_meta", checkpoint_writer)
    monkeypatch.setattr(code_vector_store, "commit_build", publisher)
    monkeypatch.setattr(code_vector_store, "_diff_manifest", differ)

    hooks = code_vector_store._build_hooks()

    assert hooks.write_json is writer
    assert hooks.write_checkpoint is checkpoint_writer
    assert hooks.publish is publisher
    assert hooks.diff_manifest is differ


def test_兼容门面的manifest校验替换点进入真实采集路径(monkeypatch) -> None:
    monkeypatch.setattr(
        code_vector_store,
        "_collect_node_chunks",
        lambda *_args, **_kwargs: ({"node": "a" * 40}, {"node": "text"}, {"node": {}}),
    )
    monkeypatch.setattr(code_vector_store, "_valid_manifest", lambda _value: False)

    with pytest.raises(RuntimeError, match="manifest"):
        code_vector_store._collect_verified_node_chunks(
            [],
            frozenset(),
            None,
            project_id="demo",
        )


def test_原子json写入在替换前刷新文件(monkeypatch, tmp_path) -> None:
    from codev_platform.recall import code_vector_io

    synced: list[int] = []
    monkeypatch.setattr(code_vector_io.os, "fsync", synced.append)
    target = tmp_path / "manifest.json"

    code_vector_io.write_json_atomic(target, {"node": "a" * 40})

    assert json.loads(target.read_text(encoding="utf-8")) == {"node": "a" * 40}
    assert synced


def test_stale_delete_failure_invalidates_manifest_and_fails(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    collection = _Collection(delete_error=OSError("delete failed"))

    with pytest.raises(RuntimeError, match="删除"):
        code_vector_store._delete_stale_nodes(collection, ["old"], manifest)

    assert collection.deleted == [["old"]]
    assert not manifest.exists()


def test_code_vec精确compaction删除会复用有界重试(monkeypatch, tmp_path) -> None:
    manifest = _manifest(tmp_path)
    collection = _DeleteSequenceCollection([
        _ChromaInvalidArgumentError(
            "Error in compaction: Failed to pull logs from the log store",
        ),
    ])
    waits: list[float] = []
    monkeypatch.setattr(collection_integrity.time, "sleep", waits.append)

    code_vector_store._delete_stale_nodes(collection, ["old"], manifest)

    assert collection.deleted == [["old"], ["old"]]
    assert waits == [0.5]
    assert manifest.exists()


def test_collection_postcheck_failure_invalidates_manifest(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    expected = {"new-1": "hash", "new-2": "hash"}

    with pytest.raises(RuntimeError, match="完整性|探针"):
        code_vector_store._run_collection_proof_with_retry(
            _proof(_Collection(["new-1"]), expected),
            manifest,
        )

    assert not manifest.exists()


def test_collection_postcheck_success_preserves_manifest(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    expected = {"new-1": "hash", "new-2": "hash"}

    assert (
        code_vector_store._run_collection_proof_with_retry(
            _proof(_Collection(list(expected)), expected),
            manifest,
        )
        == 2
    )
    assert manifest.exists()


def test_collection_same_count_with_stale_id_invalidates_manifest(tmp_path) -> None:
    manifest = _manifest(tmp_path)
    expected = {"new-1": "hash", "new-2": "hash"}

    with pytest.raises(RuntimeError, match="ID|完整性"):
        code_vector_store._run_collection_proof_with_retry(
            _proof(_Collection(["new-1", "stale"]), expected),
            manifest,
        )

    assert not manifest.exists()


def test_collection_probe_transient_failure_retries_without_revoking_manifest(
    monkeypatch,
    tmp_path,
) -> None:
    manifest = _manifest(tmp_path)
    expected = {"new-1": "hash", "new-2": "hash"}
    collection = _FlakyProbeCollection(list(expected), failures=2)
    delays: list[float] = []
    monkeypatch.setattr(code_vector_store.time, "sleep", delays.append)

    assert (
        code_vector_store._run_collection_proof_with_retry(
            _proof(collection, expected),
            manifest,
        )
        == 2
    )
    assert collection.count_calls == 3
    assert delays == [2.0, 4.0]
    assert manifest.exists()


def test_collection_probe_retry_runs_a_fresh_isolated_attempt(monkeypatch, tmp_path) -> None:
    manifest = _manifest(tmp_path)
    attempts: list[int] = []
    delays: list[float] = []
    monkeypatch.setattr(code_vector_store.time, "sleep", delays.append)

    def probe() -> int:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise CollectionProbeUnavailableError("暂态")
        return 2

    assert code_vector_store._run_collection_proof_with_retry(probe, manifest) == 2
    assert attempts == [1, 2]
    assert delays == [2.0]
    assert manifest.exists()


def test_collection_probe_permanent_failure_has_bounded_retries(
    monkeypatch,
    tmp_path,
) -> None:
    manifest = _manifest(tmp_path)
    collection = _FlakyProbeCollection(["new-1"], failures=10)
    delays: list[float] = []
    monkeypatch.setattr(code_vector_store.time, "sleep", delays.append)

    with pytest.raises(RuntimeError, match="完整性探针失败"):
        code_vector_store._run_collection_proof_with_retry(
            _proof(collection, {"new-1": "hash"}),
            manifest,
        )

    assert collection.count_calls == 4
    assert delays == [2.0, 4.0, 6.0]
    assert not manifest.exists()


def test_collection_structural_mismatch_does_not_retry(monkeypatch, tmp_path) -> None:
    manifest = _manifest(tmp_path)
    collection = _Collection(["new-1"])
    delays: list[float] = []
    monkeypatch.setattr(code_vector_store.time, "sleep", delays.append)

    with pytest.raises(RuntimeError, match="完整性失败"):
        code_vector_store._run_collection_proof_with_retry(
            _proof(collection, {"new-1": "hash", "new-2": "hash"}),
            manifest,
        )

    assert delays == []
    assert not manifest.exists()


def test_node_collection_failure_invalidates_manifest(monkeypatch, tmp_path) -> None:
    manifest = _manifest(tmp_path)
    monkeypatch.setattr(
        code_vector_store,
        "_collect_node_chunks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("仓图谱不可用")),
    )

    with pytest.raises(RuntimeError, match="图谱"):
        code_vector_store._collect_verified_node_chunks(
            [],
            frozenset(),
            manifest,
            project_id="demo",
        )

    assert not manifest.exists()


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"node":1}',
        '{"":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
        '{"node":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}',
    ],
)
def test_invalid_incremental_manifest_is_revoked_for_full_rebuild(tmp_path, payload) -> None:
    manifest = tmp_path / ".manifest.json"
    manifest.write_text(payload, encoding="utf-8")

    loaded, valid = code_vector_store._load_incremental_manifest(manifest)

    assert loaded == {}
    assert valid is False
    assert not manifest.exists()


def test_valid_incremental_manifest_is_preserved(tmp_path) -> None:
    manifest = tmp_path / ".manifest.json"
    manifest.write_text('{"node":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', encoding="utf-8")

    loaded, valid = code_vector_store._load_incremental_manifest(manifest)

    assert loaded == {"node": "a" * 40}
    assert valid is True
    assert manifest.exists()


def test_oversized_code_vec_manifest_is_rejected_before_json_load(tmp_path) -> None:
    from codev_platform.recall.code_vector_manifest import MAX_MANIFEST_BYTES

    manifest = tmp_path / ".manifest.json"
    with manifest.open("wb") as stream:
        stream.truncate(MAX_MANIFEST_BYTES + 1)

    loaded, valid = code_vector_store._load_incremental_manifest(manifest)

    assert loaded == {}
    assert valid is False
    assert not manifest.exists()


def test_code_vec_manifest_rejects_entry_count_and_long_ids(monkeypatch, tmp_path) -> None:
    from codev_platform.recall import code_vector_manifest

    monkeypatch.setattr(code_vector_manifest, "MAX_MANIFEST_ENTRIES", 1)
    manifests = [
        {"a": "a" * 40, "b": "b" * 40},
        {"x" * (code_vector_manifest.MAX_NODE_ID_CHARS + 1): "a" * 40},
    ]
    for value in manifests:
        manifest = tmp_path / ".manifest.json"
        manifest.write_text(json.dumps(value), encoding="utf-8")

        loaded, valid = code_vector_store._load_incremental_manifest(manifest)

        assert loaded == {}
        assert valid is False
        assert not manifest.exists()


def test_new_code_vec_manifest_over_limit_is_not_published(monkeypatch, tmp_path) -> None:
    from codev_platform.recall import code_vector_manifest

    manifest = _manifest(tmp_path)
    monkeypatch.setattr(code_vector_manifest, "MAX_MANIFEST_ENTRIES", 1)
    monkeypatch.setattr(
        code_vector_store,
        "_collect_node_chunks",
        lambda *_args, **_kwargs: (
            {"a": "a" * 40, "b": "b" * 40},
            {"a": "text", "b": "text"},
            {"a": {}, "b": {}},
        ),
    )

    with pytest.raises(RuntimeError, match="manifest"):
        code_vector_store._collect_verified_node_chunks(
            [],
            frozenset(),
            manifest,
            project_id="demo",
        )

    assert not manifest.exists()


def test_new_code_vec_manifest_serialized_bytes_are_bounded(monkeypatch, tmp_path) -> None:
    from codev_platform.recall import code_vector_manifest

    manifest = _manifest(tmp_path)
    monkeypatch.setattr(code_vector_manifest, "MAX_MANIFEST_BYTES", 32)
    monkeypatch.setattr(
        code_vector_store,
        "_collect_node_chunks",
        lambda *_args, **_kwargs: (
            {"节点": "a" * 40},
            {"节点": "text"},
            {"节点": {}},
        ),
    )

    with pytest.raises(RuntimeError, match="manifest"):
        code_vector_store._collect_verified_node_chunks(
            [],
            frozenset(),
            manifest,
            project_id="demo",
        )

    assert not manifest.exists()


def test_incremental_delete_failure_falls_back_to_full_handoff(monkeypatch, tmp_path) -> None:
    """增量删除旧节点失败时转 full side-build，避免旧库损坏把队列永久卡死。"""
    import json
    import sys
    from pathlib import Path

    base = tmp_path / "code_vec" / "demo"
    base.mkdir(parents=True)
    (base / ".manifest.json").write_text(json.dumps({"old": "a" * 40}), encoding="utf-8")
    fingerprint = _checkpoint_fingerprint()
    write_checkpoint_meta(
        base / ".manifest.meta.json",
        fingerprint,
        embedding_dimension=1,
        checkpoint_entries=1,
    )
    resumable = base / "builds" / "resumable"
    resumable.mkdir(parents=True)
    (resumable / ".manifest.json").write_text(
        json.dumps({"old": "a" * 40}),
        encoding="utf-8",
    )
    write_checkpoint_meta(
        resumable / ".manifest.meta.json",
        fingerprint,
        embedding_dimension=1,
        checkpoint_entries=1,
    )

    class _Embedder:
        def encode_batch(self, texts):
            return [[0.1] for _ in texts]

    class _BrokenIncrementalCollection:
        def delete(self, ids):
            raise RuntimeError(f"delete failed: {ids}")

    class _FullCollection:
        def __init__(self):
            self.upserted: list[list[str]] = []
            self.deleted: list[list[str]] = []

        def delete(self, ids):
            self.deleted.append(list(ids))

        def upsert(self, *, ids, embeddings, documents, metadatas):
            self.upserted.append(list(ids))

    full_collection = _FullCollection()
    opened_paths: list[str] = []

    class _Client:
        def __init__(self, path: str):
            opened_paths.append(path)
            self.path = path

        def get_or_create_collection(self, *, name, metadata, configuration):
            if self.path == str(base):
                return _BrokenIncrementalCollection()
            return full_collection

        def close(self) -> None:
            pass

    class _Chroma:
        PersistentClient = _Client

    monkeypatch.setitem(sys.modules, "chromadb", _Chroma())
    monkeypatch.setattr(
        "codev_platform.agent.embed.registry.build_code_vec_embedder", lambda _cfg: _Embedder()
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda _project_id: [])
    monkeypatch.setattr("codev_platform.chroma.ensure_wal", lambda _path: None)
    monkeypatch.setattr(code_vector_store, "_existing_chroma_healthy", lambda _path: True)
    monkeypatch.setattr(
        code_vector_store,
        "build_checkpoint_fingerprint",
        lambda *_args, **_kwargs: fingerprint,
    )
    monkeypatch.setattr(
        code_vector_store, "_verify_collection_ids", lambda _col, manifest, _path: len(manifest)
    )
    monkeypatch.setattr(
        code_vector_store,
        "_collect_verified_node_chunks",
        lambda *_args, **_kwargs: (
            {"new": "b" * 40},
            {"new": "新的节点文本"},
            {"new": {"kind": "function"}},
        ),
    )

    assert code_vector_store._build_locked("demo", base, incremental=True) == 1

    assert opened_paths[0] == str(base)
    assert any("builds" in Path(path).parts for path in opened_paths[1:])
    assert full_collection.deleted == [["old"]]
    assert full_collection.upserted == [["new"]]
    assert (base / "current.json").exists()


def test_resumed_integrity_failure_discards_unpublished_target(tmp_path) -> None:
    """未发布续跑库完整性失败时可安全丢弃，并在同一 job 从干净目标继续。"""
    from codev_platform.recall import code_vector_build

    base = tmp_path / "code_vec" / "demo"
    resumed = code_vector_store._CodeVecBuildTarget(
        build_dir=base / "builds" / "resumed",
        manifest_path=base / "builds" / "resumed" / ".manifest.json",
        meta_path=base / "builds" / "resumed" / ".manifest.meta.json",
        collection=object(),
        build_id="resumed",
        resumed=True,
        fingerprint=_checkpoint_fingerprint(),
        embedding_dimension=1,
    )
    clean = code_vector_store._CodeVecBuildTarget(
        build_dir=base / "builds" / "clean",
        manifest_path=base / "builds" / "clean" / ".manifest.json",
        meta_path=base / "builds" / "clean" / ".manifest.meta.json",
        collection=object(),
        build_id="clean",
        resumed=False,
        fingerprint=_checkpoint_fingerprint(),
        embedding_dimension=None,
    )
    discarded: list[object] = []
    hooks = SimpleNamespace(
        resumable_manifest=lambda _target: (_ for _ in ()).throw(
            CollectionIntegrityError("ID 不一致")
        ),
        discard_target=lambda target, _base, _pid: discarded.append(target) or clean,
    )

    target, manifest = code_vector_build._open_clean_target_after_bad_resume(
        resumed, base, "demo", hooks,
    )

    assert target is clean
    assert manifest == {}
    assert discarded == [resumed]


def test_incremental_fallback_discards_broken_resumed_target(tmp_path) -> None:
    from codev_platform.recall import code_vector_build

    base = tmp_path / "code_vec" / "demo"
    resumed = code_vector_store._CodeVecBuildTarget(
        base / "builds" / "resumed",
        base / "builds" / "resumed" / ".manifest.json",
        base / "builds" / "resumed" / ".manifest.meta.json",
        object(),
        "resumed",
        True,
        _checkpoint_fingerprint(),
        1,
    )
    clean = code_vector_store._CodeVecBuildTarget(
        base / "builds" / "clean",
        base / "builds" / "clean" / ".manifest.json",
        base / "builds" / "clean" / ".manifest.meta.json",
        object(), "clean", False, _checkpoint_fingerprint(), None,
    )
    hooks = SimpleNamespace(
        open_target=lambda *_args, **_kwargs: resumed,
        resumable_manifest=lambda _target: (_ for _ in ()).throw(
            CollectionIntegrityError("ID 不一致")
        ),
        discard_target=lambda *_args, **_kwargs: clean,
        diff_manifest=lambda old, new: (
            [node_id for node_id in new if old.get(node_id) != new[node_id]],
            [node_id for node_id in old if node_id not in new],
        ),
        delete_stale=lambda *_args, **_kwargs: None,
    )

    target, manifest, changed, deleted = code_vector_build._recover_incremental_delete_failure(
        clean, base, "demo", _checkpoint_fingerprint(), {"new": "b" * 40}, hooks,
    )

    assert target is clean
    assert manifest == {}
    assert changed == ["new"]
    assert deleted == []


@pytest.mark.parametrize("failure_stage", ["manifest", "meta", "publish"])
def test_全量构建最终发布失败时不切换current(
    monkeypatch,
    tmp_path,
    failure_stage: str,
) -> None:
    """最终元数据和指针属于同一发布边界，任一步失败都不能产生成功 current。"""
    from codev_platform.recall import code_vector_build

    base = tmp_path / "code_vec" / "demo"
    old_build = base / "builds" / "build-old"
    old_build.mkdir(parents=True)
    (base / "current.json").write_text(
        json.dumps({"build": "build-old"}),
        encoding="utf-8",
    )
    build_dir = base / "builds" / "build-new"
    build_dir.mkdir(parents=True)
    fingerprint = _checkpoint_fingerprint()
    target = code_vector_build._CodeVecBuildTarget(
        build_dir=build_dir,
        manifest_path=build_dir / ".manifest.json",
        meta_path=build_dir / ".manifest.meta.json",
        collection=object(),
        build_id="build-new",
        resumed=False,
        fingerprint=fingerprint,
        embedding_dimension=None,
    )
    published: list[str] = []

    def write_json(path, value) -> None:
        if failure_stage == "manifest":
            raise OSError("manifest 写入失败")
        path.write_text(json.dumps(value), encoding="utf-8")

    def write_checkpoint(path, value, **_kwargs) -> None:
        if failure_stage == "meta":
            raise OSError("meta 写入失败")
        path.write_text(json.dumps(value), encoding="utf-8")

    def publish(_base, build_id: str) -> None:
        published.append(build_id)
        if failure_stage == "publish":
            raise OSError("current 切换失败")

    hooks = code_vector_build.BuildHooks(
        checkpoint_fingerprint=lambda *_args, **_kwargs: fingerprint,
        existing_chroma_healthy=lambda _path: True,
        load_manifest=lambda _path: ({}, False),
        read_enrich_mode=lambda _path: None,
        open_target=lambda *_args, **_kwargs: target,
        resumable_manifest=lambda _target: {},
        discard_target=lambda *_args, **_kwargs: target,
        collect_chunks=lambda *_args, **_kwargs: (
            {"node": "a" * 40},
            {"node": "text"},
            {"node": {}},
        ),
        delete_stale=lambda *_args, **_kwargs: None,
        upsert=lambda *_args, **_kwargs: ({"node": "a" * 40}, 1),
        verify=lambda *_args, **_kwargs: 1,
        diff_manifest=lambda _old, new: (list(new), []),
        write_json=write_json,
        write_checkpoint=write_checkpoint,
        publish=publish,
    )
    monkeypatch.setattr(
        "codev_platform.agent.embed.registry.build_code_vec_embedder",
        lambda _cfg: object(),
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda _pid: [])

    with pytest.raises(OSError, match="失败"):
        code_vector_build._build_locked("demo", base, incremental=False, hooks=hooks)

    expected_publish = ["build-new"] if failure_stage == "publish" else []
    assert published == expected_publish
    assert json.loads((base / "current.json").read_text(encoding="utf-8")) == {
        "build": "build-old",
    }


def test_full_build_reuses_latest_healthy_side_checkpoint(monkeypatch, tmp_path) -> None:
    """全量构建重试必须复用未发布 side-build 的已提交进度。"""
    base = tmp_path / "code_vec" / "demo"
    old = base / "builds" / "old"
    resumable = base / "builds" / "resumable"
    for directory, node_id in ((old, "old"), (resumable, "done")):
        directory.mkdir(parents=True)
        (directory / ".manifest.json").write_text(
            json.dumps({node_id: "a" * 40}),
            encoding="utf-8",
        )
    (base / "current.json").write_text(json.dumps({"build": "old"}), encoding="utf-8")
    write_checkpoint_meta(
        resumable / ".manifest.meta.json",
        _checkpoint_fingerprint(),
        embedding_dimension=1,
        checkpoint_entries=1,
    )
    monkeypatch.setattr(code_vector_store, "_existing_chroma_healthy", lambda _path: True)

    assert code_vector_store._find_resumable_full_build(base) == resumable


def test_full_build_rejects_checkpoint_from_different_embedding_policy(
    monkeypatch, tmp_path
) -> None:
    """文本 hash 相同也不能混用不同 max_seq/模型策略生成的向量。"""
    base = tmp_path / "code_vec" / "demo"
    resumable = base / "builds" / "resumable"
    resumable.mkdir(parents=True)
    (resumable / ".manifest.json").write_text(
        json.dumps({"done": "a" * 40}),
        encoding="utf-8",
    )
    old_fingerprint = _checkpoint_fingerprint()
    old_fingerprint["embed_max_seq_length"] = 32768
    write_checkpoint_meta(
        resumable / ".manifest.meta.json",
        old_fingerprint,
        embedding_dimension=1024,
        checkpoint_entries=1,
    )
    monkeypatch.setattr(code_vector_store, "_existing_chroma_healthy", lambda _path: True)

    assert (
        code_vector_store._find_resumable_full_build(
            base,
            _checkpoint_fingerprint(),
        )
        is None
    )


def test_checkpoint_progress_uses_latest_build_not_stale_high_water(
    monkeypatch,
    tmp_path,
) -> None:
    """同指纹旧大 checkpoint 不能遮蔽新 side-build 的真实推进。"""
    base = tmp_path / "code_vec" / "demo"
    fingerprint = _checkpoint_fingerprint()
    for order, (name, count) in enumerate((("stale", 128), ("active", 64)), 1):
        directory = base / "builds" / name
        directory.mkdir(parents=True)
        manifest = {f"node-{index}": "a" * 40 for index in range(count)}
        manifest_path = directory / ".manifest.json"
        meta_path = directory / ".manifest.meta.json"
        manifest_path.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        write_checkpoint_meta(
            meta_path,
            fingerprint,
            embedding_dimension=1024,
            checkpoint_entries=count,
        )
        timestamp = order * 1_000_000_000
        os.utime(manifest_path, ns=(timestamp, timestamp))
        os.utime(meta_path, ns=(timestamp, timestamp))
        directory_timestamp = (3 - order) * 1_000_000_000
        os.utime(directory, ns=(directory_timestamp, directory_timestamp))
    monkeypatch.setattr(code_vector_store, "_code_vec_persist_dir", lambda _pid: base)
    monkeypatch.setattr(code_vector_store, "_existing_chroma_healthy", lambda _path: True)

    assert code_vector_store.code_vec_checkpoint_progress("demo") == {
        checkpoint_fingerprint_digest(fingerprint): 64,
    }
    assert code_vector_store._find_resumable_full_build(base, fingerprint).name == "active"


def test_checkpoint_progress_ignores_unhealthy_stale_high_water(monkeypatch, tmp_path) -> None:
    base = tmp_path / "code_vec" / "demo"
    fingerprint = _checkpoint_fingerprint()
    for name, count in (("stale", 128), ("active", 64)):
        directory = base / "builds" / name
        directory.mkdir(parents=True)
        manifest = {f"node-{index}": "a" * 40 for index in range(count)}
        (directory / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        write_checkpoint_meta(
            directory / ".manifest.meta.json", fingerprint,
            embedding_dimension=1024, checkpoint_entries=count,
        )
    monkeypatch.setattr(code_vector_store, "_code_vec_persist_dir", lambda _pid: base)
    monkeypatch.setattr(
        code_vector_store, "_existing_chroma_healthy", lambda path: path.name != "stale"
    )

    assert code_vector_store.code_vec_checkpoint_progress("demo") == {
        checkpoint_fingerprint_digest(fingerprint): 64,
    }
