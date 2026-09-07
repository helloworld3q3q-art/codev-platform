"""目标用户探针的 Linux root 实物运行时构造辅助。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    compute_base_id,
    compute_release_id,
    current_abi,
    sha256_file,
    write_base_metadata_atomic,
    write_release_metadata_atomic,
)
from codev_platform.runtime_release_environment import MANAGED_IMPORTS
from codev_platform.runtime_service_process import ServiceAccount, resolve_service_account


@dataclass(frozen=True, slots=True)
class TargetProbeRuntime:
    """真实薄 release 探针所需的冻结对象。"""

    root: Path
    base: BaseMetadata
    release: ReleaseMetadata
    account: ServiceAccount
    app_purelib: Path
    base_purelib: Path


def build_target_probe_runtime(
    tmp_path: Path,
    *,
    module_sources: Mapping[str, str] | None = None,
    managed_from_base: str | None = None,
    preload_managed: str | None = None,
    torch_from_app: bool = False,
) -> TargetProbeRuntime:
    """构造可由 nobody 读取、但不可写的最小 base/release。"""
    _open_parent_chain(tmp_path)
    root = (tmp_path / "runtime").resolve()
    requirements = b"demo==1.0\n"
    requirements_sha = hashlib.sha256(requirements).hexdigest()
    abi = current_abi()
    base_id = compute_base_id(requirements_sha, abi)
    base_root = root / "bases" / base_id
    base_purelib = base_root / "venv/lib/python/site-packages"
    base_purelib.mkdir(parents=True)
    (base_root / "requirements.lock").write_bytes(requirements)
    _write_torch(base_purelib, "base")
    base = BaseMetadata(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=base_id,
        requirements_sha256=requirements_sha,
        approved_index_url="https://download.pytorch.org/whl/cu128",
        artifact_manifest_sha256="1" * 64,
        freeze_sha256="2" * 64,
        purelib_inventory_sha256="3" * 64,
        abi=abi,
        created_at="2026-07-18T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        bin_relative="venv/bin",
        lock_relative="requirements.lock",
    )
    write_base_metadata_atomic(base_root / "base.json", base)
    release = _create_release(root, base, sha256_file(base_root / "base.json"))
    release_root = root / "releases" / release.release_id
    app_purelib = release_root / release.purelib_relative
    _install_runtime_identity(app_purelib)
    selected_sources = dict(module_sources or {})
    for module in MANAGED_IMPORTS:
        destination = base_purelib if module == managed_from_base else app_purelib
        _write_stub_module(
            destination,
            module,
            selected_sources.get(module, "VALUE = 'ok'\n"),
        )
    if managed_from_base is not None:
        _enable_namespace_package(app_purelib / "codev_platform" / "__init__.py")
    if torch_from_app:
        _write_torch(app_purelib, "app")
    preload = "" if preload_managed is None else f"import {preload_managed}\n"
    (app_purelib / "codev_platform_base.pth").write_text(
        preload + base_purelib.as_posix() + "\n",
        encoding="utf-8",
    )
    (release_root / "base").symlink_to(
        Path("..") / ".." / "bases" / base.base_id,
        target_is_directory=True,
    )
    write_release_metadata_atomic(release_root / "release.json", release)
    _publish_read_only(root)
    return TargetProbeRuntime(
        root=root,
        base=base,
        release=release,
        account=resolve_service_account("nobody"),
        app_purelib=app_purelib,
        base_purelib=base_purelib,
    )


def _create_release(
    root: Path,
    base: BaseMetadata,
    base_metadata_sha: str,
) -> ReleaseMetadata:
    revision = "4" * 40
    wheel_sha = "5" * 64
    release_id = compute_release_id(revision, wheel_sha, base.base_id, base_metadata_sha)
    release_root = root / "releases" / release_id
    release_root.mkdir(parents=True)
    subprocess.run(
        [sys.executable, "-I", "-B", "-m", "venv", "--without-pip", release_root / "venv"],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=30,
    )
    python = release_root / "venv/bin/python"
    purelib_text = subprocess.run(
        [python, "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    app_purelib = Path(purelib_text).resolve(strict=True)
    return ReleaseMetadata(
        schema_version=1,
        release_id=release_id,
        runtime_revision=revision,
        wheel_sha256=wheel_sha,
        base_id=base.base_id,
        base_requirements_sha256=base.requirements_sha256,
        base_metadata_sha256=base_metadata_sha,
        app_freeze_sha256="6" * 64,
        created_at="2026-07-18T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative=app_purelib.relative_to(release_root).as_posix(),
        base_link_relative="base",
        base_pth_relative=(app_purelib / "codev_platform_base.pth")
        .relative_to(release_root)
        .as_posix(),
    )


def _install_runtime_identity(app_purelib: Path) -> None:
    source_core = Path(__file__).parent.parent / "codev_platform" / "core"
    target_core = app_purelib / "codev_platform" / "core"
    target_core.mkdir(parents=True)
    (app_purelib / "codev_platform" / "__init__.py").write_text("", encoding="utf-8")
    (target_core / "__init__.py").write_text("", encoding="utf-8")
    for name in (
        "runtime_identity.py",
        "runtime_metadata_io.py",
        "runtime_models.py",
        "runtime_release_identity.py",
    ):
        shutil.copyfile(source_core / name, target_core / name)


def _write_stub_module(purelib: Path, dotted_name: str, source: str) -> None:
    parts = dotted_name.split(".")
    package = purelib
    for component in parts[:-1]:
        package /= component
        package.mkdir(exist_ok=True)
        init = package / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
    (package / f"{parts[-1]}.py").write_text(source, encoding="utf-8")


def _enable_namespace_package(init: Path) -> None:
    init.write_text(
        "from pkgutil import extend_path\n__path__ = extend_path(__path__, __name__)\n",
        encoding="utf-8",
    )


def _write_torch(purelib: Path, source: str) -> None:
    package = purelib / "torch"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text(f"SOURCE = {source!r}\n", encoding="utf-8")


def _open_parent_chain(tmp_path: Path) -> None:
    for directory in (tmp_path, *tmp_path.parents):
        if directory == Path("/tmp"):
            break
        directory.chmod(0o755)


def _publish_read_only(root: Path) -> None:
    for directory, names, files in os.walk(root):
        Path(directory).chmod(0o755)
        for name in names:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(0o755)
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(0o644)


__all__ = ["TargetProbeRuntime", "build_target_probe_runtime"]
