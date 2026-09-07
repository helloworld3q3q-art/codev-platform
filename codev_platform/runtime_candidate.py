"""从精确 Git 提交快照构建内容寻址的应用 wheel 候选。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from uuid import uuid4

from codev_platform.core.runtime_models import (
    ReleaseCandidate,
    read_release_candidate,
    sha256_file,
    write_release_candidate_atomic,
)
from codev_platform.runtime_errors import RuntimeBuildError, RuntimeIdCollisionError
from codev_platform.runtime_deadline import (
    bounded_runtime_timeout,
    runtime_operation_timeout,
)
from codev_platform.runtime_git_snapshot import (
    RuntimeGitSnapshotError,
    git_command_context,
    materialize_commit,
)
from codev_platform.runtime_process import isolated_process_environment


_WHEEL_NAME = re.compile(r"[A-Za-z0-9_.+-]+\.whl\Z")
_SUBPROCESS_TIMEOUT_SEC = 600


@runtime_operation_timeout(900.0)
def build_candidate(
    repo: Path,
    out_dir: Path,
    revision: str | None = None,
    *,
    source_user: str | None = None,
) -> tuple[Path, Path, ReleaseCandidate]:
    """从精确提交快照构建 non-editable wheel 候选。

    未指定提交时保留交互式开发门禁，要求工作树干净并构建 ``HEAD``；
    部署入口必须传完整提交号，构建只读取 Git 对象库，不切换也不复制工作树状态。
    """
    source = _require_repository(repo, source_user=source_user)
    selected_revision = (
        _git_commit(source, source_user=source_user)
        if revision is None
        else _require_revision(revision)
    )
    if revision is None:
        _require_clean_worktree(source, source_user=source_user)
    output = _require_external_output_directory(out_dir, source)
    temporary = output / f".candidate-{uuid4().hex}"
    temporary.mkdir(mode=0o755)
    keep = False
    try:
        snapshot = materialize_commit(
            source,
            selected_revision,
            temporary / "source",
            source_user=source_user,
        )
        wheel = _build_wheel(snapshot, temporary)
        shutil.rmtree(snapshot)
        _validate_built_wheel(wheel, temporary)
        _fsync_file(wheel)
        digest = sha256_file(wheel)
        candidate = ReleaseCandidate(
            schema_version=1,
            runtime_revision=selected_revision,
            wheel_name=wheel.name,
            wheel_sha256=digest,
        )
        candidate_name = f"{wheel.name}.candidate.json"
        write_release_candidate_atomic(temporary / candidate_name, candidate)
        final = output / compute_candidate_id(selected_revision, digest)
        if final.exists() or final.is_symlink():
            return _reuse_candidate(final, candidate_name, candidate)
        _fsync_directory(temporary)
        try:
            os.replace(temporary, final)
        except OSError:
            if not final.exists() and not final.is_symlink():
                raise
            return _reuse_candidate(final, candidate_name, candidate)
        _fsync_directory(output)
        keep = True
        return final / wheel.name, final / candidate_name, candidate
    except (RuntimeBuildError, RuntimeIdCollisionError):
        raise
    except (OSError, RuntimeGitSnapshotError, subprocess.SubprocessError, ValueError) as error:
        raise RuntimeBuildError("应用 wheel 候选构建失败") from error
    finally:
        if not keep and temporary.exists():
            shutil.rmtree(temporary)


def compute_candidate_id(runtime_revision: str, wheel_sha256: str) -> str:
    """返回候选目录使用的固定长度内容地址。"""
    return hashlib.sha256(f"{runtime_revision}\n{wheel_sha256}\n".encode("ascii")).hexdigest()


def _reuse_candidate(
    final: Path,
    candidate_name: str,
    expected: ReleaseCandidate,
) -> tuple[Path, Path, ReleaseCandidate]:
    if final.is_symlink() or not final.is_dir():
        raise RuntimeIdCollisionError("相同候选 ID 的既有路径不受信任")
    try:
        existing = read_release_candidate(final / candidate_name)
        wheel = final / expected.wheel_name
        digest = sha256_file(wheel)
    except (OSError, ValueError):
        raise RuntimeIdCollisionError("相同候选 ID 的既有制品无效") from None
    if existing != expected or wheel.is_symlink() or digest != expected.wheel_sha256:
        raise RuntimeIdCollisionError("相同候选 ID 的既有制品发生碰撞")
    return wheel, final / candidate_name, expected


def _validate_built_wheel(wheel: Path, temporary: Path) -> None:
    try:
        parent = wheel.parent.resolve(strict=True)
        expected_parent = temporary.resolve(strict=True)
    except OSError as error:
        raise RuntimeBuildError("候选 wheel 构建路径不可用") from error
    if parent != expected_parent or wheel.is_symlink() or not wheel.is_file():
        raise RuntimeBuildError("候选 wheel 逃逸构建目录")
    if _WHEEL_NAME.fullmatch(wheel.name) is None:
        raise RuntimeBuildError("候选 wheel 文件名无效")


def _require_repository(value: Path, *, source_user: str | None = None) -> Path:
    try:
        raw = Path(value).expanduser()
        metadata = raw.lstat()
        repo = raw.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise RuntimeBuildError("应用源码仓不可用") from error
    if not stat.S_ISDIR(metadata.st_mode) or raw.is_symlink():
        raise RuntimeBuildError("应用源码仓不受信任")
    if repo.as_posix().startswith("/mnt/"):
        raise RuntimeBuildError("正式 wheel 不接受 /mnt 检出目录")
    _run_git(repo, "rev-parse", "--git-dir", source_user=source_user)
    return repo


def _require_clean_worktree(repo: Path, *, source_user: str | None = None) -> None:
    if _run_git(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
        source_user=source_user,
    ):
        raise RuntimeBuildError("应用源码仓必须保持干净")


def _require_revision(value: object) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise RuntimeBuildError("应用源码提交必须是完整 40 位小写 OID")
    if set(value) == {"0"}:
        raise RuntimeBuildError("应用源码提交无效")
    return value


def _require_external_output_directory(value: Path, repo: Path) -> Path:
    try:
        output = Path(value).expanduser()
        output.mkdir(parents=True, exist_ok=True)
        canonical = output.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise RuntimeBuildError("候选输出目录不可用") from error
    if output.is_symlink() or canonical.is_relative_to(repo):
        raise RuntimeBuildError("候选输出目录必须位于源码仓外")
    return canonical


def _git_commit(repo: Path, *, source_user: str | None = None) -> str:
    value = _run_git(
        repo,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        source_user=source_user,
    )
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise RuntimeBuildError("应用源码提交身份无效")
    return value


def _run_git(repo: Path, *arguments: str, source_user: str | None = None) -> str:
    context = git_command_context(repo, source_user=source_user)
    try:
        completed = subprocess.run(
            context.command(*arguments),
            cwd=context.cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=bounded_runtime_timeout(10.0),
            env=context.environment,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeBuildError("应用源码 Git 身份无法证明") from error
    return completed.stdout.strip()


def _build_wheel(repo: Path, output: Path) -> Path:
    _run_checked(
        (
            sys.executable,
            "-B",
            "-I",
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "--no-input",
            "wheel",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(output),
            str(repo),
        )
    )
    wheels = tuple(output.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeBuildError("应用构建必须只产出一个 wheel")
    return wheels[0]


def _run_checked(command: tuple[str, ...]) -> None:
    try:
        subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=bounded_runtime_timeout(_SUBPROCESS_TIMEOUT_SEC),
            env=isolated_process_environment(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeBuildError("应用 wheel 构建子进程失败") from error


def _fsync_file(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["build_candidate", "compute_candidate_id"]
