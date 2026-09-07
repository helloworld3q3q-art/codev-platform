"""release root-fd worker 在执行期根替换后的相对路径边界回归。"""

from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform import runtime_build
from codev_platform import runtime_bound_worker_release
from codev_platform import runtime_release_environment
from codev_platform.core.runtime_models import (
    ReleaseCandidate,
    ReleaseMetadata,
    compute_release_id,
    sha256_file,
)
from codev_platform.runtime_bound_worker_release import (
    stage_release,
    verify_release as verify_release_worker,
)
from codev_platform.runtime_errors import RuntimeBuildError
from codev_platform.runtime_root_binding import RuntimeRootBindingError
from tests.test_runtime_build import _WHEEL_BYTES, _base, _candidate_bundle


_POSIX = os.name == "posix"


def testrelease环境不暴露动态pth引用覆盖参数() -> None:
    """root-fd 动态导入引用只能由环境层内部派生。"""
    parameters = inspect.signature(runtime_release_environment.stage_environment).parameters

    assert "staging_base_purelib_reference" not in parameters


@pytest.mark.parametrize(
    ("final_reference", "checker", "finalizer", "message"),
    (
        (Path("/srv/runtime/bases/demo"), None, lambda _path, _content: None, "绑定复验"),
        (Path("/srv/runtime/bases/demo"), lambda: None, None, "受控最终发布器"),
        (
            Path("/proc/self/cwd/bases/demo"),
            lambda: None,
            lambda _path, _content: None,
            "激活引用",
        ),
        (
            Path("/proc/self/fd/9/bases/demo"),
            lambda: None,
            lambda _path, _content: None,
            "激活引用",
        ),
    ),
)
def test动态pth门禁拒绝不受控配置(
    final_reference: Path,
    checker: object,
    finalizer: object,
    message: str,
) -> None:
    """动态阶段必须有绑定检查、原子发布器和命名激活引用。"""
    with pytest.raises(RuntimeBuildError, match=message):
        runtime_release_environment._require_dynamic_pth_guards(
            final_reference,
            checker,
            finalizer,
        )


def test动态pth门禁接受命名激活引用() -> None:
    """受控 checker 与 finalizer 配合命名 runtime 根可以进入动态阶段。"""
    runtime_release_environment._require_dynamic_pth_guards(
        Path("/srv/runtime/bases/demo"),
        lambda: None,
        lambda _path, _content: None,
    )


def test相对rootfd缺少命名基座引用时不启动子进程(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """动态模式不得从相对 I/O 基座路径猜测最终激活引用。"""
    release_dir = Path("releases") / ("a" * 64)
    wheel = Path("application.whl")
    base_dir = Path("bases") / ("b" * 64)
    marker = Path("marker")
    monkeypatch.setattr(runtime_release_environment.os, "name", "posix")
    monkeypatch.setattr(
        runtime_release_environment,
        "_inspect_application_wheel",
        lambda _wheel: pytest.fail("缺少命名引用前不得启动子进程"),
    )

    with pytest.raises(RuntimeBuildError, match="命名基座发布引用"):
        runtime_release_environment.stage_environment(
            release_dir,
            wheel,
            base_dir,
            SimpleNamespace(base_id="b" * 64, purelib_relative="venv/purelib"),
            marker,
        )


@pytest.mark.skipif(not _POSIX, reason="仅验证 POSIX root-fd 动态 pth")
def testrelease环境在相对运行时根内构建并单独序列化基座引用(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 的根内 I/O 不得因 `.pth` 的绝对引用而变成命名根 I/O。"""
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    base_purelib = root / "bases" / base.base_id / base.purelib_relative
    base_purelib.mkdir(parents=True)
    release_id = "a" * 64
    release_dir = Path("releases") / release_id
    venv = release_dir / "venv"
    purelib = venv / "lib" / "python3.12" / "site-packages"
    base_dir = Path("bases") / base.base_id
    marker = release_dir / ".incomplete"
    final_reference = base_purelib
    staging_reference = Path("/proc/self/cwd") / "bases" / base.base_id / base.purelib_relative
    pth = purelib / "codev_platform_base.pth"
    observed: list[tuple[Path, Path, Path]] = []
    commands: list[tuple[str, ...]] = []
    binding_checks: list[None] = []

    def run(command: tuple[str, ...], **_kwargs: object) -> str:
        commands.append(command)
        if "venv" in command:
            python = venv / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            purelib.mkdir(parents=True)
            return ""
        if any("sysconfig" in part for part in command):
            return "lib/python3.12/site-packages\n"
        if "pip" in command:
            assert pth.read_text(encoding="utf-8") == f"{staging_reference.as_posix()}\n"
        return ""

    def verify_installed(_proof: object, app_purelib: Path, pth: Path) -> None:
        assert app_purelib == purelib
        assert pth == purelib / "codev_platform_base.pth"

    def deep_probe(python: Path, app_purelib: Path, dependency_purelib: Path) -> str:
        observed.append((python, app_purelib, dependency_purelib))
        assert pth.read_text(encoding="utf-8") == f"{staging_reference.as_posix()}\n"
        return "freeze"

    monkeypatch.setattr(runtime_release_environment, "_run_checked", run)
    monkeypatch.setattr(
        runtime_release_environment, "_inspect_application_wheel", lambda _wheel: object()
    )
    monkeypatch.setattr(
        runtime_release_environment, "_verify_installed_application", verify_installed
    )
    monkeypatch.setattr(runtime_release_environment, "_run_deep_probes", deep_probe)
    monkeypatch.setattr(runtime_release_environment, "_verify_execution_trust", lambda *_args: None)
    monkeypatch.chdir(root)

    staged = runtime_release_environment.stage_environment(
        release_dir,
        release_dir / "artifacts" / "application.whl",
        base_dir,
        base,
        marker,
        base_purelib_reference=final_reference,
        verify_runtime_binding=lambda: binding_checks.append(None),
        finalize_base_pth=lambda path, content: path.write_bytes(content),
    )

    assert observed == [(venv / "bin" / "python", purelib, base_dir / base.purelib_relative)]
    assert staged.python_relative == "venv/bin/python"
    assert staged.purelib_relative == "venv/lib/python3.12/site-packages"
    assert any("relative_to(sys.prefix)" in part for command in commands for part in command)
    assert (purelib / "codev_platform_base.pth").read_text(encoding="utf-8") == (
        f"{final_reference.as_posix()}\n"
    )
    assert binding_checks
    assert os.readlink(release_dir / "base") == os.fspath(
        Path("..") / ".." / "bases" / base.base_id
    )


@pytest.mark.skipif(not _POSIX, reason="仅验证 POSIX root-fd 动态 pth")
def testrelease环境在绑定漂移前不执行动态命令(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """动态 pth 已写入后必须先确认 binding，避免替换根引入错误 base。"""
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    (root / "bases" / base.base_id / base.purelib_relative).mkdir(parents=True)
    release_dir = Path("releases") / ("a" * 64)
    venv = release_dir / "venv"
    purelib = venv / "lib" / "python3.12" / "site-packages"
    marker = release_dir / ".incomplete"
    commands: list[tuple[str, ...]] = []
    deep_probe_called = False

    def run(command: tuple[str, ...], **_kwargs: object) -> str:
        commands.append(command)
        if "venv" in command:
            python = venv / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            purelib.mkdir(parents=True)
            return ""
        if any("sysconfig" in part for part in command):
            return "lib/python3.12/site-packages\n"
        return ""

    def deep_probe(*_args: object) -> str:
        nonlocal deep_probe_called
        deep_probe_called = True
        return "freeze"

    monkeypatch.setattr(runtime_release_environment, "_run_checked", run)
    monkeypatch.setattr(
        runtime_release_environment, "_inspect_application_wheel", lambda _wheel: object()
    )
    monkeypatch.setattr(
        runtime_release_environment, "_verify_installed_application", lambda *_args: None
    )
    monkeypatch.setattr(runtime_release_environment, "_run_deep_probes", deep_probe)
    monkeypatch.setattr(runtime_release_environment, "_verify_execution_trust", lambda *_args: None)
    monkeypatch.chdir(root)

    with pytest.raises(RuntimeRootBindingError, match="运行时根"):
        runtime_release_environment.stage_environment(
            release_dir,
            release_dir / "artifacts" / "application.whl",
            Path("bases") / base.base_id,
            base,
            marker,
            base_purelib_reference=root / "bases" / base.base_id / base.purelib_relative,
            verify_runtime_binding=lambda: (_ for _ in ()).throw(
                RuntimeRootBindingError("运行时根目录身份已漂移")
            ),
            finalize_base_pth=lambda path, content: path.write_bytes(content),
        )

    assert not any("pip" in command for command in commands)
    assert not deep_probe_called


@pytest.mark.skipif(not _POSIX, reason="仅验证 POSIX root-fd 动态 pth")
def testrelease环境在最终pth切换前检测绑定漂移(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """深探针后根漂移不得让最终命名引用覆盖稳定动态 `.pth`。"""
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    (root / "bases" / base.base_id / base.purelib_relative).mkdir(parents=True)
    release_dir = Path("releases") / ("a" * 64)
    venv = release_dir / "venv"
    purelib = venv / "lib" / "python3.12" / "site-packages"
    marker = release_dir / ".incomplete"
    dynamic_reference = Path("/proc/self/cwd") / "bases" / base.base_id / base.purelib_relative
    final_reference = root / "final-base" / base.purelib_relative
    finalized: list[tuple[Path, bytes]] = []
    checks = 0

    def run(command: tuple[str, ...], **_kwargs: object) -> str:
        if "venv" in command:
            python = venv / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            purelib.mkdir(parents=True)
            return ""
        if any("sysconfig" in part for part in command):
            return "lib/python3.12/site-packages\n"
        return ""

    def verify_binding() -> None:
        nonlocal checks
        checks += 1
        if checks == 4:
            raise RuntimeRootBindingError("运行时根目录身份已漂移")

    monkeypatch.setattr(runtime_release_environment, "_run_checked", run)
    monkeypatch.setattr(
        runtime_release_environment, "_inspect_application_wheel", lambda _wheel: object()
    )
    monkeypatch.setattr(
        runtime_release_environment, "_verify_installed_application", lambda *_args: None
    )
    monkeypatch.setattr(runtime_release_environment, "_run_deep_probes", lambda *_args: "freeze")
    monkeypatch.setattr(runtime_release_environment, "_verify_execution_trust", lambda *_args: None)
    monkeypatch.chdir(root)

    with pytest.raises(RuntimeRootBindingError, match="运行时根"):
        runtime_release_environment.stage_environment(
            release_dir,
            release_dir / "artifacts" / "application.whl",
            Path("bases") / base.base_id,
            base,
            marker,
            base_purelib_reference=final_reference,
            verify_runtime_binding=verify_binding,
            finalize_base_pth=lambda path, content: finalized.append((path, content)),
        )

    assert finalized == []
    assert (purelib / "codev_platform_base.pth").read_text(encoding="utf-8") == (
        f"{dynamic_reference.as_posix()}\n"
    )


def testrelease构建在相对根内向环境层传递绝对基座引用(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """构建层只传 reference，环境层仍只得到相对受管 I/O 路径。"""
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="b" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    base_metadata_sha = sha256_file(root / "bases" / base.base_id / "base.json")
    release_id = compute_release_id(
        candidate.runtime_revision,
        candidate.wheel_sha256,
        base.base_id,
        base_metadata_sha,
    )
    observed: list[tuple[Path, Path, Path | None]] = []

    def stage(
        release_dir: Path,
        _wheel: Path,
        base_dir: Path,
        _base: object,
        _marker: Path,
        *,
        base_purelib_reference: Path | None = None,
        **_kwargs: object,
    ) -> runtime_release_environment.StagedEnvironment:
        observed.append((release_dir, base_dir, base_purelib_reference))
        return runtime_release_environment.StagedEnvironment(
            python_relative="venv/bin/python",
            purelib_relative="venv/lib/python3.12/site-packages",
            base_link_relative="base",
            base_pth_relative="venv/lib/python3.12/site-packages/codev_platform_base.pth",
            app_freeze_sha256="4" * 64,
        )

    def verify(root_path: Path, object_id: str, **_kwargs: object) -> ReleaseMetadata:
        return runtime_build.read_release_metadata(
            root_path / "releases" / object_id / "release.json"
        )

    monkeypatch.setattr(runtime_build, "_stage_environment", stage)
    monkeypatch.setattr(runtime_build, "_verify_release_directory", verify)
    monkeypatch.setattr(runtime_build, "_seal_durable_tree", lambda _path: None)
    monkeypatch.chdir(root)

    built = runtime_build._stage_release_locked(
        Path("."),
        object(),
        candidate,
        Path("candidate.whl"),
        _WHEEL_BYTES,
        base,
        base_metadata_sha,
        release_id,
        runtime_root_reference=root,
        seal_object_access=lambda _path: None,
        verify_object_access=lambda _path: None,
    )

    assert built.release_id == release_id
    assert observed == [
        (
            Path("releases") / release_id,
            Path("bases") / base.base_id,
            root / "bases" / base.base_id / base.purelib_relative,
        )
    ]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("base_id", "not-a-sha256", "基座 ID"),
        ("base_metadata_sha256", "not-a-sha256", "基座元数据摘要"),
        ("release_id", "not-a-sha256", "release ID"),
    ),
)
def testreleaseworker拒绝非摘要身份字段(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    """worker 在触碰根 descriptor 前拒绝所有非 SHA-256 身份字段。"""
    root = tmp_path / "runtime"
    payload: dict[str, object] = {
        "base_id": "a" * 64,
        "base_metadata_sha256": "b" * 64,
        "candidate_file": str(tmp_path / "candidate.json"),
        "release_id": "c" * 64,
        "runtime_root": str(root),
        "wheel": str(tmp_path / "candidate.whl"),
    }
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        stage_release(payload, -1)


@pytest.mark.skipif(not _POSIX, reason="仅验证 POSIX root-fd worker")
def testreleaseworker在进入后根替换时不写入替换命名根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="a" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_file = _candidate_bundle(tmp_path, candidate, _WHEEL_BYTES)
    base_metadata_sha = sha256_file(root / "bases" / base.base_id / "base.json")
    release_id = compute_release_id(
        candidate.runtime_revision,
        candidate.wheel_sha256,
        base.base_id,
        base_metadata_sha,
    )
    metadata = ReleaseMetadata(
        schema_version=1,
        release_id=release_id,
        runtime_revision=candidate.runtime_revision,
        wheel_sha256=candidate.wheel_sha256,
        base_id=base.base_id,
        base_requirements_sha256=base.requirements_sha256,
        base_metadata_sha256=base_metadata_sha,
        app_freeze_sha256="4" * 64,
        created_at="2026-07-21T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python/site-packages/codev_platform_base.pth",
    )
    previous = tmp_path / "runtime-previous"

    monkeypatch.setattr(
        runtime_bound_worker_release,
        "verify_base_locked_from_cwd",
        lambda *_args: base,
    )

    def write_after_replace(
        worker_root: Path, *_args: object, **_kwargs: object
    ) -> ReleaseMetadata:
        root.rename(previous)
        root.mkdir()
        (worker_root / "releases").mkdir(parents=True)
        return metadata

    monkeypatch.setattr(runtime_build, "_stage_release_locked", write_after_replace)
    payload = {
        "base_id": base.base_id,
        "base_metadata_sha256": base_metadata_sha,
        "candidate_file": str(candidate_file),
        "release_id": release_id,
        "runtime_root": str(root),
        "wheel": str(wheel),
    }
    cwd_descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(root_descriptor)
        with pytest.raises(RuntimeRootBindingError, match="运行时根"):
            stage_release(payload, root_descriptor)
    finally:
        os.fchdir(cwd_descriptor)
        os.close(root_descriptor)
        os.close(cwd_descriptor)

    assert not (root / "releases").exists()
    assert not (root / "journal").exists()


@pytest.mark.skipif(not _POSIX, reason="仅验证 POSIX root-fd worker")
def testrelease静态复验worker在进入后根替换时不读取替换命名根(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    release_id = "c" * 64
    metadata = ReleaseMetadata(
        schema_version=1,
        release_id=release_id,
        runtime_revision="a" * 40,
        wheel_sha256="d" * 64,
        base_id=base.base_id,
        base_requirements_sha256=base.requirements_sha256,
        base_metadata_sha256="e" * 64,
        app_freeze_sha256="f" * 64,
        created_at="2026-07-21T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python/site-packages/codev_platform_base.pth",
    )
    previous = tmp_path / "runtime-previous"
    monkeypatch.setattr(
        runtime_bound_worker_release,
        "verify_base_locked_from_cwd",
        lambda *_args: base,
    )

    def replace_before_read(
        worker_root: Path, *_args: object, **_kwargs: object
    ) -> ReleaseMetadata:
        root.rename(previous)
        root.mkdir()
        (worker_root / "verification-probe").write_text("old-root", encoding="utf-8")
        return metadata

    monkeypatch.setattr(runtime_build, "_verify_release_directory", replace_before_read)
    payload = {
        "base_id": base.base_id,
        "release_id": release_id,
        "runtime_root": str(root),
    }
    cwd_descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(root_descriptor)
        with pytest.raises(RuntimeRootBindingError, match="运行时根"):
            verify_release_worker(payload, root_descriptor)
    finally:
        os.fchdir(cwd_descriptor)
        os.close(root_descriptor)
        os.close(cwd_descriptor)

    assert not (root / "verification-probe").exists()
