from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import codev_platform.runtime_base as runtime_base
import codev_platform.runtime_build as runtime_build
from codev_platform.runtime_base_bound import run_bound_build
from codev_platform.runtime_build_bound import stage_bound_release
from codev_platform.core.runtime_models import ReleaseCandidate
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_storage import RuntimeStoragePathError, object_lock
from tests.runtime_base_support import _install_fake_ports, _lock_info
from tests.test_runtime_build import _base, _candidate_bundle, _install_stage_fakes


_WHEEL_BYTES = b"trusted application wheel"
_POSIX = os.name == "posix"


@pytest.mark.skipif(not _POSIX, reason="仅验证 POSIX 根绑定")
def test基座构建在锁后根替换时不写入替换命名根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    root = tmp_path / "runtime"
    root.mkdir()
    previous = tmp_path / "runtime-previous"
    ports = _install_fake_ports(monkeypatch, runtime_base, info)
    ports.id_lock = object_lock
    monkeypatch.setattr(runtime_base, "_run_bound_build", run_bound_build)
    original_duplicate = BoundRuntimeRoot._duplicate_root_fd

    def duplicate_then_replace(bound_root: BoundRuntimeRoot) -> int:
        descriptor = original_duplicate(bound_root)
        root.rename(previous)
        root.mkdir()
        return descriptor

    monkeypatch.setattr(BoundRuntimeRoot, "_duplicate_root_fd", duplicate_then_replace)

    with pytest.raises(RuntimeStoragePathError, match="根目录"):
        runtime_base.build_base(root, lock, tmp_path / "approved.txt")

    assert not (root / "bases").exists()
    assert not (root / "journal").exists()


@pytest.mark.skipif(not _POSIX, reason="仅验证 POSIX 根绑定")
def testrelease构建在锁后根替换时不写入替换命名根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    monkeypatch.setattr(runtime_build, "_run_bound_stage_release", stage_bound_release)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="a" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_path = _candidate_bundle(tmp_path, candidate, _WHEEL_BYTES)
    previous = tmp_path / "runtime-previous"
    original_duplicate = BoundRuntimeRoot._duplicate_root_fd

    def duplicate_then_replace(bound_root: BoundRuntimeRoot) -> int:
        descriptor = original_duplicate(bound_root)
        root.rename(previous)
        root.mkdir()
        return descriptor

    monkeypatch.setattr(BoundRuntimeRoot, "_duplicate_root_fd", duplicate_then_replace)

    with pytest.raises(RuntimeStoragePathError, match="根目录"):
        runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    replacement_releases = root / "releases"
    assert not replacement_releases.exists() or not tuple(replacement_releases.iterdir())
    assert not (root / "journal").exists()
    assert (previous / "releases").exists()
