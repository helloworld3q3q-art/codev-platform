from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import os
import sys
import zipfile
from pathlib import Path

import pytest

from codev_platform import runtime_release_generated_bytecode as bytecode
from codev_platform import runtime_wheel


_DIST_INFO = "codev_platform-0.1.0.dist-info"
_RECORD = f"{_DIST_INFO}/RECORD"
_FILES = {
    "codev_platform/__init__.py": b'__version__ = "0.1.0"\n',
    "codev_platform/demo.py": b"VALUE = 1\n",
    f"{_DIST_INFO}/METADATA": (b"Metadata-Version: 2.1\nName: codev-platform\nVersion: 0.1.0\n"),
    f"{_DIST_INFO}/WHEEL": (
        b"Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    ),
}
_LINUX_ROOT = sys.platform.startswith("linux") and getattr(os, "geteuid", lambda: -1)() == 0


def _record(files: dict[str, bytes]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        writer.writerow((name, f"sha256={digest.decode('ascii')}", len(content)))
    writer.writerow((_RECORD, "", ""))
    return output.getvalue().encode("utf-8")


def _write_wheel(path: Path) -> Path:
    payload = dict(_FILES)
    payload[_RECORD] = _record(payload)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
    return path


def _install_application(purelib: Path) -> None:
    payload = dict(_FILES)
    payload[_RECORD] = _record(payload)
    for name, content in payload.items():
        target = purelib / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _controlled_base_pth(purelib: Path, root: Path) -> Path:
    base = root / "base"
    base.mkdir()
    pth = purelib / "codev_platform_base.pth"
    pth.write_text(f"{base.resolve()}\n", encoding="utf-8")
    return pth


def _pycache(purelib: Path, source_relative: str, *, magic: bytes | None = None) -> Path:
    source = purelib / source_relative
    cached = Path(importlib.util.cache_from_source(os.fspath(source)))
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes((importlib.util.MAGIC_NUMBER if magic is None else magic) + b"\x00" * 28)
    return cached


def _setup(tmp_path: Path) -> tuple[Path, object, Path]:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    return purelib, proof, _controlled_base_pth(purelib, tmp_path)


def test只计划wheel载荷证明的生成字节码(tmp_path: Path) -> None:
    purelib, proof, base_pth = _setup(tmp_path)
    _pycache(purelib, "codev_platform/demo.py")

    plan = bytecode.plan_verified_release_generated_bytecode(purelib, proof, base_pth)

    assert plan.candidate_count == 1
    assert plan.cache_directory_count == 1


@pytest.mark.parametrize(
    "mutation", ["unknown_file", "unknown_empty_directory", "wrong_magic", "unclaimed_source"]
)
def testrelease非严格生成字节码候选一律拒绝(tmp_path: Path, mutation: str) -> None:
    purelib, proof, base_pth = _setup(tmp_path)
    if mutation == "unknown_file":
        (purelib / "foreign.py").write_bytes(b"VALUE = 2\n")
    elif mutation == "unknown_empty_directory":
        (purelib / "unknown-empty").mkdir()
    elif mutation == "wrong_magic":
        _pycache(purelib, "codev_platform/demo.py", magic=b"BAD!")
    else:
        source = purelib / "codev_platform/unclaimed.py"
        source.write_bytes(b"VALUE = 2\n")
        _pycache(purelib, "codev_platform/unclaimed.py")

    with pytest.raises(bytecode.RuntimeReleaseGeneratedBytecodeError):
        bytecode.plan_verified_release_generated_bytecode(purelib, proof, base_pth)


@pytest.mark.skipif(not _LINUX_ROOT, reason="fd-relative 清理集成要求 Linux root")
def testrelease删除后复用严格wheel安装校验(tmp_path: Path) -> None:
    purelib, proof, base_pth = _setup(tmp_path)
    cached = _pycache(purelib, "codev_platform/demo.py")

    result = bytecode.apply_verified_release_generated_bytecode(
        bytecode.plan_verified_release_generated_bytecode(purelib, proof, base_pth)
    )

    assert result.deleted_files == 1
    assert result.deleted_cache_directories == 1
    assert not cached.exists()
    runtime_wheel.verify_installed_application(proof, purelib, base_pth)
