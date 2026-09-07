"""默认生产端口集成测试使用的离线真实运行时夹具。"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from codev_platform.core.runtime_models import (
    BaseMetadata,
    ReleaseCandidate,
    ReleaseMetadata,
    sha256_file,
    write_release_candidate_atomic,
)
from codev_platform.runtime_base import build_base
from codev_platform.runtime_build import stage_release
from codev_platform.runtime_candidate import compute_candidate_id
from codev_platform.runtime_dependency_contract import REQUIRED_RUNTIME_DISTRIBUTIONS
from codev_platform.runtime_lock import validate_requirements_lock_contract
from codev_platform.runtime_lock_parsing import LOCK_HEADER
from codev_platform.runtime_release_environment import MANAGED_IMPORTS
from codev_platform.runtime_service_access import (
    converge_runtime_service_namespace,
    publish_runtime_service_objects,
)
from codev_platform.runtime_service_process import ServiceAccount, resolve_service_account


_WORKSPACE_PREFIX = "codev-target-probe-"
_PRIMARY_INDEX = "https://pypi.org/simple"
_CUDA_INDEX = "https://download.pytorch.org/whl/cu128"
_APP_VERSION = "0.1.0"
_RUNTIME_REVISION = "7" * 40
_BOOTSTRAP_WHEEL_ROOT = Path("/usr/share/python-wheels")
_CORE_IDENTITY_MODULES = (
    "runtime_identity.py",
    "runtime_metadata_io.py",
    "runtime_models.py",
    "runtime_release_identity.py",
)


class VerifiedTargetProbeFixtureError(RuntimeError):
    """离线真实运行时夹具无法形成完整生产契约。"""


@dataclass(frozen=True, slots=True)
class VerifiedTargetProbeRuntime:
    """已完成服务组发布的真实基座、版本与目标账号。"""

    root: Path
    base: BaseMetadata
    release: ReleaseMetadata
    account: ServiceAccount


@dataclass(frozen=True, slots=True)
class _WheelArtifact:
    package: str
    version: str
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class _BaseInputs:
    lock: Path
    approved: Path
    wheelhouse: Path


@contextmanager
def verified_target_probe_runtime() -> Iterator[VerifiedTargetProbeRuntime]:
    """构建并发布独立运行时；无论成功失败都删除唯一 `/run` 工作区。"""
    workspace = Path(tempfile.mkdtemp(prefix=_WORKSPACE_PREFIX, dir="/run"))
    try:
        workspace.chmod(0o755)
        yield _build_verified_runtime(workspace)
    finally:
        _remove_workspace(workspace)


def _build_verified_runtime(workspace: Path) -> VerifiedTargetProbeRuntime:
    inputs = _prepare_base_inputs(workspace / "inputs")
    root = workspace / "runtime"
    base = build_base(root, inputs.lock, inputs.approved, inputs.wheelhouse)
    (root / "releases").mkdir(mode=0o755)
    wheel, candidate = _build_application_candidate(workspace / "candidates")
    release = stage_release(root, wheel, candidate, base.base_id)
    account = resolve_service_account("nobody")
    converge_runtime_service_namespace(
        root,
        service_uid=account.uid,
        service_gid=account.gid,
    )
    publish_runtime_service_objects(
        root,
        service_uid=account.uid,
        service_gid=account.gid,
        base_ids=(base.base_id,),
        release_ids=(release.release_id,),
    )
    return VerifiedTargetProbeRuntime(root, base, release, account)


def _prepare_base_inputs(root: Path) -> _BaseInputs:
    root.mkdir(mode=0o755)
    wheelhouse = root / "wheelhouse"
    wheelhouse.mkdir(mode=0o755)
    artifacts = [
        _write_dependency_wheel(
            wheelhouse,
            package,
            _dependency_version(package),
            package.replace("-", "_"),
        )
        for package in sorted(REQUIRED_RUNTIME_DISTRIBUTIONS)
    ]
    artifacts.extend(_copy_bootstrap_wheels(wheelhouse))
    selected = tuple(sorted(artifacts, key=lambda item: item.package))
    approved = _write_approved(root / "requirements-runtime.txt")
    lock = _write_lock(root / "wsl-runtime.lock", selected)
    validate_requirements_lock_contract(lock, approved)
    return _BaseInputs(lock=lock, approved=approved, wheelhouse=wheelhouse)


def _write_dependency_wheel(
    wheelhouse: Path,
    package: str,
    version: str,
    module: str,
) -> _WheelArtifact:
    filename = f"{package.replace('-', '_')}-{version}-py3-none-any.whl"
    path = wheelhouse / filename
    payload: dict[str, bytes] = {}
    _add_python_module(
        payload,
        module,
        f"__version__ = {version!r}\n".encode("ascii"),
    )
    _add_distribution_metadata(payload, package, version)
    _write_wheel(path, payload)
    return _WheelArtifact(package, version, path, sha256_file(path))


def _copy_bootstrap_wheels(wheelhouse: Path) -> tuple[_WheelArtifact, ...]:
    artifacts: list[_WheelArtifact] = []
    for expected in ("pip", "setuptools"):
        source, version = _unique_bootstrap_wheel(expected)
        destination = wheelhouse / source.name
        shutil.copyfile(source, destination)
        destination.chmod(0o644)
        artifacts.append(_WheelArtifact(expected, version, destination, sha256_file(destination)))
    return tuple(artifacts)


def _unique_bootstrap_wheel(expected: str) -> tuple[Path, str]:
    matches: list[tuple[Path, str]] = []
    if _BOOTSTRAP_WHEEL_ROOT.is_dir():
        for path in sorted(_BOOTSTRAP_WHEEL_ROOT.glob("*.whl")):
            package, version = _read_wheel_identity(path)
            if package == expected:
                matches.append((path, version))
    if len(matches) != 1:
        raise VerifiedTargetProbeFixtureError(f"系统 {expected} wheel 不唯一")
    return matches[0]


def _read_wheel_identity(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA") and name.count("/") == 1
            ]
            if len(names) != 1:
                raise VerifiedTargetProbeFixtureError("bootstrap wheel 元数据不唯一")
            metadata = BytesParser(policy=policy.compat32).parsebytes(archive.read(names[0]))
    except (OSError, KeyError, zipfile.BadZipFile):
        raise VerifiedTargetProbeFixtureError("bootstrap wheel 不可读") from None
    names = metadata.get_all("Name", [])
    versions = metadata.get_all("Version", [])
    if len(names) != 1 or len(versions) != 1:
        raise VerifiedTargetProbeFixtureError("bootstrap wheel 身份无效")
    package = _canonical_name(str(names[0]))
    version = str(versions[0])
    if not package or not version or any(char.isspace() for char in version):
        raise VerifiedTargetProbeFixtureError("bootstrap wheel 身份无效")
    return package, version


def _write_approved(path: Path) -> Path:
    path.write_text(
        f"--index-url {_PRIMARY_INDEX}\n--extra-index-url {_CUDA_INDEX}\n",
        encoding="ascii",
    )
    return path


def _write_lock(path: Path, artifacts: tuple[_WheelArtifact, ...]) -> Path:
    lines = [
        LOCK_HEADER,
        f"--index-url {_PRIMARY_INDEX}",
        f"--extra-index-url {_CUDA_INDEX}",
        *(
            f"# codev-artifact {item.package} {item.path.name} {item.sha256}"
            for item in sorted(artifacts, key=lambda item: item.path.name)
        ),
        *(f"{item.package}=={item.version} --hash=sha256:{item.sha256}" for item in artifacts),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def _build_application_candidate(root: Path) -> tuple[Path, Path]:
    root.mkdir(mode=0o755)
    staging = root / "codev_platform-0.1.0-py3-none-any.whl"
    _write_wheel(staging, _application_payload())
    digest = sha256_file(staging)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision=_RUNTIME_REVISION,
        wheel_name=staging.name,
        wheel_sha256=digest,
    )
    directory = root / compute_candidate_id(_RUNTIME_REVISION, digest)
    directory.mkdir(mode=0o755)
    wheel = directory / staging.name
    staging.replace(wheel)
    metadata = directory / f"{wheel.name}.candidate.json"
    write_release_candidate_atomic(metadata, candidate)
    return wheel, metadata


def _application_payload() -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for module in MANAGED_IMPORTS:
        _add_python_module(payload, module, b"VALUE = 'verified-probe'\n")
    source_core = Path(__file__).resolve().parents[1] / "codev_platform" / "core"
    payload.setdefault("codev_platform/__init__.py", b"")
    payload.setdefault("codev_platform/core/__init__.py", b"")
    for name in _CORE_IDENTITY_MODULES:
        payload[f"codev_platform/core/{name}"] = (source_core / name).read_bytes()
    _add_distribution_metadata(payload, "codev-platform", _APP_VERSION)
    return payload


def _add_python_module(payload: dict[str, bytes], dotted: str, source: bytes) -> None:
    parts = dotted.split(".")
    for index in range(1, len(parts)):
        payload.setdefault("/".join(parts[:index]) + "/__init__.py", b"")
    payload["/".join(parts) + ".py"] = source


def _add_distribution_metadata(
    payload: dict[str, bytes],
    package: str,
    version: str,
) -> None:
    normalized = package.replace("-", "_")
    dist_info = f"{normalized}-{version}.dist-info"
    payload[f"{dist_info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n\n".encode("ascii")
    )
    payload[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\n"
        b"Generator: codev-platform-tests\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n\n"
    )
    record = f"{dist_info}/RECORD"
    payload[record] = _record_bytes(payload, record)


def _record_bytes(files: dict[str, bytes], record_name: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name in sorted(files):
        content = files[name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=")
        writer.writerow((name, f"sha256={digest.decode('ascii')}", len(content)))
    writer.writerow((record_name, "", ""))
    return output.getvalue().encode("utf-8")


def _write_wheel(path: Path, payload: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(payload):
            archive.writestr(name, payload[name])
    path.chmod(0o644)


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _dependency_version(package: str) -> str:
    return "1.0+cu128" if package == "torch" else "1.0"


def _remove_workspace(workspace: Path) -> None:
    selected = Path(os.path.abspath(os.fspath(workspace)))
    if (
        selected.parent != Path("/run")
        or not selected.name.startswith(_WORKSPACE_PREFIX)
        or selected.is_symlink()
    ):
        raise VerifiedTargetProbeFixtureError("拒绝清理非夹具工作区")
    shutil.rmtree(selected)


__all__ = [
    "VerifiedTargetProbeFixtureError",
    "VerifiedTargetProbeRuntime",
    "verified_target_probe_runtime",
]
