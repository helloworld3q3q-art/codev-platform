"""基座重建期间的锁内隔离调用链测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.core.runtime_models import compute_base_id
from tests.runtime_base_support import _abi, _install_fake_ports, _lock_info


def test_未完成基座通过锁内根租约隔离后重建(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    base_id = compute_base_id(info.requirements_sha256, _abi())
    root = tmp_path / "runtime"
    incomplete = root / "bases" / base_id
    incomplete.mkdir(parents=True)
    (incomplete / ".incomplete").write_text("after_install\n", encoding="utf-8")
    (incomplete / "failure-evidence.txt").write_text("保留取证", encoding="utf-8")
    quarantine = root / "quarantine" / "incomplete" / "base" / f"{base_id}.saved"

    def isolate(bound_root: object, kind: str, object_id: str) -> Path:
        assert not isinstance(bound_root, Path)
        assert (kind, object_id) == ("base", base_id)
        quarantine.parent.mkdir(parents=True)
        incomplete.replace(quarantine)
        return quarantine

    _install_fake_ports(
        monkeypatch,
        runtime_base,
        info,
        isolate_incomplete=isolate,
    )

    metadata = runtime_base.build_base(root, lock, tmp_path / "approved.txt")

    assert metadata.base_id == base_id
    assert (quarantine / "failure-evidence.txt").read_text(encoding="utf-8") == "保留取证"
    assert not (root / "bases" / base_id / ".incomplete").exists()


def test_损坏完成基座通过锁内根租约隔离后重建(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    root = tmp_path / "runtime"
    _install_fake_ports(monkeypatch, runtime_base, info)
    original = runtime_base.build_base(root, lock, tmp_path / "approved.txt")
    completed = root / "bases" / original.base_id
    (completed / "base.json").write_text("损坏元数据", encoding="utf-8")
    quarantine = root / "quarantine" / "corrupt" / "base" / f"{original.base_id}.saved"

    def isolate(bound_root: object, kind: str, object_id: str) -> Path:
        assert not isinstance(bound_root, Path)
        assert (kind, object_id) == ("base", original.base_id)
        quarantine.parent.mkdir(parents=True)
        completed.replace(quarantine)
        return quarantine

    _install_fake_ports(monkeypatch, runtime_base, info, isolate_corrupt=isolate)

    rebuilt = runtime_base.build_base(root, lock, tmp_path / "approved.txt")

    assert rebuilt.base_id == original.base_id
    assert (quarantine / "base.json").read_text(encoding="utf-8") == "损坏元数据"
    assert (root / "bases" / original.base_id / "base.json").is_file()


def test_无标记目录通过锁内根租约隔离后重建(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import runtime_base

    lock = tmp_path / "wsl-runtime.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    base_id = compute_base_id(info.requirements_sha256, _abi())
    root = tmp_path / "runtime"
    abandoned = root / "bases" / base_id
    abandoned.mkdir(parents=True)
    (abandoned / "failure-evidence.txt").write_text("创建 marker 前崩溃", encoding="utf-8")
    quarantine = root / "quarantine" / "incomplete" / "base" / f"{base_id}.saved"

    def isolate(bound_root: object, kind: str, object_id: str) -> Path:
        assert not isinstance(bound_root, Path)
        assert (kind, object_id) == ("base", base_id)
        quarantine.parent.mkdir(parents=True)
        abandoned.replace(quarantine)
        return quarantine

    _install_fake_ports(monkeypatch, runtime_base, info, isolate_incomplete=isolate)

    metadata = runtime_base.build_base(root, lock, tmp_path / "approved.txt")

    assert metadata.base_id == base_id
    assert (quarantine / "failure-evidence.txt").is_file()
