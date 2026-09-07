"""薄 release root-fd worker 的独立边界回归。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform.runtime_bound_worker_release as release_worker
from codev_platform import runtime_build
from codev_platform.core.runtime_models import ReleaseMetadata
from codev_platform.runtime_errors import RuntimeBuildError
from codev_platform.runtime_object_access import RuntimeObjectAccessError
from codev_platform.runtime_root_binding import RuntimeRootBinding, RuntimeRootBindingError


_POSIX = os.name == "posix"


def testrelease_worker将对象访问失败转换为隔离可识别的领域错误(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已完成 release 访问漂移必须进入既有 corrupt 隔离分支。"""

    def reject(_root: Path) -> None:
        raise RuntimeObjectAccessError("模拟访问模式漂移")

    monkeypatch.setattr(release_worker, "verify_object_access_from_cwd", reject)

    with pytest.raises(RuntimeBuildError, match="薄 release 对象访问模式无法复验"):
        release_worker._verify_release_object_access(Path("releases/demo"))


def testrelease预计算在cwd中复验基座而不调用绝对根检查(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已有 binding 的 worker 不得回退到 `runtime_build._require_runtime_root`。"""
    base_id = "a" * 64
    candidate = SimpleNamespace(
        runtime_revision="b" * 40,
        wheel_sha256="c" * 64,
    )
    bound_root = object()
    monkeypatch.setattr(
        runtime_build,
        "_read_candidate_bundle",
        lambda *_args: (candidate, Path("candidate.whl"), b"wheel"),
    )
    monkeypatch.setattr(
        runtime_build,
        "_require_runtime_root",
        lambda *_args: pytest.fail("worker 不得重新绝对化 runtime 根"),
    )
    monkeypatch.setattr(release_worker, "sha256_file", lambda _path: "d" * 64)
    monkeypatch.setattr(
        release_worker,
        "verify_base_locked_from_cwd",
        lambda object_id, binding: (
            SimpleNamespace(base_id=base_id)
            if (object_id, binding) == (base_id, bound_root)
            else pytest.fail("cwd 基座复验参数错误")
        ),
        raising=False,
    )

    result = release_worker._prepared_value(
        Path("."),
        Path("candidate.whl"),
        Path("candidate.json"),
        base_id,
        bound_root=bound_root,
    )

    assert result["base_metadata_sha256"] == "d" * 64


@pytest.mark.skipif(not _POSIX or os.geteuid() != 0, reason="仅验证 POSIX root-fd 原子发布")
def testrelease最终pth经同一绑定原子切换(
    tmp_path: Path,
) -> None:
    """动态 `.pth` 的最终引用必须仍由持有的 root binding 发布。"""
    root = tmp_path / "runtime"
    relative = (
        Path("releases")
        / ("a" * 64)
        / "venv"
        / "lib"
        / "python3.12"
        / "site-packages"
        / "codev_platform_base.pth"
    )
    pth = root / relative
    (root / "bases").mkdir(parents=True, mode=0o755)
    pth.parent.mkdir(parents=True, mode=0o755)
    pth.write_bytes(b"/proc/self/cwd/bases/old\n")
    os.chmod(pth, 0o640)

    with RuntimeRootBinding(root, 0).bind() as bound_root:
        release_worker._finalize_base_pth(
            bound_root,
            relative,
            b"/srv/codev-platform/runtime/bases/new\n",
        )

    assert pth.read_bytes() == b"/srv/codev-platform/runtime/bases/new\n"
    assert pth.stat().st_mode & 0o777 == 0o640


@pytest.mark.skipif(not _POSIX or os.geteuid() != 0, reason="仅验证 POSIX root-fd worker")
def testrelease构建worker向构建内核传递绑定的绝对参考根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 不得把 `Path('.')` 当作 `.pth` 的绝对发布引用。"""
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "bases").mkdir()
    (root / "releases").mkdir()
    base_id = "a" * 64
    base_metadata_sha = "b" * 64
    release_id = "c" * 64
    metadata = ReleaseMetadata(
        schema_version=1,
        release_id=release_id,
        runtime_revision="d" * 40,
        wheel_sha256="e" * 64,
        base_id=base_id,
        base_requirements_sha256="f" * 64,
        base_metadata_sha256=base_metadata_sha,
        app_freeze_sha256="1" * 64,
        created_at="2026-07-22T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python3.12/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python3.12/site-packages/codev_platform_base.pth",
    )
    observed: list[tuple[Path, Path | None]] = []

    monkeypatch.setattr(
        release_worker,
        "_prepared_value",
        lambda *_args: {
            "base_metadata_sha256": base_metadata_sha,
            "release_id": release_id,
        },
    )
    monkeypatch.setattr(
        runtime_build,
        "_read_candidate_bundle",
        lambda *_args: (object(), Path("candidate.whl"), b"wheel"),
    )
    observed_base: list[tuple[str, Path]] = []

    def verify_base(
        object_id: str,
        bound_root: object,
    ) -> object:
        observed_base.append((object_id, bound_root.path))
        return object()

    monkeypatch.setattr(release_worker, "verify_base_locked_from_cwd", verify_base)

    def stage(root_path: Path, *_args: object, **kwargs: object) -> ReleaseMetadata:
        observed.append((root_path, kwargs.get("runtime_root_reference")))
        return metadata

    monkeypatch.setattr(runtime_build, "_stage_release_locked", stage)
    payload = {
        "base_id": base_id,
        "base_metadata_sha256": base_metadata_sha,
        "candidate_file": str(tmp_path / "candidate.json"),
        "release_id": release_id,
        "runtime_root": str(root),
        "wheel": str(tmp_path / "candidate.whl"),
    }
    cwd_descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(root_descriptor)
        result = release_worker.stage_release(payload, root_descriptor)
    finally:
        os.fchdir(cwd_descriptor)
        os.close(root_descriptor)
        os.close(cwd_descriptor)

    assert result["release_id"] == release_id
    assert observed == [(Path("."), root)]
    assert observed_base == [(base_id, root)]


@pytest.mark.skipif(not _POSIX or os.geteuid() != 0, reason="仅验证 POSIX root-fd worker")
def testrelease复验worker向静态复验传递绑定的绝对参考根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """静态复验也必须以 reference 与相对 I/O 路径分离。"""
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "bases").mkdir()
    (root / "releases").mkdir()
    base_id = "a" * 64
    release_id = "c" * 64
    metadata = ReleaseMetadata(
        schema_version=1,
        release_id=release_id,
        runtime_revision="d" * 40,
        wheel_sha256="e" * 64,
        base_id=base_id,
        base_requirements_sha256="f" * 64,
        base_metadata_sha256="b" * 64,
        app_freeze_sha256="1" * 64,
        created_at="2026-07-22T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python3.12/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python3.12/site-packages/codev_platform_base.pth",
    )
    observed: list[tuple[Path, Path | None]] = []
    observed_base: list[tuple[str, Path]] = []

    def verify_base(
        object_id: str,
        bound_root: object,
    ) -> object:
        observed_base.append((object_id, bound_root.path))
        return object()

    monkeypatch.setattr(release_worker, "verify_base_locked_from_cwd", verify_base)

    def verify(root_path: Path, _release_id: str, **kwargs: object) -> ReleaseMetadata:
        observed.append((root_path, kwargs.get("runtime_root_reference")))
        return metadata

    monkeypatch.setattr(runtime_build, "_verify_release_directory", verify)
    payload = {
        "base_id": base_id,
        "release_id": release_id,
        "runtime_root": str(root),
    }
    cwd_descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(root_descriptor)
        result = release_worker.verify_release(payload, root_descriptor)
    finally:
        os.fchdir(cwd_descriptor)
        os.close(root_descriptor)
        os.close(cwd_descriptor)

    assert result["release_id"] == release_id
    assert observed == [(Path("."), root)]
    assert observed_base == [(base_id, root)]


@pytest.mark.skipif(not _POSIX or os.geteuid() != 0, reason="仅验证 POSIX root-fd worker")
def testrelease预计算worker不重新解析命名根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预计算阶段必须继续使用 fchdir 固定的旧根，而非重新查询 cwd 名称。"""
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "bases").mkdir()
    (root / "releases").mkdir()
    previous = tmp_path / "runtime-previous"
    original_bind = RuntimeRootBinding.bind_inherited_descriptor

    @contextmanager
    def bind_then_replace(
        binding: RuntimeRootBinding,
        descriptor: int,
    ):
        with original_bind(binding, descriptor) as bound_root:
            root.rename(previous)
            root.mkdir()
            yield bound_root

    def prepare_in_old_root(
        worker_root: Path,
        _wheel: Path,
        _candidate_file: Path,
        _base_id: str,
        _bound_root: object,
    ) -> dict[str, str]:
        (worker_root / "prepare-probe").write_text("old-root", encoding="utf-8")
        return {
            "base_metadata_sha256": "b" * 64,
            "release_id": "c" * 64,
        }

    monkeypatch.setattr(RuntimeRootBinding, "bind_inherited_descriptor", bind_then_replace)
    monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: root))
    monkeypatch.setattr(release_worker, "_prepared_value", prepare_in_old_root)
    payload = {
        "base_id": "a" * 64,
        "candidate_file": str(tmp_path / "candidate.json"),
        "runtime_root": str(root),
        "wheel": str(tmp_path / "candidate.whl"),
    }
    cwd_descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(root_descriptor)
        with pytest.raises(RuntimeRootBindingError, match="运行时根"):
            release_worker.prepare_release(payload, root_descriptor)
    finally:
        os.fchdir(cwd_descriptor)
        os.close(root_descriptor)
        os.close(cwd_descriptor)

    assert not (root / "prepare-probe").exists()
    assert (previous / "prepare-probe").read_text(encoding="utf-8") == "old-root"
