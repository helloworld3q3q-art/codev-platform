"""应用 wheel 与薄版本已安装源码的一致性测试。"""

from __future__ import annotations

import base64
import csv
from dataclasses import FrozenInstanceError
import hashlib
import io
import os
from pathlib import Path
import stat
import struct
from types import SimpleNamespace
import zipfile

import pytest

from codev_platform import runtime_wheel
from codev_platform import runtime_wheel_archive
from codev_platform import runtime_release_environment
from codev_platform.runtime_errors import RuntimeBuildError
from codev_platform.runtime_wheel import RuntimeWheelError, verify_installed_application


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


def _record(files: dict[str, bytes], record_name: str = _RECORD) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        writer.writerow((name, f"sha256={digest.decode('ascii')}", len(content)))
    writer.writerow((record_name, "", ""))
    return output.getvalue().encode("utf-8")


def _write_wheel(
    path: Path,
    files: dict[str, bytes] | None = None,
    *,
    record: bytes | None = None,
) -> Path:
    payload = dict(files or _FILES)
    payload[_RECORD] = _record(payload) if record is None else record
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in payload.items():
            archive.writestr(name, content)
    return path


def _install_application(purelib: Path, files: dict[str, bytes] | None = None) -> None:
    payload = dict(files or _FILES)
    payload[_RECORD] = _record(payload)
    data_prefix = f"{_DIST_INFO.removesuffix('.dist-info')}.data/purelib/"
    for name, content in payload.items():
        installed = name.removeprefix(data_prefix) if name.startswith(data_prefix) else name
        target = purelib / installed
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _write_controlled_base_pth(purelib: Path, base: Path) -> Path:
    purelib.mkdir(parents=True, exist_ok=True)
    pth = purelib / "codev_platform_base.pth"
    pth.write_text(f"{base.resolve()}\n", encoding="utf-8")
    return pth


def _install_bootstrap(purelib: Path, distribution: str = "pip") -> Path:
    package = distribution.replace("-", "_")
    dist_info = f"{package}-1.0.dist-info"
    record_name = f"{dist_info}/RECORD"
    files = {
        f"{package}/__init__.py": b"VALUE = 'bootstrap'\n",
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {distribution}\nVersion: 1.0\n".encode()
        ),
    }
    files[record_name] = _record(files, record_name)
    for name, content in files.items():
        target = purelib / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return purelib / f"{package}/__init__.py"


@pytest.mark.parametrize(
    "member",
    [
        "foreign.py",
        "sitecustomize.py",
        "foreign.pth",
        "foreign_package/__init__.py",
        "platform_meta_extra/__init__.py",
        f"{_DIST_INFO.removesuffix('.dist-info')}.data/purelib/foreign.py",
        f"{_DIST_INFO.removesuffix('.dist-info')}.data/purelib/sitecustomize.py",
        f"{_DIST_INFO.removesuffix('.dist-info')}.data/purelib/foreign.pth",
    ],
)
def test_wheel_foreign_purelib_payload_is_rejected_before_install(
    tmp_path: Path,
    member: str,
) -> None:
    files = dict(_FILES)
    files[member] = b"raise RuntimeError('must not execute')\n"
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl", files)

    with pytest.raises(RuntimeWheelError, match="purelib"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_valid_wheel_payload_produces_immutable_proof(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")

    proof = runtime_wheel.inspect_application_wheel(wheel)

    assert isinstance(proof, runtime_wheel.WheelPayloadProof)
    assert proof.wheel_path == wheel
    assert proof.dist_info_relative == _DIST_INFO
    with pytest.raises(FrozenInstanceError):
        proof.wheel_path = tmp_path / "replacement.whl"  # type: ignore[misc]


def test_wheel_accepts_declared_platform_meta_payload(tmp_path: Path) -> None:
    """项目登记表是应用分发的精确第二包根，不是任意顶层载荷白名单。"""
    files = dict(_FILES)
    files.update(
        {
            "platform_meta/__init__.py": b'"""registry"""\n',
            "platform_meta/projects/demo/meta.json": b'{"project_id":"demo"}\n',
        }
    )
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl", files)

    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib, files)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")

    runtime_wheel.verify_installed_application(proof, purelib, base_pth)
    assert any(item.installed_relative == "platform_meta/projects/demo/meta.json" for item in proof.application_files)


def test_wheel_member_count_has_fixed_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = dict(_FILES)
    files["codev_platform/extra.py"] = b"VALUE = 2\n"
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl", files)
    monkeypatch.setattr(runtime_wheel_archive, "_MAX_ARCHIVE_MEMBERS", 5)

    with pytest.raises(RuntimeWheelError, match="成员数量.*安全预算"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_central_directory_budget_rejects_before_zipfile_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    monkeypatch.setattr(runtime_wheel_archive, "_MAX_ARCHIVE_MEMBERS", 1)
    monkeypatch.setattr(
        runtime_wheel_archive.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: pytest.fail("中央目录预算前不得构造 ZipFile"),
    )

    with pytest.raises(RuntimeWheelError, match="成员数量.*安全预算"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_rejects_trailing_data_after_eocd(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    with wheel.open("ab") as stream:
        stream.write(b"untrusted-trailer")

    with pytest.raises(RuntimeWheelError, match="EOCD"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_rejects_zip64_eocd_sentinel_before_zipfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "codev_platform-0.1.0-py3-none-any.whl"
    wheel.write_bytes(
        struct.pack(
            "<4s4H2LH",
            b"PK\x05\x06",
            0,
            0,
            0xFFFF,
            0xFFFF,
            0,
            0,
            0,
        )
    )
    monkeypatch.setattr(
        runtime_wheel_archive.zipfile,
        "ZipFile",
        lambda *_args, **_kwargs: pytest.fail("ZIP64 预检前不得构造 ZipFile"),
    )

    with pytest.raises(RuntimeWheelError, match="ZIP64"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_total_uncompressed_size_has_fixed_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    monkeypatch.setattr(runtime_wheel_archive, "_MAX_ARCHIVE_UNCOMPRESSED_BYTES", 1)

    with pytest.raises(RuntimeWheelError, match="解压总量.*安全预算"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_compression_ratio_has_fixed_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = dict(_FILES)
    files["codev_platform/demo.py"] = b"A" * 4096
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl", files)
    monkeypatch.setattr(runtime_wheel_archive, "_COMPRESSION_RATIO_MIN_BYTES", 1)
    monkeypatch.setattr(runtime_wheel_archive, "_MAX_COMPRESSION_RATIO", 2)

    with pytest.raises(RuntimeWheelError, match="压缩比.*安全预算"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_record_rows_have_fixed_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    monkeypatch.setattr(runtime_wheel_archive, "_MAX_RECORD_ROWS", 3)

    with pytest.raises(RuntimeWheelError, match="RECORD 行数.*安全预算"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_rejects_duplicate_installed_purelib_target(tmp_path: Path) -> None:
    files = dict(_FILES)
    files[f"{_DIST_INFO.removesuffix('.dist-info')}.data/purelib/codev_platform/demo.py"] = (
        b"VALUE = 2\n"
    )
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl", files)

    with pytest.raises(RuntimeWheelError, match="目标重复"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_rejects_non_purelib_data_payload(tmp_path: Path) -> None:
    files = dict(_FILES)
    files[f"{_DIST_INFO.removesuffix('.dist-info')}.data/scripts/codev-platform"] = b"bad"
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl", files)

    with pytest.raises(RuntimeWheelError, match="data"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_symbolic_link_member_is_rejected_before_install(tmp_path: Path) -> None:
    files = dict(_FILES)
    files["codev_platform/link.py"] = b"demo.py"
    wheel = tmp_path / "codev_platform-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            if name != "codev_platform/link.py":
                archive.writestr(name, content)
        link = zipfile.ZipInfo("codev_platform/link.py")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, files[link.filename])
        archive.writestr(_RECORD, _record(files))

    with pytest.raises(RuntimeWheelError, match="成员路径"):
        runtime_wheel.inspect_application_wheel(wheel)


@pytest.mark.parametrize("mutation", ["unrecorded", "missing", "hash", "unsafe"])
def test_wheel_record_must_exactly_prove_archive_payload(
    tmp_path: Path,
    mutation: str,
) -> None:
    files = dict(_FILES)
    if mutation == "unrecorded":
        files["codev_platform/unrecorded.py"] = b"x = 1\n"
        record = _record(_FILES)
    elif mutation == "missing":
        recorded = dict(files)
        recorded["codev_platform/missing.py"] = b"x = 1\n"
        record = _record(recorded)
    elif mutation == "hash":
        record = _record(files).replace(b"sha256=", b"sha256=invalid", 1)
    else:
        record = _record(files) + b"../escape.py,sha256=invalid,1\n"
    wheel = _write_wheel(
        tmp_path / "codev_platform-0.1.0-py3-none-any.whl",
        files,
        record=record,
    )

    with pytest.raises(RuntimeWheelError, match="RECORD"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_wheel_metadata_must_declare_codev_platform_identity(tmp_path: Path) -> None:
    files = dict(_FILES)
    files[f"{_DIST_INFO}/METADATA"] = (
        b"Metadata-Version: 2.1\nName: foreign-project\nVersion: 0.1.0\n"
    )
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl", files)

    with pytest.raises(RuntimeWheelError, match="分发身份"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_complete_purelib_inventory_allows_proven_bootstrap_and_base_pth(
    tmp_path: Path,
) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    _install_bootstrap(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")

    runtime_wheel.verify_installed_application(proof, purelib, base_pth)


def test_bootstrap_record_allows_only_source_backed_empty_hash_pyc(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    source = _install_bootstrap(purelib)
    pyc = purelib / "pip/__pycache__/__init__.cpython-313.pyc"
    pyc.parent.mkdir(parents=True)
    pyc.write_bytes(b"trusted bootstrap bytecode")
    metadata = purelib / "pip-1.0.dist-info/METADATA"
    record = purelib / "pip-1.0.dist-info/RECORD"
    recorded = {
        source.relative_to(purelib).as_posix(): source.read_bytes(),
        metadata.relative_to(purelib).as_posix(): metadata.read_bytes(),
    }
    content = _record(recorded, record.relative_to(purelib).as_posix())
    record.write_bytes(content + f"{pyc.relative_to(purelib).as_posix()},,\n".encode())
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")

    runtime_wheel.verify_installed_application(proof, purelib, base_pth)


@pytest.mark.parametrize("relative", ["foreign.py", "sitecustomize.py", "foreign.pth"])
def test_complete_purelib_inventory_rejects_unknown_startup_payload(
    tmp_path: Path,
    relative: str,
) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")
    (purelib / relative).write_bytes(b"raise RuntimeError('must not execute')\n")

    with pytest.raises(RuntimeWheelError, match="未知"):
        runtime_wheel.verify_installed_application(proof, purelib, base_pth)


def test_complete_purelib_inventory_rejects_unproven_distribution(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    _install_bootstrap(purelib, "foreign-bootstrap")
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")

    with pytest.raises(RuntimeWheelError, match="bootstrap"):
        runtime_wheel.verify_installed_application(proof, purelib, base_pth)


def test_complete_purelib_inventory_verifies_bootstrap_record_hash(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    bootstrap = _install_bootstrap(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")
    bootstrap.write_bytes(b"VALUE = 'drifted'\n")

    with pytest.raises(RuntimeWheelError, match="bootstrap.*RECORD"):
        runtime_wheel.verify_installed_application(proof, purelib, base_pth)


def test_bootstrap_record_rejects_variable_length_hash_algorithm(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    bootstrap = _install_bootstrap(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")
    record = purelib / "pip-1.0.dist-info/RECORD"
    metadata = purelib / "pip-1.0.dist-info/METADATA"
    record.write_text(
        f"{bootstrap.relative_to(purelib).as_posix()},shake_128=,{bootstrap.stat().st_size}\n"
        f"{metadata.relative_to(purelib).as_posix()},sha256="
        f"{base64.urlsafe_b64encode(hashlib.sha256(metadata.read_bytes()).digest()).rstrip(b'=').decode()},{metadata.stat().st_size}\n"
        f"{record.relative_to(purelib).as_posix()},,\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeWheelError, match="哈希算法"):
        runtime_wheel.verify_installed_application(proof, purelib, base_pth)


def test_bootstrap_record_cannot_claim_application_payload(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    bootstrap = _install_bootstrap(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")
    record = purelib / "pip-1.0.dist-info/RECORD"
    metadata = purelib / "pip-1.0.dist-info/METADATA"
    claimed = {
        bootstrap.relative_to(purelib).as_posix(): bootstrap.read_bytes(),
        metadata.relative_to(purelib).as_posix(): metadata.read_bytes(),
        "codev_platform/demo.py": (purelib / "codev_platform/demo.py").read_bytes(),
    }
    record.write_bytes(_record(claimed, record.relative_to(purelib).as_posix()))

    with pytest.raises(RuntimeWheelError, match="来源重叠"):
        runtime_wheel.verify_installed_application(proof, purelib, base_pth)


def test_complete_purelib_inventory_rejects_executable_base_pth(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")
    base_pth.write_text("import os\n", encoding="utf-8")

    with pytest.raises(RuntimeWheelError, match="基座.*pth"):
        runtime_wheel.verify_installed_application(proof, purelib, base_pth)


def test_installation_proof_rejects_wheel_drift_after_preflight(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")
    wheel.write_bytes(b"drifted")

    with pytest.raises(RuntimeWheelError, match="wheel.*漂移"):
        runtime_wheel.verify_installed_application(proof, purelib, base_pth)


def test_stage_rejects_wheel_before_starting_any_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def reject(_wheel: Path) -> None:
        events.append("inspect")
        raise RuntimeBuildError("预检拒绝")

    monkeypatch.setattr(
        runtime_release_environment, "_inspect_application_wheel", reject, raising=False
    )
    monkeypatch.setattr(
        runtime_release_environment,
        "_run_checked",
        lambda *_args, **_kwargs: pytest.fail("不可信 wheel 预检前不得启动子进程"),
    )

    with pytest.raises(RuntimeBuildError, match="预检拒绝"):
        runtime_release_environment.stage_environment(
            tmp_path / "release",
            tmp_path / "application.whl",
            tmp_path / "base",
            SimpleNamespace(base_id="b" * 64, purelib_relative="venv/purelib"),
            tmp_path / "marker",
        )

    assert events == ["inspect"]


def test_release_reverification_passes_proof_and_controlled_pth_before_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = object()
    purelib = tmp_path / "purelib"
    observed: list[tuple[object, Path, Path]] = []
    monkeypatch.setattr(runtime_release_environment, "_verify_execution_trust", lambda *_args: None)
    monkeypatch.setattr(
        runtime_release_environment,
        "_inspect_application_wheel",
        lambda _wheel: proof,
        raising=False,
    )

    def reject_after_static_proof(value: object, root: Path, pth: Path) -> None:
        observed.append((value, root, pth))
        raise RuntimeBuildError("静态证明完成")

    monkeypatch.setattr(
        runtime_release_environment,
        "_verify_installed_application",
        reject_after_static_proof,
    )
    monkeypatch.setattr(
        runtime_release_environment,
        "_run_checked",
        lambda *_args, **_kwargs: pytest.fail("静态证明完成前不得执行版本 Python"),
    )

    with pytest.raises(RuntimeBuildError, match="静态证明完成"):
        runtime_release_environment.verify_environment(
            tmp_path / "runtime",
            tmp_path / "release",
            tmp_path / "python",
            purelib,
            tmp_path / "base-purelib",
            tmp_path / "application.whl",
            "0" * 64,
        )

    assert observed == [(proof, purelib, purelib / "codev_platform_base.pth")]


def test_release_reverification可注入同语义安装载荷校验(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proof = object()
    purelib = tmp_path / "purelib"
    base_purelib = tmp_path / "base-purelib"
    base_purelib.mkdir()
    observed: list[tuple[object, Path, Path]] = []
    monkeypatch.setattr(runtime_release_environment, "_verify_execution_trust", lambda *_args: None)
    monkeypatch.setattr(
        runtime_release_environment,
        "_inspect_application_wheel",
        lambda _wheel: proof,
        raising=False,
    )

    def verify(value: object, root: Path, pth: Path) -> None:
        observed.append((value, root, pth))

    runtime_release_environment.verify_environment(
        tmp_path / "runtime",
        tmp_path / "release",
        tmp_path / "python",
        purelib,
        base_purelib,
        tmp_path / "application.whl",
        "0" * 64,
        installed_application_verifier=verify,
    )

    assert observed == [(proof, purelib, purelib / "codev_platform_base.pth")]


def test_exact_installed_application_matches_wheel(tmp_path: Path) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")

    verify_installed_application(proof, purelib, base_pth)


@pytest.mark.parametrize("mutation", ["changed", "missing", "extra"])
def test_source_drift_is_rejected(tmp_path: Path, mutation: str) -> None:
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")
    if mutation == "changed":
        (purelib / "codev_platform/demo.py").write_bytes(b"VALUE = 2\n")
    elif mutation == "missing":
        (purelib / "codev_platform/demo.py").unlink()
    else:
        (purelib / "codev_platform/extra.py").write_bytes(b"unexpected = True\n")

    with pytest.raises(RuntimeWheelError, match="源码"):
        verify_installed_application(proof, purelib, base_pth)


def test_installed_source_symlink_is_rejected(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows 测试环境不保证普通用户可创建符号链接")
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl")
    proof = runtime_wheel.inspect_application_wheel(wheel)
    purelib = tmp_path / "purelib"
    _install_application(purelib)
    base_pth = _write_controlled_base_pth(purelib, tmp_path / "base")
    target = purelib / "codev_platform/demo.py"
    target.unlink()
    target.symlink_to(tmp_path / "outside.py")

    with pytest.raises(RuntimeWheelError, match="符号链接"):
        verify_installed_application(proof, purelib, base_pth)


@pytest.mark.parametrize("unsafe_name", ["../escape.py", "/absolute.py", "bad//name.py"])
def test_wheel_unsafe_member_is_rejected(tmp_path: Path, unsafe_name: str) -> None:
    files = dict(_FILES)
    files[unsafe_name] = b"escape\n"
    wheel = _write_wheel(tmp_path / "codev_platform-0.1.0-py3-none-any.whl", files)

    with pytest.raises(RuntimeWheelError, match="成员路径"):
        runtime_wheel.inspect_application_wheel(wheel)


def test_duplicate_wheel_member_is_rejected(tmp_path: Path) -> None:
    wheel = tmp_path / "codev_platform-0.1.0-py3-none-any.whl"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, content in _FILES.items():
                archive.writestr(name, content)
            archive.writestr("codev_platform/demo.py", b"duplicate\n")
    with pytest.raises(RuntimeWheelError, match="重复"):
        runtime_wheel.inspect_application_wheel(wheel)
