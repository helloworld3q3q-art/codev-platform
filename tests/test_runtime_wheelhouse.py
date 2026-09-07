"""离线 wheelhouse 与 hash lock 一一对应测试。"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import RequirementsLockInfo
from codev_platform.runtime_dependency_contract import (
    DistributionPin,
    LockedWheelArtifact,
    RequirementsLockContract,
)
from codev_platform.runtime_wheelhouse import (
    RuntimeWheelhouseError,
    verify_locked_wheelhouse,
)


def _contract(files: dict[str, bytes]) -> RequirementsLockContract:
    artifacts = tuple(
        LockedWheelArtifact(
            filename.split("-", 1)[0].replace("_", "-"),
            filename,
            hashlib.sha256(content).hexdigest(),
        )
        for filename, content in sorted(files.items())
    )
    pins = tuple(
        DistributionPin(artifact.package, "1.0")
        for artifact in sorted(artifacts, key=lambda item: item.package)
    )
    info = RequirementsLockInfo(
        requirements_sha256="a" * 64,
        approved_index_url="https://download.pytorch.org/whl/cu128",
        pin_count=len(pins),
        artifact_manifest_sha256="b" * 64,
        cuda_tags=frozenset({"cu128"}),
    )
    return RequirementsLockContract(info, pins, artifacts)


def _write_wheelhouse(root: Path, files: dict[str, bytes]) -> Path:
    root.mkdir()
    for filename, content in files.items():
        (root / filename).write_bytes(content)
    return root


def test_exact_wheelhouse_is_accepted(tmp_path: Path) -> None:
    files = {
        "demo-1.0-py3-none-any.whl": b"demo",
        "other-1.0-py3-none-any.whl": b"other",
    }
    wheelhouse = _write_wheelhouse(tmp_path / "wheelhouse", files)

    assert verify_locked_wheelhouse(_contract(files), wheelhouse) == wheelhouse


@pytest.mark.parametrize("mutation", ["missing", "extra", "hash"])
def test_wheelhouse_drift_fails_closed(tmp_path: Path, mutation: str) -> None:
    files = {"demo-1.0-py3-none-any.whl": b"demo"}
    wheelhouse = _write_wheelhouse(tmp_path / "wheelhouse", files)
    contract = _contract(files)
    if mutation == "missing":
        (wheelhouse / next(iter(files))).unlink()
    elif mutation == "extra":
        (wheelhouse / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")
    else:
        (wheelhouse / next(iter(files))).write_bytes(b"drift")

    with pytest.raises(RuntimeWheelhouseError):
        verify_locked_wheelhouse(contract, wheelhouse)


def test_wheelhouse_rejects_relative_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    files = {"demo-1.0-py3-none-any.whl": b"demo"}
    _write_wheelhouse(Path("wheelhouse"), files)

    with pytest.raises(RuntimeWheelhouseError, match="绝对路径"):
        verify_locked_wheelhouse(_contract(files), Path("wheelhouse"))


def test_wheelhouse_rejects_wheel_incompatible_with_current_abi(tmp_path: Path) -> None:
    files = {"demo-1.0-cp27-cp27m-manylinux1_x86_64.whl": b"demo"}
    wheelhouse = _write_wheelhouse(tmp_path / "wheelhouse", files)

    with pytest.raises(RuntimeWheelhouseError, match="ABI 不兼容"):
        verify_locked_wheelhouse(_contract(files), wheelhouse)
