"""版本化运行时首次切换、进程身份冻结与回滚验收。"""

from __future__ import annotations

import hashlib
import json
import os
import selectors
import subprocess
import sys
import venv
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    current_abi,
    read_base_metadata,
    read_release_metadata,
    sha256_file,
    write_base_metadata_atomic,
    write_release_metadata_atomic,
)
import codev_platform.runtime_base as runtime_base
import codev_platform.runtime_build as runtime_build
import codev_platform.runtime_release as runtime_release

POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="真实 venv 符号链接切换仅在 POSIX 执行")

_BASELINE_RELEASE = "1" * 64
_NEW_RELEASE = "2" * 64
_BASE_ID = "3" * 64
_BASELINE_REVISION = "a" * 40
_NEW_REVISION = "b" * 40
_REQUIREMENTS_SHA = "4" * 64
_MANIFEST_SHA = "5" * 64
_FREEZE_SHA = "6" * 64

_PROBE_PROGRAM = """
import json
import sys
from cutover_probe import RUNTIME_REVISION

identity = {"runtime_revision": RUNTIME_REVISION}
print(json.dumps(identity, sort_keys=True), flush=True)
for _line in sys.stdin:
    print(json.dumps(identity, sort_keys=True), flush=True)
"""


def _runtime_root(path: Path) -> Path:
    root = path / "runtime"
    (root / "bases").mkdir(parents=True)
    (root / "releases").mkdir()
    return root


def _write_base(root: Path) -> BaseMetadata:
    purelib = f"venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    metadata = BaseMetadata(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=_BASE_ID,
        requirements_sha256=_REQUIREMENTS_SHA,
        approved_index_url="https://download.pytorch.org/whl/cu128",
        artifact_manifest_sha256=_MANIFEST_SHA,
        freeze_sha256=_FREEZE_SHA,
        purelib_inventory_sha256="e" * 64,
        abi=current_abi(),
        created_at="2026-07-17T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative=purelib,
        bin_relative="venv/bin",
        lock_relative="requirements.lock",
    )
    directory = root / "bases" / metadata.base_id
    (directory / metadata.purelib_relative).mkdir(parents=True)
    (directory / metadata.bin_relative / "python").write_text("探针基座\n", encoding="utf-8")
    (directory / metadata.lock_relative).write_text("probe==1\n", encoding="utf-8")
    write_base_metadata_atomic(directory / "base.json", metadata)
    (directory / ".release-valid").write_text("ok\n", encoding="ascii")
    return metadata


def _venv_python(directory: Path) -> Path:
    return directory / "bin" / "python"


def _purelib(python: Path) -> Path:
    completed = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    return Path(completed.stdout.strip())


def _write_release(
    root: Path,
    release_id: str,
    revision: str,
    *,
    real_venv: bool = True,
) -> ReleaseMetadata:
    directory = root / "releases" / release_id
    environment = directory / "venv"
    if real_venv:
        venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
        python = _venv_python(environment)
        purelib = _purelib(python)
    else:
        python = _venv_python(environment)
        python.parent.mkdir(parents=True)
        python.write_text("探针解释器占位\n", encoding="utf-8")
        purelib = directory / "venv" / "lib" / "probe" / "site-packages"
        purelib.mkdir(parents=True)

    (purelib / "cutover_probe.py").write_text(
        f'RUNTIME_REVISION = "{revision}"\n',
        encoding="utf-8",
    )
    maintenance = purelib / "codev_platform" / "ops" / "memory_maintenance.py"
    maintenance.parent.mkdir(parents=True)
    (maintenance.parents[1] / "__init__.py").write_text("", encoding="utf-8")
    (maintenance.parent / "__init__.py").write_text("", encoding="utf-8")
    maintenance.write_text("ENTRYPOINT_READY = True\n", encoding="utf-8")

    base = root / "bases" / _BASE_ID
    (directory / "base").symlink_to(base, target_is_directory=True)
    pth = purelib / "codev_platform_base.pth"
    pth.write_text(
        str((base / read_base_metadata(base / "base.json").purelib_relative).resolve()) + "\n",
        encoding="utf-8",
    )
    artifact = directory / "artifacts" / "probe-1-py3-none-any.whl"
    artifact.parent.mkdir()
    artifact.write_bytes(f"probe:{revision}".encode("ascii"))
    metadata = ReleaseMetadata(
        schema_version=1,
        release_id=release_id,
        runtime_revision=revision,
        wheel_sha256=sha256_file(artifact),
        base_id=_BASE_ID,
        base_requirements_sha256=_REQUIREMENTS_SHA,
        base_metadata_sha256=sha256_file(base / "base.json"),
        app_freeze_sha256=hashlib.sha256(revision.encode("ascii")).hexdigest(),
        created_at="2026-07-17T00:00:00Z",
        python_relative=python.relative_to(directory).as_posix(),
        purelib_relative=purelib.relative_to(directory).as_posix(),
        base_link_relative="base",
        base_pth_relative=pth.relative_to(directory).as_posix(),
    )
    write_release_metadata_atomic(directory / "release.json", metadata)
    (directory / ".release-valid").write_text("ok\n", encoding="ascii")
    (directory / ".service-ready").write_text("ok\n", encoding="ascii")
    return metadata


def _install_public_verifiers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []

    def verify_base(root: Path, base_id: str) -> BaseMetadata:
        metadata = read_base_metadata(root / "bases" / base_id / "base.json")
        if metadata.base_id != base_id or not (root / "bases" / base_id / ".release-valid").is_file():
            raise runtime_base.RuntimeBaseError("测试基座无效")
        events.append(("base", base_id))
        return metadata

    def read_base_id(root: Path, release_id: str) -> str:
        return read_release_metadata(root / "releases" / release_id / "release.json").base_id

    def verify_release(
        root: Path,
        release_id: str,
        *,
        verified_base: BaseMetadata | None = None,
    ) -> ReleaseMetadata:
        directory = root / "releases" / release_id
        metadata = read_release_metadata(directory / "release.json")
        if metadata.release_id != release_id or not (directory / ".release-valid").is_file():
            raise runtime_build.RuntimeBuildError("测试版本无效")
        base = verified_base or runtime_base.verify_base_locked(root, metadata.base_id)
        if metadata.base_requirements_sha256 != base.requirements_sha256:
            raise runtime_build.RuntimeBuildError("测试版本与基座不一致")
        events.append(("release", release_id))
        return metadata

    monkeypatch.setattr(runtime_base, "verify_base_locked", verify_base)
    monkeypatch.setattr(runtime_build, "read_release_base_id_locked", read_base_id)
    monkeypatch.setattr(runtime_build, "verify_release_locked", verify_release)
    monkeypatch.setattr(runtime_build, "verify_release", verify_release)
    return events


def _link_release_id(root: Path, name: str, *, required: bool = True) -> str | None:
    link = root / name
    if not link.is_symlink():
        if required:
            raise AssertionError(f"{name} 运行时引用不存在")
        return None
    raw = os.readlink(link)
    prefix = "releases/"
    if not raw.startswith(prefix):
        raise AssertionError(f"{name} 运行时引用越界")
    return raw.removeprefix(prefix)


def _probe_maintenance_entrypoint(root: Path, release: ReleaseMetadata) -> None:
    python = root / "releases" / release.release_id / release.python_relative
    completed = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            (
                "from codev_platform.ops import memory_maintenance as m; "
                "assert m.ENTRYPOINT_READY; print('ok')"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert completed.stdout.strip() == "ok"


def _require_verified_production_pair(root: Path, expected_current: str) -> tuple[str, str]:
    current = _link_release_id(root, "current")
    previous = _link_release_id(root, "previous", required=False)
    if previous is None:
        raise AssertionError("首次生产切换前 previous 不得为空")
    if current != expected_current or previous == current:
        raise AssertionError("首次生产切换必须保留不同的基线版本")
    current_release = runtime_build.verify_release(root, current)
    previous_release = runtime_build.verify_release(root, previous)
    assert current_release.base_id == previous_release.base_id == _BASE_ID
    _probe_maintenance_entrypoint(root, previous_release)
    return current, previous


def _require_service_ready(root: Path) -> None:
    current = _link_release_id(root, "current")
    if current is None or not (root / "releases" / current / ".service-ready").is_file():
        raise AssertionError("当前版本 readiness 未通过")


def _read_probe(process: subprocess.Popen[str]) -> dict[str, str]:
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout=10):
            raise AssertionError("运行身份探针响应超时")
    line = process.stdout.readline()
    if not line:
        raise AssertionError("运行身份探针提前退出")
    payload = json.loads(line)
    assert isinstance(payload, dict)
    return payload


@contextmanager
def _running_probe(python: Path) -> Iterator[subprocess.Popen[str]]:
    process = subprocess.Popen(
        [str(python), "-I", "-u", "-c", _PROBE_PROGRAM],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    try:
        yield process
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _query_probe(process: subprocess.Popen[str]) -> dict[str, str]:
    assert process.stdin is not None
    process.stdin.write("状态\n")
    process.stdin.flush()
    return _read_probe(process)


@POSIX_ONLY
def test_cutover_keeps_old_process_identity_and_restart_uses_new_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    _write_base(root)
    baseline = _write_release(root, _BASELINE_RELEASE, _BASELINE_REVISION)
    new = _write_release(root, _NEW_RELEASE, _NEW_REVISION)
    events = _install_public_verifiers(monkeypatch)

    runtime_release.activate_release(root, baseline.release_id)
    baseline_python = root / "current" / baseline.python_relative
    with _running_probe(baseline_python) as old_process:
        assert _read_probe(old_process)["runtime_revision"] == _BASELINE_REVISION
        runtime_release.activate_release(root, new.release_id)
        assert _query_probe(old_process)["runtime_revision"] == _BASELINE_REVISION

    current, previous = _require_verified_production_pair(root, new.release_id)
    assert (current, previous) == (new.release_id, baseline.release_id)
    with _running_probe(root / "current" / new.python_relative) as new_process:
        assert _read_probe(new_process)["runtime_revision"] == _NEW_REVISION
    assert ("release", baseline.release_id) in events
    assert ("release", new.release_id) in events
    assert ("base", _BASE_ID) in events


@POSIX_ONLY
def test_direct_new_activation_is_not_production_ready_and_readiness_failure_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct = _runtime_root(tmp_path / "direct")
    _write_base(direct)
    _write_release(direct, _NEW_RELEASE, _NEW_REVISION, real_venv=False)
    _install_public_verifiers(monkeypatch)
    runtime_release.activate_release(direct, _NEW_RELEASE)
    with pytest.raises(AssertionError, match="previous"):
        _require_verified_production_pair(direct, _NEW_RELEASE)

    root = _runtime_root(tmp_path / "rollback")
    _write_base(root)
    baseline = _write_release(root, _BASELINE_RELEASE, _BASELINE_REVISION)
    new = _write_release(root, _NEW_RELEASE, _NEW_REVISION)
    runtime_release.activate_release(root, baseline.release_id)
    runtime_release.activate_release(root, new.release_id)
    assert _require_verified_production_pair(root, new.release_id) == (
        new.release_id,
        baseline.release_id,
    )

    (root / "releases" / new.release_id / ".service-ready").unlink()
    with pytest.raises(AssertionError, match="readiness"):
        _require_service_ready(root)

    result = runtime_release.rollback_release(root)
    assert result.active_release == baseline.release_id
    _require_service_ready(root)
    with _running_probe(root / "current" / baseline.python_relative) as process:
        assert _read_probe(process)["runtime_revision"] == _BASELINE_REVISION
