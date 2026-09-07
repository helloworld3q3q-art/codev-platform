"""non-editable 薄 release 的候选构建与内容寻址测试。"""

from __future__ import annotations

import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseCandidate,
    compute_base_id,
    current_abi,
    read_release_candidate,
    read_release_metadata,
    sha256_file,
    write_release_candidate_atomic,
)
import codev_platform.runtime_build as runtime_build
import codev_platform.runtime_candidate as runtime_candidate
import codev_platform.runtime_release_environment as runtime_environment
from codev_platform.runtime_wheel import RuntimeWheelError
from tests.runtime_support import init_git_repo


_WHEEL_BYTES = b"trusted application wheel"


def _candidate_bundle(
    root: Path,
    candidate: ReleaseCandidate,
    wheel_bytes: bytes = _WHEEL_BYTES,
) -> tuple[Path, Path]:
    directory = root / runtime_candidate.compute_candidate_id(
        candidate.runtime_revision,
        candidate.wheel_sha256,
    )
    directory.mkdir()
    wheel = directory / candidate.wheel_name
    wheel.write_bytes(wheel_bytes)
    candidate_path = directory / f"{candidate.wheel_name}.candidate.json"
    write_release_candidate_atomic(candidate_path, candidate)
    return wheel, candidate_path


def _base(root: Path) -> BaseMetadata:
    lock = b"demo==1.0 --hash=sha256:" + b"1" * 64 + b"\n"
    abi = current_abi()
    requirements_sha = hashlib.sha256(lock).hexdigest()
    base_id = compute_base_id(requirements_sha, abi)
    directory = root / "bases" / base_id
    directory.mkdir(parents=True)
    (directory / "requirements.lock").write_bytes(lock)
    metadata = BaseMetadata(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=base_id,
        requirements_sha256=requirements_sha,
        approved_index_url="https://download.pytorch.org/whl/cu128",
        artifact_manifest_sha256="2" * 64,
        freeze_sha256="3" * 64,
        purelib_inventory_sha256="4" * 64,
        abi=abi,
        created_at="2026-07-17T00:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        bin_relative="venv/bin",
        lock_relative="requirements.lock",
    )
    from codev_platform.core.runtime_models import write_base_metadata_atomic

    write_base_metadata_atomic(directory / "base.json", metadata)
    return metadata


def test发布worker根路径以纯词法方式归一化(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """含 `..` 的调用方根不得被严格 worker 请求拒绝。"""
    root = tmp_path / "runtime"
    root.mkdir()
    supplied_root = root.parent / root.name / ".." / root.name
    observed: list[Path] = []
    monkeypatch.setattr(runtime_build, "_require_privileged_builder", lambda: None)
    monkeypatch.setattr(
        runtime_build,
        "_read_candidate_bundle",
        lambda *_args: (object(), Path("candidate.whl"), b"wheel"),
    )
    monkeypatch.setattr(
        runtime_build,
        "_run_bound_stage_release",
        lambda runtime_root, *_args: observed.append(runtime_root),
    )

    runtime_build.stage_release(
        supplied_root,
        tmp_path / "candidate.whl",
        tmp_path / "candidate.json",
        "a" * 64,
    )

    assert observed == [root]


def test静态复验worker根路径以纯词法方式归一化(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """静态复验入口也必须传递不含 `..` 的规范绝对根。"""
    root = tmp_path / "runtime"
    root.mkdir()
    supplied_root = root.parent / root.name / ".." / root.name
    observed: list[Path] = []
    monkeypatch.setattr(
        runtime_build,
        "_run_bound_verify_release",
        lambda runtime_root, *_args: observed.append(runtime_root),
    )

    runtime_build.verify_release(supplied_root, "a" * 64)

    assert observed == [root]


@contextmanager
def _unlocked(*_args, **_kwargs):
    yield object()


def _install_stage_fakes(
    monkeypatch: pytest.MonkeyPatch,
    base: BaseMetadata,
    *,
    events: list[object] | None = None,
) -> None:
    recorded = [] if events is None else events
    monkeypatch.setattr(runtime_build, "_require_privileged_builder", lambda: None)
    monkeypatch.setattr(runtime_build, "_inspect_application_wheel", lambda _wheel: object())
    monkeypatch.setattr(runtime_build, "_verify_base", lambda _root, base_id: base)
    monkeypatch.setattr(runtime_build, "_verify_base_locked", lambda _root, base_id: base)
    monkeypatch.setattr(
        runtime_build,
        "_run_bound_stage_release",
        runtime_build._stage_release_direct,
    )
    monkeypatch.setattr(
        runtime_build,
        "_run_bound_verify_release",
        runtime_build._verify_release_direct,
    )
    monkeypatch.setattr(runtime_build, "_id_lock", _unlocked)
    monkeypatch.setattr(runtime_build, "_isolate_incomplete", lambda *_args: None)
    monkeypatch.setattr(runtime_build, "_isolate_corrupt_completed", lambda *_args: None)
    monkeypatch.setattr(
        runtime_build,
        "_seal_object_access",
        lambda path: recorded.append(("seal_object_access", path)),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_build,
        "_verify_object_access",
        lambda path: recorded.append(("verify_object_access", path)),
        raising=False,
    )

    def stage_environment(
        release_dir: Path,
        _wheel: Path,
        base_dir: Path,
        _base: BaseMetadata,
        _marker: Path,
        *,
        base_purelib_reference: Path | None = None,
        **_kwargs: object,
    ) -> runtime_environment.StagedEnvironment:
        python = release_dir / "venv/bin/python"
        purelib = release_dir / "venv/lib/python/site-packages"
        python.parent.mkdir(parents=True)
        purelib.mkdir(parents=True)
        python.write_bytes(b"python")
        (release_dir / "base").symlink_to(
            Path("..") / ".." / "bases" / base.base_id,
            target_is_directory=True,
        )
        pth = purelib / "codev_platform_base.pth"
        reference = base_dir / base.purelib_relative
        pth.write_text(
            str(reference if base_purelib_reference is None else base_purelib_reference) + "\n",
            encoding="utf-8",
        )
        recorded.append(("environment_complete", release_dir))
        return runtime_environment.StagedEnvironment(
            python_relative="venv/bin/python",
            purelib_relative="venv/lib/python/site-packages",
            base_link_relative="base",
            base_pth_relative="venv/lib/python/site-packages/codev_platform_base.pth",
            app_freeze_sha256="4" * 64,
        )

    monkeypatch.setattr(runtime_build, "_stage_environment", stage_environment)

    def verify_release_directory(_root: Path, release_id: str, **kwargs):
        directory = _root / "releases" / release_id
        if not kwargs.get("object_access_verified", False):
            runtime_build._verify_object_access(directory)
        return read_release_metadata(directory / "release.json")

    monkeypatch.setattr(runtime_build, "_verify_release_directory", verify_release_directory)


def test_build_candidate_rejects_dirty_repo_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = init_git_repo(tmp_path / "repo")
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr(
        runtime_candidate,
        "_build_wheel",
        lambda *_args: pytest.fail("dirty repo must fail before wheel build"),
    )

    with pytest.raises(runtime_build.RuntimeBuildError, match="干净"):
        runtime_build.build_candidate(repo, tmp_path / "artifacts")


def test_build_candidate_binds_commit_and_wheel_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = init_git_repo(tmp_path / "repo")

    def build(snapshot: Path, output: Path) -> Path:
        (repo / "README.md").write_text("mutated\n", encoding="utf-8")
        assert snapshot != repo
        assert (snapshot / "README.md").read_text(encoding="utf-8") == "运行时测试\n"
        wheel = output / "codev_platform-0.1.0-py3-none-any.whl"
        wheel.write_bytes(_WHEEL_BYTES)
        return wheel

    monkeypatch.setattr(runtime_candidate, "_build_wheel", build)

    wheel, candidate_path, candidate = runtime_build.build_candidate(
        repo,
        tmp_path / "artifacts",
    )

    assert candidate == read_release_candidate(candidate_path)
    assert candidate.runtime_revision == runtime_candidate._git_commit(repo)
    assert candidate.wheel_name == wheel.name
    assert candidate.wheel_sha256 == hashlib.sha256(_WHEEL_BYTES).hexdigest()
    assert wheel.parent == candidate_path.parent
    assert not wheel.resolve().is_relative_to(repo.resolve())


def test_stage_release_writes_content_addressed_metadata_and_reuses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="a" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_path = _candidate_bundle(tmp_path, candidate)

    first = runtime_build.stage_release(root, wheel, candidate_path, base.base_id)
    second = runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    assert first == second
    assert len(first.release_id) == 64
    release_dir = root / "releases" / first.release_id
    assert read_release_metadata(release_dir / "release.json") == first
    assert not (release_dir / ".incomplete").exists()
    assert (release_dir / "artifacts" / wheel.name).read_bytes() == _WHEEL_BYTES


def test_release_seals_after_environment_and_again_after_metadata_then_reuses_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    events: list[object] = []
    _install_stage_fakes(monkeypatch, base, events=events)
    original_stage = runtime_build._write_stage
    original_metadata = runtime_build.write_release_metadata_atomic

    def record_stage(marker: Path, stage: str) -> None:
        events.append(("stage", stage))
        original_stage(marker, stage)

    def record_metadata(path: Path, metadata) -> None:
        events.append(("release_json", path))
        original_metadata(path, metadata)

    monkeypatch.setattr(runtime_build, "_write_stage", record_stage)
    monkeypatch.setattr(runtime_build, "write_release_metadata_atomic", record_metadata)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="f" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_path = _candidate_bundle(tmp_path, candidate)

    built = runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    release_dir = root / "releases" / built.release_id
    seal = ("seal_object_access", release_dir)
    verify = ("verify_object_access", release_dir)
    seals = [index for index, event in enumerate(events) if event == seal]
    assert len(seals) == 2
    assert events.index(("environment_complete", release_dir)) < seals[0]
    assert (
        events.index(("release_json", release_dir / "release.json"))
        < events.index(("stage", "after_release_json"))
        < seals[1]
        < events.index(verify)
    )
    metadata_bytes = (release_dir / "release.json").read_bytes()
    events.clear()

    reused = runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    assert reused.release_id == built.release_id
    assert events.count(verify) == 1
    assert not any(event[0] == "seal_object_access" for event in events)
    assert (release_dir / "release.json").read_bytes() == metadata_bytes


def test_stage_release_rejects_wheel_drift_before_creating_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="b" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_path = _candidate_bundle(tmp_path, candidate)
    wheel.write_bytes(b"tampered")

    with pytest.raises(runtime_build.RuntimeBuildError, match="wheel"):
        runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    assert list((root / "releases").iterdir()) == []


def test_stage_release_rejects_untrusted_payload_before_creating_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wheel 静态载荷失败不得先留下 release 或 `.incomplete`。"""
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="c" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_path = _candidate_bundle(tmp_path, candidate)

    def reject_payload(_wheel: Path) -> object:
        raise RuntimeWheelError("应用 wheel purelib 含 codev_platform 外载荷")

    monkeypatch.setattr(runtime_build, "_inspect_application_wheel", reject_payload)

    with pytest.raises(runtime_build.RuntimeBuildError, match="purelib"):
        runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    assert list((root / "releases").iterdir()) == []


def test_stage_release_rejects_unbound_candidate_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="d" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    bundle = tmp_path / "not-content-addressed"
    bundle.mkdir()
    wheel = bundle / candidate.wheel_name
    wheel.write_bytes(_WHEEL_BYTES)
    candidate_path = bundle / f"{wheel.name}.candidate.json"
    write_release_candidate_atomic(candidate_path, candidate)

    with pytest.raises(runtime_build.RuntimeBuildError, match="内容地址"):
        runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    assert list((root / "releases").iterdir()) == []


def test_existing_incomplete_release_is_isolated_before_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="c" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_path = _candidate_bundle(tmp_path, candidate)
    base_sha = sha256_file(root / "bases" / base.base_id / "base.json")
    release_id = runtime_build.compute_release_id(
        candidate.runtime_revision,
        candidate.wheel_sha256,
        base.base_id,
        base_sha,
    )
    incomplete = root / "releases" / release_id
    incomplete.mkdir()
    (incomplete / ".incomplete").write_text("after_marker\n", encoding="utf-8")
    isolated: list[str] = []

    def isolate(bound_root: object, kind: str, object_id: str):
        assert not isinstance(bound_root, Path)
        assert kind == "release"
        isolated.append(object_id)
        for child in incomplete.iterdir():
            child.unlink()
        incomplete.rmdir()

    monkeypatch.setattr(runtime_build, "_isolate_incomplete", isolate)

    result = runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    assert isolated == [result.release_id]


def test_existing_corrupt_completed_release_is_isolated_before_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="e" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_path = _candidate_bundle(tmp_path, candidate)
    _install_stage_fakes(monkeypatch, base)
    first = runtime_build.stage_release(root, wheel, candidate_path, base.base_id)
    release_dir = root / "releases" / first.release_id
    (release_dir / "release.json").write_text("损坏元数据", encoding="utf-8")
    quarantine = root / "quarantine" / "corrupt" / "release" / f"{first.release_id}.saved"

    def verify(target_root: Path, release_id: str, **_kwargs):
        try:
            return read_release_metadata(target_root / "releases" / release_id / "release.json")
        except ValueError:
            raise runtime_build.RuntimeBuildError("薄 release 元数据无效") from None

    def isolate(bound_root: object, kind: str, object_id: str) -> Path:
        assert not isinstance(bound_root, Path)
        assert (kind, object_id) == ("release", first.release_id)
        quarantine.parent.mkdir(parents=True)
        release_dir.replace(quarantine)
        return quarantine

    monkeypatch.setattr(runtime_build, "_verify_release_directory", verify)
    monkeypatch.setattr(runtime_build, "_isolate_corrupt_completed", isolate)

    rebuilt = runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    assert rebuilt.release_id == first.release_id
    assert (quarantine / "release.json").read_text(encoding="utf-8") == "损坏元数据"
    assert (root / "releases" / first.release_id / "release.json").is_file()


def test_stage_failure_keeps_durable_failure_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    (root / "releases").mkdir(parents=True)
    base = _base(root)
    _install_stage_fakes(monkeypatch, base)
    candidate = ReleaseCandidate(
        schema_version=1,
        runtime_revision="e" * 40,
        wheel_name="codev_platform-0.1.0-py3-none-any.whl",
        wheel_sha256=hashlib.sha256(_WHEEL_BYTES).hexdigest(),
    )
    wheel, candidate_path = _candidate_bundle(tmp_path, candidate)
    monkeypatch.setattr(
        runtime_build,
        "_stage_environment",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            runtime_build.RuntimeBuildError("injected failure")
        ),
    )

    with pytest.raises(runtime_build.RuntimeBuildError, match="injected"):
        runtime_build.stage_release(root, wheel, candidate_path, base.base_id)

    releases = list((root / "releases").iterdir())
    assert len(releases) == 1
    assert (releases[0] / ".incomplete").read_text(encoding="ascii") == "after_marker\n"


def test_write_all_retries_short_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bytes] = []

    def short_write(_descriptor: int, payload: bytes) -> int:
        count = min(2, len(payload))
        seen.append(payload[:count])
        return count

    monkeypatch.setattr(runtime_environment.os, "write", short_write)

    runtime_environment.write_all(37, b"abcdef")

    assert b"".join(seen) == b"abcdef"


@pytest.mark.skipif(os.name == "nt", reason="仅 POSIX 支持精确文件权限")
def test独占运行时写入不受umask裁剪(tmp_path: Path) -> None:
    """最终原子发布依赖初始 `.pth` 的精确 0640 权限。"""
    target = tmp_path / "codev_platform_base.pth"
    previous_umask = os.umask(0o077)
    try:
        runtime_environment.write_bytes_exclusive(target, b"base\n", mode=0o640)
    finally:
        os.umask(previous_umask)

    assert target.read_bytes() == b"base\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_import_probe_requires_isolation_and_all_managed_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_kwargs) -> str:
        commands.append(command)
        return "ok\n"

    monkeypatch.setattr(runtime_environment, "_run_checked", run)

    runtime_environment.probe_release_imports(
        Path("/runtime/venv/bin/python"),
        Path("/runtime/venv/lib/site-packages"),
        Path("/runtime/base/lib/site-packages"),
    )

    command = commands[0]
    assert command[1:3] == ("-B", "-I")
    assert "paths.index(app)<paths.index(base)" in command[4]
    for module in runtime_environment.MANAGED_IMPORTS:
        assert module in command[4]


def test_locked_release_interfaces_leave_lock_lifecycle_to_caller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    release_id = "a" * 64
    base_id = "b" * 64
    root.mkdir()
    metadata = SimpleNamespace(release_id=release_id, base_id=base_id)
    calls: list[tuple[Path, str, bool]] = []
    monkeypatch.setattr(runtime_build, "_require_runtime_root", lambda value: value)
    monkeypatch.setattr(runtime_build, "_verify_object_access", lambda _path: None)
    monkeypatch.setattr(
        runtime_build,
        "_read_release_metadata_locked",
        lambda value, object_id: metadata,
    )

    def verify(
        value: Path,
        object_id: str,
        *,
        base_locked: bool = False,
        verified_base=None,
    ):
        assert verified_base.base_id == base_id
        calls.append((value, object_id, base_locked))
        return metadata

    monkeypatch.setattr(runtime_build, "_verify_release_directory", verify)
    monkeypatch.setattr(
        runtime_build,
        "_id_lock",
        lambda *_args, **_kwargs: pytest.fail("已锁定接口不得重新获取 release 锁"),
    )

    assert runtime_build.read_release_base_id_locked(root, release_id) == base_id
    verified_base = SimpleNamespace(base_id=base_id)
    assert (
        runtime_build.verify_release_locked(
            root,
            release_id,
            verified_base=verified_base,
        )
        is metadata
    )
    assert calls == [(root, release_id, True)]


def test_locked_release_rejects_mismatched_preverified_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_id = "a" * 64
    monkeypatch.setattr(
        runtime_build,
        "_read_release_metadata_locked",
        lambda *_args, **_kwargs: SimpleNamespace(base_id="b" * 64),
    )
    monkeypatch.setattr(runtime_build, "_verify_object_access", lambda _path: None)

    with pytest.raises(runtime_build.RuntimeBuildError, match="预验证基座身份"):
        runtime_build._verify_release_directory(
            tmp_path,
            release_id,
            base_locked=True,
            verified_base=SimpleNamespace(base_id="c" * 64),
        )
