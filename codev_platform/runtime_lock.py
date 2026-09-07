"""构建并验证生产运行时使用的受审 CUDA 依赖 hash lock。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from codev_platform.core.runtime_models import RequirementsLockInfo
from codev_platform.runtime_artifact_staging import (
    RuntimeArtifactStagingError,
    locked_artifact_publication,
    locked_artifact_workspace,
)
from codev_platform.runtime_cancellable_process import (
    RuntimeCancellation,
    RuntimeCancellableProcessError,
    run_cancellable_process,
)
from codev_platform.runtime_deadline import bounded_runtime_timeout
from codev_platform.runtime_deadline import current_runtime_deadline, runtime_operation_timeout
from codev_platform.runtime_dependency_contract import RequirementsLockContract
from codev_platform.runtime_lock_download import (
    ProgressReporter,
    RuntimeWheelDownloadError,
    download_wheels_bounded,
)
from codev_platform import runtime_lock_parsing as _parsing
from codev_platform.runtime_process import isolated_process_environment
from codev_platform.runtime_wheel_tags import (
    RuntimeWheelCompatibilityError,
    require_compatible_wheel,
)


RuntimeLockError = _parsing.RuntimeLockError
_SUBPROCESS_TIMEOUT_SEC = 300
_LOCK_BUILD_TIMEOUT_SEC = 3600.0
_MAX_DOWNLOAD_WORKERS = 4


def _matching_wheels(download_dir: Path, pin: _parsing.Pin) -> list[Path]:
    matches: list[Path] = []
    for candidate in download_dir.glob("*.whl"):
        try:
            _verify_wheel_for_pin(candidate, download_dir, pin)
        except RuntimeLockError:
            if _filename_targets_pin(candidate.name, pin):
                raise
            continue
        matches.append(candidate)
    return matches


def _download_one(
    requirement: str,
    download_dir: Path,
    approved_index_url: str,
    primary_index_url: str,
    cancellation: RuntimeCancellation | None = None,
) -> Path:
    """下载一个当前平台 wheel；外部进程是构建器唯一网络边界。"""
    pin = _parsing.parse_pin(requirement, kind="下载请求")
    existing = _matching_wheels(download_dir, pin)
    if len(existing) == 1:
        return existing[0]
    if len(existing) > 1:
        raise _parsing.fail("本地 wheel 结果不能唯一绑定精确 pin")
    source_arguments = (
        (_parsing.PRIMARY_INDEX_OPTION, approved_index_url)
        if "+cu" in pin.version
        else (_parsing.PRIMARY_INDEX_OPTION, primary_index_url)
    )
    command = (
        sys.executable,
        "-I",
        "-m",
        "pip",
        "--isolated",
        "download",
        "--disable-pip-version-check",
        "--no-input",
        "--no-deps",
        "--only-binary=:all:",
        "--dest",
        str(download_dir),
        *source_arguments,
        pin.requirement,
    )
    token = RuntimeCancellation() if cancellation is None else cancellation
    try:
        run_cancellable_process(
            command,
            cancellation=token,
            timeout=bounded_runtime_timeout(_SUBPROCESS_TIMEOUT_SEC),
            environment=isolated_process_environment(network=True),
        )
    except RuntimeCancellableProcessError:
        raise _parsing.fail("pip wheel 下载失败") from None
    matches = _matching_wheels(download_dir, pin)
    if len(matches) != 1:
        raise _parsing.fail("pip 下载结果不能唯一绑定精确 pin")
    return matches[0]


def _filename_targets_pin(filename: str, pin: _parsing.Pin) -> bool:
    parts = filename.removesuffix(".whl").split("-")
    return (
        filename.endswith(".whl")
        and len(parts) >= 5
        and _parsing.canonical_name(parts[0]) == pin.name
        and parts[1] == pin.version
    )


def _verify_wheel_for_pin(path: Path, download_dir: Path, pin: _parsing.Pin) -> Path:
    artifact = Path(path)
    try:
        root = download_dir.resolve(strict=True)
        if artifact.is_symlink() or not artifact.is_file():
            raise OSError
        resolved = artifact.resolve(strict=True)
    except OSError:
        raise _parsing.fail("wheel 制品不可用") from None
    if resolved.parent != root or artifact.name != resolved.name or artifact.suffix != ".whl":
        raise _parsing.fail("wheel 制品逃逸下载目录")
    if not artifact.name.isascii() or any(char.isspace() for char in artifact.name):
        raise _parsing.fail("wheel 文件名无效")
    try:
        require_compatible_wheel(
            artifact.name,
            package=pin.name,
            version=pin.version,
        )
    except RuntimeWheelCompatibilityError as error:
        raise _parsing.fail(str(error)) from None
    return resolved


def _artifact_for_pin(
    path: Path,
    download_dir: Path,
    pin: _parsing.Pin,
) -> _parsing.Artifact:
    artifact = _verify_wheel_for_pin(path, download_dir, pin)
    return _parsing.Artifact(
        package=pin.name,
        filename=artifact.name,
        sha256=_sha256_file(artifact),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        raise _parsing.fail("wheel 制品不可读") from None
    return digest.hexdigest()


def _render_lock(
    pins: tuple[_parsing.Pin, ...],
    artifacts: tuple[_parsing.Artifact, ...],
    primary_index_url: str,
    approved_index_url: str,
) -> bytes:
    by_package = {artifact.package: artifact for artifact in artifacts}
    lines = [_parsing.LOCK_HEADER]
    lines.extend(
        f"{_parsing.ARTIFACT_PREFIX}{item.package} {item.filename} {item.sha256}"
        for item in sorted(artifacts, key=lambda artifact: artifact.filename)
    )
    lines.extend(
        (
            f"{_parsing.PRIMARY_INDEX_OPTION} {primary_index_url}",
            f"{_parsing.INDEX_OPTION} {approved_index_url}",
            "",
        )
    )
    lines.extend(f"{pin.requirement} --hash=sha256:{by_package[pin.name].sha256}" for pin in pins)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _write_validated_lock(
    output: Path,
    content: bytes,
    approved_source: Path,
) -> RequirementsLockInfo:
    target = Path(output)
    if target.exists() and target.is_symlink():
        raise _parsing.fail("锁输出不能是符号链接")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
        )
    except OSError:
        raise _parsing.fail("锁输出目录不可用") from None
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        info = validate_requirements_lock(temporary, approved_source)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
        return info
    except RuntimeLockError:
        raise
    except OSError:
        raise _parsing.fail("锁文件无法原子写入") from None
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@runtime_operation_timeout(_LOCK_BUILD_TIMEOUT_SEC)
def build_hashed_lock(
    raw_freeze: Path,
    approved_source: Path,
    output: Path,
    download_dir: Path,
    *,
    progress: ProgressReporter | None = None,
) -> RequirementsLockInfo:
    """从完整 freeze 下载逐 pin wheel，生成可供 ``--require-hashes`` 使用的锁。"""
    primary_index_url, approved_index_url, index_tag = _parsing.approved_indexes(
        approved_source
    )
    pins = _parsing.parse_freeze(raw_freeze)
    _parsing.require_cuda_contract({pin.name: pin for pin in pins}, index_tag)
    try:
        with locked_artifact_publication(Path(output)) as locked_output:
            with locked_artifact_workspace(Path(download_dir)) as workspace:
                staged_output = _staged_lock_output(locked_output)
                info = _build_hashed_lock_in_workspace(
                    pins,
                    primary_index_url,
                    approved_index_url,
                    approved_source,
                    staged_output,
                    workspace.working,
                    allow_download=workspace.publish_required,
                    progress=progress,
                )
                workspace.publish()
                return _publish_staged_lock(staged_output, locked_output, info)
    except RuntimeArtifactStagingError as error:
        raise _parsing.fail(str(error)) from None


def _build_hashed_lock_in_workspace(
    pins: tuple[_parsing.Pin, ...],
    primary_index_url: str,
    approved_index_url: str,
    approved_source: Path,
    output: Path,
    artifacts_root: Path,
    *,
    allow_download: bool,
    progress: ProgressReporter | None,
) -> RequirementsLockInfo:
    if artifacts_root.is_symlink() or not artifacts_root.is_dir():
        raise _parsing.fail("wheel 下载目录不可用") from None
    if allow_download:
        _verify_existing_artifacts(artifacts_root, pins)
        deadline = current_runtime_deadline()
        if deadline is None:
            raise _parsing.fail("依赖锁总截止时间未建立")

        def download(
            pin: _parsing.Pin,
            cancellation: RuntimeCancellation,
        ) -> Path:
            return _download_one(
                pin.requirement,
                artifacts_root,
                approved_index_url,
                primary_index_url,
                cancellation,
            )

        try:
            downloaded = download_wheels_bounded(
                pins,
                download,
                deadline=deadline,
                reporter=progress,
                max_workers=_MAX_DOWNLOAD_WORKERS,
            )
        except RuntimeWheelDownloadError as error:
            raise _parsing.fail(str(error)) from None
    else:
        downloaded = _require_complete_published_artifacts(artifacts_root, pins)
    artifacts: list[_parsing.Artifact] = []
    seen_filenames: set[str] = set()
    for pin, wheel in zip(pins, downloaded, strict=True):
        artifact = _artifact_for_pin(wheel, artifacts_root, pin)
        if artifact.filename in seen_filenames:
            raise _parsing.fail("wheel 制品文件名重复")
        seen_filenames.add(artifact.filename)
        artifacts.append(artifact)
    _require_exact_artifact_directory(artifacts_root, seen_filenames)
    content = _render_lock(
        pins,
        tuple(artifacts),
        primary_index_url,
        approved_index_url,
    )
    return _write_validated_lock(output, content, approved_source)


def _staged_lock_output(output: Path) -> Path:
    """锁文件最后发布；wheelhouse 失败时只留下不可见的续跑文件。"""
    target = Path(output)
    if target.name in {"", ".", ".."}:
        raise _parsing.fail("锁输出路径无效")
    return target.with_name(f".{target.name}.incomplete")


def _publish_staged_lock(
    staged: Path,
    output: Path,
    info: RequirementsLockInfo,
) -> RequirementsLockInfo:
    """在 wheelhouse 已原子发布后，把已验证锁作为最后提交点。"""
    target = Path(output)
    try:
        if staged.is_symlink() or not staged.is_file():
            raise OSError
        if target.exists() and target.is_symlink():
            raise OSError
        os.replace(staged, target)
        _fsync_directory(target.parent)
    except OSError:
        raise _parsing.fail("锁文件无法最终发布") from None
    return info


def _verify_existing_artifacts(download_dir: Path, pins: tuple[_parsing.Pin, ...]) -> None:
    """允许精确 pin 的已完成 wheel 续跑，拒绝任何额外或不兼容制品。"""
    try:
        entries = tuple(download_dir.iterdir())
    except OSError:
        raise _parsing.fail("wheel 下载目录不可枚举") from None
    for entry in entries:
        matches = 0
        for pin in pins:
            try:
                _verify_wheel_for_pin(entry, download_dir, pin)
            except RuntimeLockError:
                continue
            matches += 1
        if matches != 1:
            raise _parsing.fail("wheel 下载目录含额外、不兼容或不受管制品")


def _require_complete_published_artifacts(
    download_dir: Path,
    pins: tuple[_parsing.Pin, ...],
) -> tuple[Path, ...]:
    """已发布 wheelhouse 是不可变快照，只允许完整复用，禁止原地补写。"""
    _verify_existing_artifacts(download_dir, pins)
    selected: list[Path] = []
    for pin in pins:
        matches = _matching_wheels(download_dir, pin)
        if len(matches) != 1:
            raise _parsing.fail("已发布 wheelhouse 不完整或发生漂移")
        selected.append(matches[0])
    return tuple(selected)


def _require_exact_artifact_directory(download_dir: Path, expected: set[str]) -> None:
    try:
        actual = {entry.name for entry in download_dir.iterdir()}
    except OSError:
        raise _parsing.fail("wheel 下载目录不可枚举") from None
    if actual != expected:
        raise _parsing.fail("wheel 下载完成后制品集合不精确")


def inspect_requirements_lock(
    path: Path,
    expected_index_url: str,
    expected_primary_index_url: str | None = None,
) -> RequirementsLockInfo:
    """从已保存锁本身按显式预期官方索引重算身份。"""
    return _parsing.inspect_requirements_lock(
        path,
        expected_index_url,
        expected_primary_index_url,
    )


def inspect_requirements_lock_contract(
    path: Path,
    expected_index_url: str,
    expected_primary_index_url: str | None = None,
) -> RequirementsLockContract:
    """一次解析返回锁身份与完整精确 pin。"""
    return _parsing.inspect_requirements_lock_contract(
        path,
        expected_index_url,
        expected_primary_index_url,
    )


def validate_requirements_lock(
    path: Path,
    approved_source: Path,
) -> RequirementsLockInfo:
    """从批准 requirements 提取索引后严格验证 hash lock。"""
    primary, approved_index_url, _index_tag = _parsing.approved_indexes(approved_source)
    return inspect_requirements_lock(path, approved_index_url, primary)


def validate_requirements_lock_contract(
    path: Path,
    approved_source: Path,
) -> RequirementsLockContract:
    """按批准来源一次验证锁身份与完整精确 pin。"""
    primary, approved_index_url, _index_tag = _parsing.approved_indexes(approved_source)
    return inspect_requirements_lock_contract(path, approved_index_url, primary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证生产运行时依赖 hash lock")
    subparsers = parser.add_subparsers(dest="action", required=True)
    validate_parser = subparsers.add_parser("validate", help="验证 hash lock")
    validate_parser.add_argument("lock", type=Path)
    validate_parser.add_argument("--approved", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        info = validate_requirements_lock(args.lock, args.approved)
    except RuntimeLockError:
        print(json.dumps({"error": "runtime_lock_invalid", "status": "error"}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifact_manifest_sha256": info.artifact_manifest_sha256,
                "cuda_tags": sorted(info.cuda_tags),
                "pin_count": info.pin_count,
                "requirements_sha256": info.requirements_sha256,
                "status": "ok",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RuntimeLockError",
    "build_hashed_lock",
    "inspect_requirements_lock",
    "inspect_requirements_lock_contract",
    "main",
    "validate_requirements_lock",
    "validate_requirements_lock_contract",
]
