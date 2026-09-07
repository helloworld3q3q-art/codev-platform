"""内容寻址依赖基座的构建与完整性测试。"""

from __future__ import annotations

import dataclasses
import hashlib
import stat
from pathlib import Path

import pytest

from codev_platform import _runtime_base_files
from codev_platform.core.runtime_models import compute_base_id
from tests.runtime_base_support import _abi, _install_fake_ports, _lock_info


def test_base_id_is_canonical_lock_plus_abi_sha256(tmp_path: Path, monkeypatch) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    _install_fake_ports(monkeypatch, runtime_base, info)

    metadata = runtime_base.build_base(tmp_path / "runtime", lock, tmp_path / "approved.txt")

    assert metadata.base_id == compute_base_id(info.requirements_sha256, _abi())
    assert len(metadata.base_id) == 64


def test_build_uses_final_venv_hash_lock_and_fixed_probe_order(tmp_path: Path, monkeypatch) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock_bytes = b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n"
    lock.write_bytes(lock_bytes)
    info = _lock_info(lock_bytes)
    events: list[object] = []
    freeze = b"demo==1.0\npip==26.0\n"
    _install_fake_ports(monkeypatch, runtime_base, info, events=events, freeze=freeze)
    root = tmp_path / "runtime"

    metadata = runtime_base.build_base(root, lock, tmp_path / "approved.txt")

    base_dir = root / "bases" / metadata.base_id
    assert (base_dir / "requirements.lock").read_bytes() == lock_bytes
    assert (base_dir / "base.json").is_file()
    assert not (base_dir / ".incomplete").exists()
    assert ("create_venv", base_dir) in events
    python_events = [event for event in events if event[0] == "python"]
    arguments = [event[2] for event in python_events[:4]]
    assert arguments[0] == (
        "-B",
        "-I",
        "-m",
        "pip",
        "--isolated",
        "--disable-pip-version-check",
        "--no-input",
        "install",
        "--require-hashes",
        "--no-compile",
        "-r",
        str(base_dir / "requirements.lock"),
    )
    assert arguments[1] == ("-B", "-I", "-m", "pip", "check")
    assert arguments[2][:3] == ("-B", "-I", "-c")
    assert "codev-platform" in arguments[2][3]
    assert arguments[3] == ("-B", "-I", "-m", "pip", "freeze", "--all")
    assert metadata.freeze_sha256 == hashlib.sha256(freeze).hexdigest()
    assert events.index(("seal_durable_tree", base_dir)) < len(events)


def test_build_seals_modes_before_inventory_and_after_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    events: list[object] = []
    _install_fake_ports(monkeypatch, runtime_base, info, events=events)
    original_stage = runtime_base._write_stage
    original_metadata = runtime_base.write_base_metadata_atomic

    def record_stage(marker: Path, stage: str) -> None:
        events.append(("stage", stage))
        original_stage(marker, stage)

    def record_metadata(path: Path, metadata) -> None:
        events.append(("base_json", path))
        original_metadata(path, metadata)

    monkeypatch.setattr(runtime_base, "_write_stage", record_stage)
    monkeypatch.setattr(runtime_base, "write_base_metadata_atomic", record_metadata)

    metadata = runtime_base.build_base(
        tmp_path / "runtime",
        lock,
        tmp_path / "approved.txt",
    )

    base_dir = tmp_path / "runtime" / "bases" / metadata.base_id
    seal = ("seal_object_access", base_dir)
    verify = ("verify_object_access", base_dir)
    inventories = [index for index, event in enumerate(events) if event[0] == "inventory"]
    seals = [index for index, event in enumerate(events) if event == seal]
    assert len(inventories) == 3
    assert len(seals) == 2
    assert events.index(("stage", "after_install")) < seals[0] < inventories[0]
    assert inventories[1] < events.index(("base_json", base_dir / "base.json"))
    assert (
        events.index(("base_json", base_dir / "base.json"))
        < events.index(("stage", "after_base_json"))
        < seals[1]
        < events.index(verify)
        < inventories[2]
    )


def test_build_with_wheelhouse_is_offline_and_reverifies_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    events: list[object] = []
    _install_fake_ports(monkeypatch, runtime_base, info, events=events)
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()

    runtime_base.build_base(
        tmp_path / "runtime",
        lock,
        tmp_path / "approved.txt",
        wheelhouse,
    )

    install = next(event[2] for event in events if event[0] == "python" and "install" in event[2])
    assert "--no-index" in install
    assert install[install.index("--find-links") + 1] == str(wheelhouse)
    assert events.count(("wheelhouse", wheelhouse)) == 2


def test_existing_verified_base_is_reused_without_rebuilding(tmp_path: Path, monkeypatch) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    events: list[object] = []
    _install_fake_ports(monkeypatch, runtime_base, info, events=events)
    root = tmp_path / "runtime"
    first = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    assert runtime_base.verify_base(root, first.base_id) == first
    assert ("lock", "base", first.base_id, True) in events
    events.clear()

    second = runtime_base.build_base(root, lock, tmp_path / "approved.txt")

    assert second == first
    assert not any(event[0] == "create_venv" for event in events if isinstance(event, tuple))
    assert events.count(("verify_object_access", root / "bases" / first.base_id)) == 1
    assert not any(event[0] == "seal_object_access" for event in events)


def test_repeated_base_verification_keeps_inventory_and_metadata_bytes_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    _install_fake_ports(monkeypatch, runtime_base, info)
    root = tmp_path / "runtime"
    built = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    metadata_file = root / "bases" / built.base_id / "base.json"
    before = metadata_file.read_bytes()

    first = runtime_base.verify_base(root, built.base_id)
    second = runtime_base.verify_base(root, built.base_id)

    assert first == second == built
    assert first.purelib_inventory_sha256 == built.purelib_inventory_sha256
    assert metadata_file.read_bytes() == before


def test_locked_verifier_does_not_reacquire_base_lock(tmp_path: Path, monkeypatch) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    events: list[object] = []
    _install_fake_ports(monkeypatch, runtime_base, info, events=events)
    root = tmp_path / "runtime"
    built = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    events.clear()

    assert runtime_base.verify_base_locked(root, built.base_id) == built
    assert not any(event[0] == "lock" for event in events if isinstance(event, tuple))


def test_forced_base_id_collision_fails_without_overwrite(tmp_path: Path, monkeypatch) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    _install_fake_ports(monkeypatch, runtime_base, info)
    root = tmp_path / "runtime"
    built = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    metadata_path = root / "bases" / built.base_id / "base.json"
    original = metadata_path.read_bytes()
    conflicting = dataclasses.replace(info, artifact_manifest_sha256="c" * 64)
    _install_fake_ports(
        monkeypatch,
        runtime_base,
        conflicting,
        inspected_lock_info=info,
    )

    with pytest.raises(runtime_base.RuntimeIdCollisionError):
        runtime_base.build_base(root, lock, tmp_path / "approved.txt")

    assert metadata_path.read_bytes() == original


def test_forced_hash_collision_classifies_identity_input_before_integrity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base

    first_lock = tmp_path / "first.lock"
    first_lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    first_info = _lock_info(first_lock.read_bytes())
    _install_fake_ports(monkeypatch, runtime_base, first_info)
    root = tmp_path / "runtime"
    built = runtime_base.build_base(root, first_lock, tmp_path / "approved.txt")
    metadata_path = root / "bases" / built.base_id / "base.json"
    original = metadata_path.read_bytes()

    second_lock = tmp_path / "second.lock"
    second_lock.write_bytes(b"demo==2.0 --hash=sha256:" + b"c" * 64 + b"\n")
    second_info = _lock_info(second_lock.read_bytes())
    second_abi = dataclasses.replace(_abi(), machine="aarch64")
    _install_fake_ports(
        monkeypatch,
        runtime_base,
        second_info,
        inspected_lock_info=first_info,
        runtime_abi=second_abi,
    )
    monkeypatch.setattr(runtime_base, "compute_base_id", lambda _sha, _abi_value: built.base_id)

    with pytest.raises(runtime_base.RuntimeIdCollisionError):
        runtime_base.build_base(root, second_lock, tmp_path / "approved.txt")

    assert metadata_path.read_bytes() == original


def test_base_json_is_reverified_before_completion_marker_is_removed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    _install_fake_ports(monkeypatch, runtime_base, info)
    root = tmp_path / "runtime"
    base_id = compute_base_id(info.requirements_sha256, _abi())

    def write_corrupted_metadata(path: Path, _metadata) -> None:
        path.write_bytes(b"{}")

    monkeypatch.setattr(runtime_base, "write_base_metadata_atomic", write_corrupted_metadata)

    with pytest.raises(runtime_base.RuntimeBaseIntegrityError):
        runtime_base.build_base(root, lock, tmp_path / "approved.txt")

    marker = root / "bases" / base_id / ".incomplete"
    assert marker.read_text(encoding="ascii") == "after_base_json\n"


def test_venv_layout_escape_fails_before_hash_install(tmp_path: Path, monkeypatch) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    outside = tmp_path / "outside"
    outside.mkdir()
    python = outside / "python"
    python.write_bytes(b"foreign")
    purelib = outside / "site-packages"
    purelib.mkdir()

    def escaped_layout(_base_dir: Path):
        return runtime_base._VenvLayout(python=python, purelib=purelib, bin_dir=outside)

    def forbidden_run(_python: Path, _arguments: tuple[str, ...]) -> bytes:
        raise AssertionError("路径围栏失败后不得执行 Python")

    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        create_venv_override=escaped_layout,
        run_python_override=forbidden_run,
    )

    with pytest.raises(runtime_base.RuntimeBaseIntegrityError, match="逃逸"):
        runtime_base.build_base(tmp_path / "runtime", lock, tmp_path / "approved.txt")


def test_corrupted_saved_lock_is_rejected_before_venv_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())

    def corrupt_copy(_source: Path, destination: Path) -> Path:
        destination.write_bytes(b"corrupted\n")
        return destination

    def forbidden_create(_base_dir: Path):
        raise AssertionError("保存锁摘要不一致时不得创建或执行 venv")

    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        create_venv_override=forbidden_create,
    )
    monkeypatch.setattr(_runtime_base_files.shutil, "copyfile", corrupt_copy)

    with pytest.raises(runtime_base.RuntimeBaseIntegrityError, match="依赖锁"):
        runtime_base.build_base(tmp_path / "runtime", lock, tmp_path / "approved.txt")


def test_verify_rejects_writable_python_before_dynamic_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    root = tmp_path / "runtime"
    _install_fake_ports(monkeypatch, runtime_base, info)
    built = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    python = root / "bases" / built.base_id / built.python_relative
    python.chmod(0o777)
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def verify_trust(
        _root: Path,
        _base: Path,
        executable: Path,
        _purelib: Path,
        _bin_dir: Path,
    ) -> None:
        if stat.S_IMODE(executable.stat().st_mode) & (stat.S_IWGRP | stat.S_IWOTH):
            raise runtime_base.RuntimeBaseIntegrityError("基座解释器对非所有者可写")

    def record_run(executable: Path, arguments: tuple[str, ...]) -> bytes:
        calls.append((executable, arguments))
        return b""

    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        run_python_override=record_run,
        verify_execution_trust_override=verify_trust,
    )

    with pytest.raises(runtime_base.RuntimeBaseIntegrityError, match="可写"):
        runtime_base.verify_base(root, built.base_id)

    assert calls == []
