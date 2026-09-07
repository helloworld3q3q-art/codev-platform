"""把完整 Git 提交物化为不含仓库元数据的只读构建快照。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
from collections.abc import Mapping
from uuid import uuid4

from codev_platform.runtime_deadline import bounded_runtime_timeout
from codev_platform.runtime_process import isolated_process_environment


_GIT_OID = re.compile(r"[0-9a-f]{40}\Z")
_SOURCE_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_MAX_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_RUNUSER = "/usr/sbin/runuser"


class RuntimeGitSnapshotError(RuntimeError):
    """Git 提交不能安全物化为正式构建输入。"""


@dataclass(frozen=True, slots=True)
class _GitCommandContext:
    """固定 Git 调用身份，避免 root 隐式信任服务账号工作树。"""

    argv_prefix: tuple[str, ...]
    cwd: Path
    environment: Mapping[str, str]

    def command(self, *arguments: str) -> tuple[str, ...]:
        return (*self.argv_prefix, *arguments)


@dataclass(frozen=True, slots=True)
class _ServiceGitIdentity:
    """仅保存经 POSIX 账号数据库解析后的最小 Git 执行身份。"""

    name: str
    uid: int
    home: str


def materialize_commit(
    repo: Path,
    revision: str,
    destination: Path,
    *,
    source_user: str | None = None,
) -> Path:
    """通过 ``git archive`` 物化精确提交，拒绝链接与路径逃逸。"""
    source = _repository(Path(repo))
    commit = _revision(revision)
    target = _new_destination(Path(destination))
    archive_path = target.parent / f".{target.name}.{uuid4().hex}.tar"
    try:
        _write_archive(source, commit, archive_path, source_user=source_user)
        target.mkdir(mode=0o755)
        _extract_archive(archive_path, target)
        return target
    except RuntimeGitSnapshotError:
        raise
    except (OSError, tarfile.TarError, subprocess.SubprocessError):
        raise RuntimeGitSnapshotError("Git 提交快照构建失败") from None
    finally:
        try:
            archive_path.unlink(missing_ok=True)
        except OSError:
            pass


def _repository(path: Path) -> Path:
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError:
        raise RuntimeGitSnapshotError("Git 仓库不可用") from None
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise RuntimeGitSnapshotError("Git 仓库必须是非链接目录")
    return resolved


def git_command_context(
    repo: Path,
    *,
    source_user: str | None = None,
) -> _GitCommandContext:
    """返回无网络 Git 读取上下文；降权只接受显式、已验证的服务账号。"""
    source = _repository(repo)
    if source_user is None:
        return _GitCommandContext(
            argv_prefix=("git", "-C", str(source)),
            cwd=Path(source.anchor),
            environment=isolated_process_environment(),
        )
    identity = _require_service_git_identity(source_user)
    _require_service_owned_repository(source, identity)
    return _GitCommandContext(
        argv_prefix=(
            _RUNUSER,
            "--user",
            identity.name,
            "--",
            "git",
            "-C",
            str(source),
        ),
        cwd=Path(source.anchor),
        environment=isolated_process_environment(
            overrides={
                "HOME": identity.home,
                "PATH": "/usr/bin:/bin",
            }
        ),
    )


def _require_service_git_identity(value: object) -> _ServiceGitIdentity:
    if (
        type(value) is not str
        or _SOURCE_USER.fullmatch(value) is None
        or os.name != "posix"
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
    ):
        raise RuntimeGitSnapshotError("服务账号 Git 身份不可用")
    try:
        import pwd

        account = pwd.getpwnam(value)
    except (ImportError, KeyError):
        raise RuntimeGitSnapshotError("服务账号 Git 身份不可用") from None
    if account.pw_uid == 0 or not account.pw_dir.startswith("/"):
        raise RuntimeGitSnapshotError("服务账号 Git 身份不可用")
    return _ServiceGitIdentity(account.pw_name, account.pw_uid, account.pw_dir)


def _require_service_owned_repository(
    repository: Path,
    identity: _ServiceGitIdentity,
) -> None:
    try:
        metadata = repository.lstat()
    except OSError:
        raise RuntimeGitSnapshotError("服务账号源码仓不可用") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != identity.uid
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RuntimeGitSnapshotError("服务账号源码仓身份不安全")


def _revision(value: object) -> str:
    if type(value) is not str or _GIT_OID.fullmatch(value) is None or set(value) == {"0"}:
        raise RuntimeGitSnapshotError("Git 提交必须是完整 40 位小写 OID")
    return value


def _new_destination(path: Path) -> Path:
    try:
        parent = path.parent.resolve(strict=True)
    except OSError:
        raise RuntimeGitSnapshotError("快照目标父目录不可用") from None
    target = parent / path.name
    if path != target or target.exists() or target.is_symlink():
        raise RuntimeGitSnapshotError("快照目标必须是尚不存在的规范路径")
    return target


def _write_archive(
    repo: Path,
    revision: str,
    archive_path: Path,
    *,
    source_user: str | None = None,
) -> None:
    context = git_command_context(repo, source_user=source_user)
    try:
        with archive_path.open("xb") as stream:
            subprocess.run(
                context.command("archive", "--format=tar", revision),
                cwd=context.cwd,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=bounded_runtime_timeout(120.0),
                env=context.environment,
            )
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, subprocess.SubprocessError):
        raise RuntimeGitSnapshotError("Git 提交无法归档") from None


def _extract_archive(archive_path: Path, destination: Path) -> None:
    total_size = 0
    seen: set[str] = set()
    with tarfile.open(archive_path, mode="r:") as archive:
        for member in archive:
            name = _safe_member_name(member)
            if name in seen:
                raise RuntimeGitSnapshotError("Git 归档含重复成员")
            seen.add(name)
            target = destination.joinpath(*PurePosixPath(name).parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o755)
                continue
            if not member.isfile() or member.size > _MAX_MEMBER_BYTES:
                raise RuntimeGitSnapshotError("Git 归档只接受受限普通文件")
            total_size += member.size
            if total_size > _MAX_ARCHIVE_BYTES:
                raise RuntimeGitSnapshotError("Git 归档展开总量过大")
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            _write_member(archive, member, target)


def _safe_member_name(member: tarfile.TarInfo) -> str:
    name = member.name
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or path.is_absolute()
        or path.as_posix() != name.rstrip("/")
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeGitSnapshotError("Git 归档成员路径无效")
    return name.rstrip("/")


def _write_member(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    source = archive.extractfile(member)
    if source is None:
        raise RuntimeGitSnapshotError("Git 归档普通文件内容缺失")
    mode = 0o755 if member.mode & 0o111 else 0o644
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        mode,
    )
    written = 0
    try:
        with source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                written += len(block)
                if written > member.size:
                    raise RuntimeGitSnapshotError("Git 归档成员大小漂移")
                _write_all(descriptor, block)
        if written != member.size:
            raise RuntimeGitSnapshotError("Git 归档成员内容不完整")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise RuntimeGitSnapshotError("Git 快照写入未取得进展")
        offset += written


__all__ = ["RuntimeGitSnapshotError", "git_command_context", "materialize_commit"]
