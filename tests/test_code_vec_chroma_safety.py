"""code_vec Chroma compaction 安全策略回归。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import codev_platform.chroma.collection_integrity as collection_integrity
from codev_platform.recall import code_vector_build
from codev_platform.recall.code_vector_checkpoint import (
    storage_policy_matches,
    write_checkpoint_meta,
)
from codev_platform.recall.code_vector_chroma_config import (
    code_vec_collection_configuration,
    open_code_vec_collection,
)


def _fingerprint() -> dict[str, object]:
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


def test_code_vec_collection使用统一的安全HNSW配置() -> None:
    captured: dict[str, object] = {}
    sentinel = object()

    class _Client:
        def get_or_create_collection(self, **kwargs):
            captured.update(kwargs)
            return sentinel

    assert open_code_vec_collection(_Client(), "demo") is sentinel
    assert captured["name"] == "demo__code_vec"
    assert captured["metadata"] == {"hnsw:space": "cosine"}
    assert captured["configuration"] == {
        "hnsw": {
            "space": "cosine",
            "batch_size": 50_000,
            "sync_threshold": 50_000,
        }
    }
    configuration = code_vec_collection_configuration()
    configuration["hnsw"]["batch_size"] = 1
    assert code_vec_collection_configuration()["hnsw"]["batch_size"] == 50_000


def test旧checkpoint缺少存储策略版本时拒绝续写(tmp_path) -> None:
    meta_path = tmp_path / ".manifest.meta.json"
    write_checkpoint_meta(
        meta_path,
        _fingerprint(),
        embedding_dimension=1024,
        checkpoint_entries=1,
    )
    assert storage_policy_matches(meta_path)

    value = json.loads(meta_path.read_text(encoding="utf-8"))
    value.pop("storage_policy_version")
    meta_path.write_text(json.dumps(value), encoding="utf-8")

    assert not storage_policy_matches(meta_path)


def test旧存储策略会强制进入全量side_build(monkeypatch, tmp_path) -> None:
    from codev_platform.recall import code_vector_build as build

    base = tmp_path / "code_vec" / "demo"
    base.mkdir(parents=True)
    (base / ".manifest.json").write_text("{}", encoding="utf-8")
    target = build._CodeVecBuildTarget(
        build_dir=base / "builds" / "safe",
        manifest_path=base / "builds" / "safe" / ".manifest.json",
        meta_path=base / "builds" / "safe" / ".manifest.meta.json",
        collection=object(),
        build_id="safe",
        resumed=False,
        fingerprint=_fingerprint(),
        embedding_dimension=None,
    )
    opened_full: list[bool] = []
    hooks = build.BuildHooks(
        checkpoint_fingerprint=lambda *_args, **_kwargs: _fingerprint(),
        existing_chroma_healthy=lambda _path: True,
        load_manifest=lambda _path: ({"node": "a" * 40}, True),
        read_enrich_mode=lambda _path: None,
        open_target=lambda *_args, full, **_kwargs: opened_full.append(full) or target,
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
        write_json=lambda *_args, **_kwargs: None,
        write_checkpoint=lambda *_args, **_kwargs: None,
        publish=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(build, "storage_policy_matches", lambda _path: False)
    monkeypatch.setattr(
        "codev_platform.agent.embed.registry.build_code_vec_embedder",
        lambda _cfg: object(),
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda _pid: [])

    assert build._build_locked("demo", base, incremental=True, hooks=hooks) == 1
    assert opened_full == [True]


@pytest.mark.parametrize(("build_id", "expected_checkpoints"), [
    ("side-build", ([1], [1])),
    (None, ([], [])),
])
def test_code_vec_upsert仅为side_build写checkpoint并有界重试(
    monkeypatch, tmp_path, build_id, expected_checkpoints,
) -> None:
    transient = type(
        "InvalidArgumentError",
        (Exception,),
        {"__module__": "chromadb.errors"},
    )("Error in compaction: Error purging logs")

    class Collection:
        def __init__(self) -> None:
            self.calls = 0

        def upsert(self, **_kwargs) -> None:
            self.calls += 1
            if self.calls == 1:
                raise transient

    class Embedder:
        def encode_batch(self, texts):
            return [[1.0, 2.0] for _text in texts]

    collection = Collection()
    waits: list[float] = []
    manifest_checkpoints: list[int] = []
    meta_checkpoints: list[int] = []
    monkeypatch.setattr(collection_integrity.time, "sleep", waits.append)
    target = SimpleNamespace(
        collection=collection,
        build_id=build_id,
        embedding_dimension=None,
        manifest_path=tmp_path / "manifest.json",
        meta_path=tmp_path / "meta.json",
        fingerprint=_fingerprint(),
    )

    persisted, dimension = code_vector_build._upsert_with_checkpoints(
        target,
        Embedder(),
        changed=["node"],
        persisted={},
        manifest_by_id={"node": "a" * 40},
        text_by_id={"node": "text"},
        meta_by_id={"node": {}},
        writer=lambda _path, value: manifest_checkpoints.append(len(value)),
        checkpoint_writer=lambda _path, _fingerprint, **kwargs:
            meta_checkpoints.append(kwargs["checkpoint_entries"]),
    )

    assert collection.calls == 2
    assert waits == [0.5]
    assert persisted == {"node": "a" * 40}
    assert dimension == 2
    assert (manifest_checkpoints, meta_checkpoints) == expected_checkpoints
