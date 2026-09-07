"""运行时对象树持久化封存测试。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from codev_platform import runtime_durable_tree


def test_seal_durable_tree_syncs_every_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "release"
    nested = root / "venv" / "package"
    nested.mkdir(parents=True)
    first = root / "release.json"
    second = nested / "module.py"
    first.write_bytes(b"metadata")
    second.write_bytes(b"source")
    synced: list[Path] = []
    original = runtime_durable_tree._fsync_regular_file

    def record(path: Path, metadata: os.stat_result) -> None:
        synced.append(path)
        original(path, metadata)

    monkeypatch.setattr(runtime_durable_tree, "_fsync_regular_file", record)

    runtime_durable_tree.seal_durable_tree(root)

    assert set(synced) == {first, second}


def test_seal_durable_tree_rejects_special_file(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("FIFO 仅用于 POSIX 特殊文件测试")
    root = tmp_path / "base"
    root.mkdir()
    os.mkfifo(root / "unexpected.fifo")

    with pytest.raises(runtime_durable_tree.RuntimeDurabilityError, match="特殊文件"):
        runtime_durable_tree.seal_durable_tree(root)


def test_目录枚举失败必须fail_closed而不是静默跳过(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "release"
    root.mkdir()

    def failed_walk(
        _root: Path,
        *,
        topdown: bool,
        onerror,
        followlinks: bool,
    ):
        assert topdown is True
        assert followlinks is False
        onerror(PermissionError("不可枚举"))
        return iter(())

    monkeypatch.setattr(runtime_durable_tree.os, "walk", failed_walk)

    with pytest.raises(runtime_durable_tree.RuntimeDurabilityError, match="完整枚举"):
        runtime_durable_tree.seal_durable_tree(root)


def test_目录fsync拒绝打开期间的inode替换(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("目录 fsync 身份门禁仅在 POSIX 验证")
    root = tmp_path / "release"
    root.mkdir()
    before = root.lstat()
    original_fstat = runtime_durable_tree.os.fstat
    calls = 0

    def changed_fstat(descriptor: int):
        nonlocal calls
        calls += 1
        actual = original_fstat(descriptor)
        if calls == 1:
            values = list(actual)
            values[1] += 1
            return os.stat_result(values)
        return actual

    monkeypatch.setattr(runtime_durable_tree.os, "fstat", changed_fstat)

    with pytest.raises(runtime_durable_tree.RuntimeDurabilityError, match="漂移"):
        runtime_durable_tree._fsync_directory(root, before)


def test_posix目录fsync显式拒绝跟随符号链接(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW 仅在 POSIX 验证")
    root = tmp_path / "release"
    root.mkdir()
    before = root.lstat()
    original_open = runtime_durable_tree.os.open
    flags_seen: list[int] = []

    def record_open(path: Path, flags: int) -> int:
        flags_seen.append(flags)
        return original_open(path, flags)

    monkeypatch.setattr(runtime_durable_tree.os, "open", record_open)

    runtime_durable_tree._fsync_directory(root, before)

    assert flags_seen[0] & os.O_NOFOLLOW


def test_seal_failure_keeps_base_incomplete_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import runtime_base
    from tests.test_runtime_base import _install_fake_ports, _lock_info

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    ports = _install_fake_ports(monkeypatch, runtime_base, info)
    ports.seal_durable_tree = lambda _path: (_ for _ in ()).throw(
        runtime_durable_tree.RuntimeDurabilityError("注入封存失败")
    )

    with pytest.raises(runtime_durable_tree.RuntimeDurabilityError, match="注入"):
        runtime_base.build_base(tmp_path / "runtime", lock, tmp_path / "approved.txt")

    bases = tuple((tmp_path / "runtime" / "bases").iterdir())
    assert len(bases) == 1
    assert (bases[0] / ".incomplete").read_text(encoding="ascii") == "after_base_json\n"


def test_seal_failure_keeps_release_incomplete_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import runtime_build
    from codev_platform.core.runtime_models import ReleaseCandidate
    from tests.test_runtime_build import (
        _WHEEL_BYTES,
        _base,
        _candidate_bundle,
        _install_stage_fakes,
    )

    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="d" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_file = _candidate_bundle(tmp_path, candidate)
    monkeypatch.setattr(
        runtime_build,
        "_seal_durable_tree",
        lambda _path: (_ for _ in ()).throw(runtime_build.RuntimeBuildError("注入封存失败")),
    )

    with pytest.raises(runtime_build.RuntimeBuildError, match="注入封存失败"):
        runtime_build.stage_release(
            root,
            wheel,
            candidate_file,
            base.base_id,
        )

    releases = tuple((root / "releases").iterdir())
    assert len(releases) == 1
    assert (releases[0] / ".incomplete").read_text(encoding="ascii") == (
        "after_release_json\n"
    )
