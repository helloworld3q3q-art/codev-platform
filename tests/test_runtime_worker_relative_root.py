"""root-fd worker 在执行期根替换后的相对路径边界回归。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from codev_platform import runtime_base
from codev_platform.core.runtime_models import compute_base_id
from codev_platform.runtime_base_bound import _build_request
from codev_platform.runtime_bound_worker_base import build_base, verify_base
from codev_platform.runtime_root_binding import RuntimeRootBindingError
from tests.runtime_base_support import _abi, _install_fake_ports, _lock_info
from tests.test_runtime_build import _base


_POSIX = os.name == "posix"


@pytest.mark.skipif(not _POSIX or os.geteuid() != 0, reason="仅验证 POSIX root-fd worker")
def test基座worker在进入后根替换时不写入替换命名根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_bytes(b"demo==1.0 --hash=sha256:" + b"b" * 64 + b"\n")
    info = _lock_info(lock.read_bytes())
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "bases").mkdir()
    (root / "releases").mkdir()
    previous = tmp_path / "runtime-previous"
    base_id = compute_base_id(info.requirements_sha256, _abi())
    ports = _install_fake_ports(monkeypatch, runtime_base, info)
    base_ports = runtime_base._BasePorts(**vars(ports))
    original_create_venv = base_ports.create_venv

    def replace_before_venv(base_dir: Path):
        root.rename(previous)
        root.mkdir()
        return original_create_venv(base_dir)

    worker_ports = replace(base_ports, create_venv=replace_before_venv)
    monkeypatch.setattr(runtime_base, "_default_ports", lambda: worker_ports)
    contract = ports.validate_lock(lock, tmp_path / "approved.txt")
    payload = _build_request(root, lock, contract, _abi(), base_id, None)
    cwd_descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(root_descriptor)
        with pytest.raises(RuntimeRootBindingError, match="运行时根"):
            build_base(payload, root_descriptor)
    finally:
        os.fchdir(cwd_descriptor)
        os.close(root_descriptor)
        os.close(cwd_descriptor)

    assert not (root / "bases").exists()
    assert not (root / "journal").exists()


@pytest.mark.skipif(not _POSIX or os.geteuid() != 0, reason="仅验证 POSIX root-fd worker")
def test基座静态复验worker在进入后根替换时不读取替换命名根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "releases").mkdir()
    previous = tmp_path / "runtime-previous"
    metadata = _base(root)
    base_id = metadata.base_id

    def replace_before_read(worker_root: Path, *_args: object, **_kwargs: object) -> object:
        root.rename(previous)
        root.mkdir()
        (worker_root / "bases").mkdir(parents=True)
        return metadata

    monkeypatch.setattr(runtime_base, "_verify_locked", replace_before_read)
    payload = {"base_id": base_id, "deep": False, "runtime_root": str(root)}
    cwd_descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(root_descriptor)
        with pytest.raises(RuntimeRootBindingError, match="运行时根"):
            verify_base(payload, root_descriptor)
    finally:
        os.fchdir(cwd_descriptor)
        os.close(root_descriptor)
        os.close(cwd_descriptor)

    assert not (root / "bases").exists()
    assert not (root / "journal").exists()
