"""代码向量写端与独立完整性证明之间的生命周期回归。"""

from __future__ import annotations

import pytest

from codev_platform.recall import code_vector_build


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


def _target(base, close_writer):
    return code_vector_build._CodeVecBuildTarget(
        build_dir=base / "builds" / "build-new",
        manifest_path=base / "builds" / "build-new" / ".manifest.json",
        meta_path=base / "builds" / "build-new" / ".manifest.meta.json",
        collection=object(),
        build_id="build-new",
        resumed=False,
        fingerprint=_fingerprint(),
        embedding_dimension=None,
        close_writer=close_writer,
    )


def _hooks(target, events: list[str], verify):
    return code_vector_build.BuildHooks(
        checkpoint_fingerprint=lambda *_args, **_kwargs: target.fingerprint,
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
        verify=verify,
        diff_manifest=lambda _old, new: (list(new), []),
        write_json=lambda *_args, **_kwargs: events.append("manifest"),
        write_checkpoint=lambda *_args, **_kwargs: events.append("meta"),
        publish=lambda *_args, **_kwargs: events.append("published"),
    )


def _prepare_build(monkeypatch) -> None:
    monkeypatch.setattr(
        "codev_platform.agent.embed.registry.build_code_vec_embedder",
        lambda _cfg: object(),
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda _pid: [])


def test构建在独立完整性证明前释放写端客户端(monkeypatch, tmp_path) -> None:
    """跨进程读取前必须先释放本次写端，避免读取未刷新的持久化视图。"""
    events: list[str] = []
    target = _target(tmp_path / "code_vec" / "demo", lambda: events.append("closed"))

    def verify(*_args, **_kwargs) -> int:
        assert events == ["closed"]
        events.append("verified")
        return 1

    _prepare_build(monkeypatch)

    assert (
        code_vector_build._build_locked(
            "demo",
            tmp_path / "code_vec" / "demo",
            incremental=False,
            hooks=_hooks(target, events, verify),
        )
        == 1
    )
    assert events == ["closed", "verified", "meta", "manifest", "published"]


def test写端关闭失败时拒绝完整性证明和发布(monkeypatch, tmp_path) -> None:
    """关闭失败时不能把未证明 collection 写成成功 manifest 或切换指针。"""
    events: list[str] = []

    def close_writer() -> None:
        raise OSError("client close failed")

    target = _target(tmp_path / "code_vec" / "demo", close_writer)
    _prepare_build(monkeypatch)

    with pytest.raises(RuntimeError, match="写入客户端关闭失败"):
        code_vector_build._build_locked(
            "demo",
            tmp_path / "code_vec" / "demo",
            incremental=False,
            hooks=_hooks(target, events, lambda *_args, **_kwargs: events.append("verified") or 1),
        )

    assert events == []


def test增量写失败撤销current_manifest(monkeypatch, tmp_path) -> None:
    """当前库可能部分落盘，必须撤销成功标记让 runner 转 side-build。"""
    base = tmp_path / "code_vec" / "demo"
    base.mkdir(parents=True)
    manifest = base / ".manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    target = code_vector_build._CodeVecBuildTarget(
        base, manifest, base / ".manifest.meta.json", object(), None, False,
        _fingerprint(), None,
    )
    def fail_after_manifest_revoked(*_args, **_kwargs):
        assert not manifest.exists()
        raise OSError("compaction failed")

    hooks = _hooks(target, [], lambda *_args, **_kwargs: 1)
    hooks = code_vector_build.BuildHooks(
        **{
            **vars(hooks),
            "load_manifest": lambda _path: ({"old": "a" * 40}, True),
            "upsert": fail_after_manifest_revoked,
        }
    )
    _prepare_build(monkeypatch)
    monkeypatch.setattr(code_vector_build, "storage_policy_matches", lambda _path: True)

    with pytest.raises(OSError, match="compaction failed"):
        code_vector_build._build_locked("demo", base, incremental=True, hooks=hooks)

    assert not manifest.exists()


def test增量写端关闭失败也撤销current_manifest(monkeypatch, tmp_path) -> None:
    base = tmp_path / "code_vec" / "demo"
    base.mkdir(parents=True)
    manifest = base / ".manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def close_writer() -> None:
        raise OSError("client close failed")

    target = code_vector_build._CodeVecBuildTarget(
        base, manifest, base / ".manifest.meta.json", object(), None, False,
        _fingerprint(), None, close_writer,
    )
    hooks = _hooks(target, [], lambda *_args, **_kwargs: 1)
    hooks = code_vector_build.BuildHooks(**{
        **vars(hooks),
        "load_manifest": lambda _path: ({"old": "a" * 40}, True),
    })
    _prepare_build(monkeypatch)
    monkeypatch.setattr(code_vector_build, "storage_policy_matches", lambda _path: True)

    with pytest.raises(RuntimeError, match="写入客户端关闭失败"):
        code_vector_build._build_locked("demo", base, incremental=True, hooks=hooks)

    assert not manifest.exists()


def test持久客户端缺少关闭接口时拒绝创建构建目标(monkeypatch, tmp_path) -> None:
    """没有可验证的关闭动作时，不能承诺跨进程读取到已刷新的 collection。"""
    import sys

    class Client:
        def __init__(self, *, path: str) -> None:
            self.path = path

        def get_or_create_collection(self, **_kwargs):
            return object()

    class Chroma:
        PersistentClient = Client

    monkeypatch.setitem(sys.modules, "chromadb", Chroma())
    monkeypatch.setattr("codev_platform.chroma.ensure_wal", lambda _path: None)

    with pytest.raises(RuntimeError, match="缺少关闭接口"):
        code_vector_build._open_code_vec_build_target(
            tmp_path / "code_vec" / "demo",
            "demo",
            full=False,
            fingerprint=_fingerprint(),
        )


def test_discard_side_build_closes_writer_before_delete(tmp_path) -> None:
    build_dir = tmp_path / "builds" / "broken"
    build_dir.mkdir(parents=True)
    (build_dir / "chroma.sqlite3").write_bytes(b"locked")
    events: list[str] = []
    target = code_vector_build._CodeVecBuildTarget(
        build_dir, build_dir / ".manifest.json", build_dir / ".manifest.meta.json",
        object(), "broken", True, _fingerprint(), 1,
        lambda: events.append("closed"),
    )
    clean = _target(tmp_path, lambda: None)

    opened = code_vector_build._discard_and_open_clean_full_target(
        target, tmp_path, "demo", opener=lambda *_args, **_kwargs: clean,
    )

    assert opened is clean
    assert events == ["closed"]
    assert not build_dir.exists()


def test_incremental_delete_fallback_closes_current_before_side_open(tmp_path) -> None:
    events: list[str] = []
    current = code_vector_build._CodeVecBuildTarget(
        tmp_path, tmp_path / ".manifest.json", tmp_path / ".manifest.meta.json",
        object(), None, False, _fingerprint(), None,
        lambda: events.append("current_closed"),
    )
    side = _target(tmp_path, lambda: None)

    class Hooks:
        @staticmethod
        def open_target(*_args, **_kwargs):
            events.append("side_opened")
            return side

        resumable_manifest = staticmethod(lambda _target: {})
        diff_manifest = staticmethod(lambda _old, new: (list(new), []))
        delete_stale = staticmethod(lambda *_args, **_kwargs: None)

    code_vector_build._recover_incremental_delete_failure(
        current, tmp_path, "demo", _fingerprint(), {"node": "a" * 40}, Hooks(),
    )

    assert events == ["current_closed", "side_opened"]


def test_full_targets_use_unique_ids_and_never_delete_current(monkeypatch, tmp_path) -> None:
    import sys

    class Client:
        def __init__(self, *, path: str) -> None:
            self.path = path

        def get_or_create_collection(self, **_kwargs):
            return object()

        def close(self) -> None:
            return None

    class Chroma:
        PersistentClient = Client

    base = tmp_path / "code_vec" / "demo"
    current = base / "builds" / "current"
    current.mkdir(parents=True)
    payload = current / "live.txt"
    payload.write_text("live", encoding="utf-8")
    (base / "current.json").write_text('{"build":"current"}', encoding="utf-8")
    monkeypatch.setitem(sys.modules, "chromadb", Chroma())
    monkeypatch.setattr("codev_platform.chroma.ensure_wal", lambda _path: None)

    first = code_vector_build._open_code_vec_build_target(
        base, "demo", full=True, fingerprint=_fingerprint(),
    )
    second = code_vector_build._open_code_vec_build_target(
        base, "demo", full=True, fingerprint=_fingerprint(),
    )

    assert first.build_id != second.build_id
    assert payload.read_text(encoding="utf-8") == "live"
