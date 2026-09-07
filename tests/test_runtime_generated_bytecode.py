from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import os
import sys
from pathlib import Path

import pytest

from codev_platform import runtime_generated_bytecode as bytecode
from codev_platform import runtime_generated_bytecode_core as bytecode_core
from codev_platform.runtime_dependency_contract import DistributionPin


_LINUX_ROOT = sys.platform.startswith("linux") and getattr(os, "geteuid", lambda: -1)() == 0


def _record_content(files: dict[str, bytes], record_relative: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for relative, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        writer.writerow((relative, f"sha256={digest.decode('ascii')}", len(content)))
    writer.writerow((record_relative, "", ""))
    return output.getvalue().encode("utf-8")


def _install_distribution(
    purelib: Path,
    *,
    extra: dict[str, bytes] | None = None,
) -> None:
    dist_info = "demo-1.0.dist-info"
    files = {
        "demo/__init__.py": b"VALUE = 1\n",
        f"{dist_info}/METADATA": b"Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n",
        **(extra or {}),
    }
    record = f"{dist_info}/RECORD"
    files[record] = _record_content(files, record)
    for relative, content in files.items():
        target = purelib / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _pycache_file(purelib: Path, source_relative: str, *, magic: bytes | None = None) -> Path:
    source = purelib / source_relative
    cached = Path(importlib.util.cache_from_source(os.fspath(source)))
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes((importlib.util.MAGIC_NUMBER if magic is None else magic) + b"\x00" * 28)
    return cached


def _pins() -> tuple[DistributionPin, ...]:
    return (DistributionPin("demo", "1.0"),)


def test只计划已证明的生成字节码且虚拟清单可恢复(tmp_path: Path) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib)
    _pycache_file(purelib, "demo/__init__.py")

    plan = bytecode.plan_verified_generated_bytecode(purelib, _pins())

    assert plan.candidate_count == 1
    assert plan.cache_directory_count == 1
    assert plan.inventory_proof.file_count == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_file",
        "unknown_empty_directory",
        "wrong_magic",
        "wrong_cache_tag",
        "unclaimed_source",
    ],
)
def test非严格生成字节码候选一律拒绝(tmp_path: Path, mutation: str) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib)
    if mutation == "unknown_file":
        (purelib / "unknown.py").write_bytes(b"unexpected\n")
    elif mutation == "unknown_empty_directory":
        (purelib / "unknown-empty").mkdir()
    elif mutation == "wrong_magic":
        _pycache_file(purelib, "demo/__init__.py", magic=b"BAD!")
    elif mutation == "wrong_cache_tag":
        cache = purelib / "demo/__pycache__/__init__.untrusted.pyc"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(importlib.util.MAGIC_NUMBER + b"\x00" * 28)
    else:
        source = purelib / "demo/unclaimed.py"
        source.write_bytes(b"VALUE = 2\n")
        _pycache_file(purelib, "demo/unclaimed.py")

    with pytest.raises(bytecode.RuntimeGeneratedBytecodeError):
        bytecode.plan_verified_generated_bytecode(purelib, _pins())


@pytest.mark.skipif(not _LINUX_ROOT, reason="fd-relative 清理集成要求 Linux root")
def test删除后复用严格inventory并移除空缓存目录(tmp_path: Path) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib)
    cached = _pycache_file(purelib, "demo/__init__.py")

    proof = bytecode.apply_verified_generated_bytecode(
        bytecode.plan_verified_generated_bytecode(purelib, _pins())
    )

    assert proof.deleted_files == 1
    assert proof.deleted_cache_directories == 1
    assert not cached.exists()
    assert proof.inventory_proof.file_count == 3


@pytest.mark.skipif(not _LINUX_ROOT, reason="fd-relative 清理集成要求 Linux root")
def test删除中断后只留下可重试的可信缓存状态(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    purelib = tmp_path / "site-packages"
    _install_distribution(purelib, extra={"demo/other.py": b"VALUE = 2\n"})
    first = _pycache_file(purelib, "demo/__init__.py")
    second = _pycache_file(purelib, "demo/other.py")
    real_unlink = bytecode_core.os.unlink
    calls = 0

    def interrupt_second(name, *, dir_fd=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("模拟中断")
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr(bytecode_core.os, "unlink", interrupt_second)
    with pytest.raises(bytecode.RuntimeGeneratedBytecodeError):
        bytecode.apply_verified_generated_bytecode(
            bytecode.plan_verified_generated_bytecode(purelib, _pins())
        )

    assert sum(path.exists() for path in (first, second)) == 1
    monkeypatch.setattr(bytecode_core.os, "unlink", real_unlink)
    proof = bytecode.apply_verified_generated_bytecode(
        bytecode.plan_verified_generated_bytecode(purelib, _pins())
    )

    assert proof.deleted_files == 1
    assert not first.exists()
    assert not second.exists()
