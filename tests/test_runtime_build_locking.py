"""薄 release 构建与复验的双锁生命周期测试。"""

from __future__ import annotations

from contextlib import contextmanager
import dataclasses
import hashlib
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import ReleaseCandidate, read_release_metadata
import codev_platform.runtime_build as runtime_build
from tests.test_runtime_build import (
    _WHEEL_BYTES,
    _base,
    _candidate_bundle,
    _install_stage_fakes,
)


def _candidate(revision: str) -> ReleaseCandidate:
    return ReleaseCandidate(
        schema_version=1,
        runtime_revision=revision * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )


def test_stage_holds_release_then_base_lock_through_seal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    wheel, candidate_file = _candidate_bundle(tmp_path, _candidate("a"))
    active: list[tuple[str, str]] = []
    events: list[tuple[str, str]] = []

    @contextmanager
    def lock(_root: Path, kind: str, object_id: str, *, shared: bool):
        events.append(("enter", kind))
        active.append((kind, object_id))
        try:
            yield
        finally:
            assert active.pop() == (kind, object_id)
            events.append(("exit", kind))

    original_stage = runtime_build._stage_environment

    def stage(*args, **kwargs):
        assert [kind for kind, _object_id in active] == ["release", "base"]
        return original_stage(*args, **kwargs)

    def seal(_path: Path) -> None:
        assert [kind for kind, _object_id in active] == ["release", "base"]
        events.append(("action", "seal"))

    monkeypatch.setattr(runtime_build, "_id_lock", lock)
    monkeypatch.setattr(runtime_build, "_stage_environment", stage)
    monkeypatch.setattr(runtime_build, "_seal_durable_tree", seal)

    runtime_build.stage_release(root, wheel, candidate_file, base.base_id)

    assert events == [
        ("enter", "release"),
        ("enter", "base"),
        ("action", "seal"),
        ("exit", "base"),
        ("exit", "release"),
    ]


def test_stage_rechecks_base_after_both_locks_before_creating_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    wheel, candidate_file = _candidate_bundle(tmp_path, _candidate("b"))
    drifted = dataclasses.replace(base, purelib_inventory_sha256="f" * 64)
    monkeypatch.setattr(runtime_build, "_verify_base_locked", lambda *_args: drifted)

    with pytest.raises(runtime_build.RuntimeBuildError, match="锁定前发生漂移"):
        runtime_build.stage_release(root, wheel, candidate_file, base.base_id)

    assert tuple((root / "releases").iterdir()) == ()


def test_verify_release_holds_release_and_base_shared_locks_for_full_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    wheel, candidate_file = _candidate_bundle(tmp_path, _candidate("c"))
    release = runtime_build.stage_release(root, wheel, candidate_file, base.base_id)
    active: list[str] = []
    events: list[tuple[str, str, bool]] = []

    @contextmanager
    def lock(_root: Path, kind: str, _object_id: str, *, shared: bool):
        events.append(("enter", kind, shared))
        active.append(kind)
        try:
            yield
        finally:
            assert active.pop() == kind
            events.append(("exit", kind, shared))

    def verify(_root: Path, release_id: str, **kwargs):
        assert active == ["release", "base"]
        assert kwargs["base_locked"] is True
        assert kwargs["verified_base"] == base
        return read_release_metadata(root / "releases" / release_id / "release.json")

    monkeypatch.setattr(runtime_build, "_id_lock", lock)
    monkeypatch.setattr(runtime_build, "_verify_base_locked", lambda *_args: base)
    monkeypatch.setattr(runtime_build, "_verify_release_directory", verify)

    assert runtime_build.verify_release(root, release.release_id) == release
    assert events == [
        ("enter", "release", True),
        ("enter", "base", True),
        ("exit", "base", True),
        ("exit", "release", True),
    ]
