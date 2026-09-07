"""特权观测产物数据根的 descriptor-bound 路径边界。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat


class RuntimeArtifactDescriptorPathError(RuntimeError):
    """数据根无法在同一条受信 descriptor 链内完成绑定。"""


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    device: int
    inode: int
    kind: int
    uid: int
    gid: int
    mode: int


@dataclass(slots=True)
class BoundRuntimeArtifactPath:
    """持有从文件系统根到数据根的完整目录 descriptor 链。"""

    path: Path
    service_uid: int
    service_gid: int
    _names: tuple[str, ...]
    _descriptors: tuple[int, ...]
    _identities: tuple[_DirectoryIdentity, ...]
    _verification_descriptors: tuple[int, ...] = ()

    @property
    def root_fd(self) -> int:
        if not self._descriptors:
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根 descriptor 已关闭")
        return self._descriptors[-1]

    @property
    def root_device(self) -> int:
        if not self._identities:
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根 descriptor 已关闭")
        return self._identities[-1].device

    @property
    def verified_root_fd(self) -> int:
        if not self._verification_descriptors:
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根尚未完成可见性复验")
        return self._verification_descriptors[-1]

    def verify_visible(self) -> None:
        """在出具证明前复验持有链和当前可见链仍指向相同对象。"""
        if not self._descriptors or self._verification_descriptors:
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根身份复验状态无效")
        held = _validate_open_chain(
            self._names,
            self._descriptors,
            service_uid=self.service_uid,
            service_gid=self.service_gid,
        )
        if held != self._identities:
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根身份复验失败")
        descriptors, identities = _open_chain(
            self.path,
            service_uid=self.service_uid,
            service_gid=self.service_gid,
        )
        if identities != self._identities:
            _close_descriptors(descriptors)
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根身份复验失败")
        self._verification_descriptors = descriptors
        self.confirm_visible()

    def confirm_visible(self) -> None:
        """复验已持有的初始链与证明链仍通过各自父目录链接。"""
        if not self._descriptors or not self._verification_descriptors:
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根身份复验状态无效")
        held = _validate_open_chain(
            self._names,
            self._descriptors,
            service_uid=self.service_uid,
            service_gid=self.service_gid,
        )
        visible = _validate_open_chain(
            self._names,
            self._verification_descriptors,
            service_uid=self.service_uid,
            service_gid=self.service_gid,
        )
        if held != self._identities or visible != self._identities:
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根身份复验失败")

    def close(self) -> None:
        _close_descriptors(self._verification_descriptors)
        _close_descriptors(self._descriptors)
        self._verification_descriptors = ()
        self._descriptors = ()
        self._identities = ()

    def __enter__(self) -> BoundRuntimeArtifactPath:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def bind_runtime_artifact_path(
    path: Path,
    *,
    service_uid: int,
    service_gid: int,
) -> BoundRuntimeArtifactPath:
    """从 ``/`` 逐段打开并冻结数据根路径，不解析任何中间符号链接。"""
    _require_service_ids(service_uid, service_gid)
    names = _path_names(path)
    descriptors, identities = _open_chain(
        path,
        service_uid=service_uid,
        service_gid=service_gid,
    )
    return BoundRuntimeArtifactPath(
        path=path,
        service_uid=service_uid,
        service_gid=service_gid,
        _names=names,
        _descriptors=descriptors,
        _identities=identities,
    )


def require_descriptor_without_acl(descriptor: int) -> None:
    try:
        attributes = os.listxattr(descriptor)
        names = {
            item.decode("ascii", "strict") if isinstance(item, bytes) else str(item)
            for item in attributes
        }
    except (AttributeError, OSError, UnicodeError):
        raise RuntimeArtifactDescriptorPathError("运行观测产物 ACL 无法安全复验") from None
    if any("acl" in name.casefold() for name in names):
        raise RuntimeArtifactDescriptorPathError("运行观测产物禁止 ACL")


def _open_chain(
    path: Path,
    *,
    service_uid: int,
    service_gid: int,
) -> tuple[tuple[int, ...], tuple[_DirectoryIdentity, ...]]:
    names = _path_names(path)
    _require_platform_capabilities()
    descriptors: list[int] = []
    try:
        descriptors.append(os.open("/", _directory_flags()))
        for name in names:
            descriptors.append(os.open(name, _directory_flags(), dir_fd=descriptors[-1]))
        identities = _validate_open_chain(
            names,
            tuple(descriptors),
            service_uid=service_uid,
            service_gid=service_gid,
        )
        return tuple(descriptors), identities
    except RuntimeArtifactDescriptorPathError:
        _close_descriptors(tuple(descriptors))
        raise
    except OSError:
        _close_descriptors(tuple(descriptors))
        raise RuntimeArtifactDescriptorPathError("运行观测产物数据根路径无法安全打开") from None


def _validate_open_chain(
    names: tuple[str, ...],
    descriptors: tuple[int, ...],
    *,
    service_uid: int,
    service_gid: int,
) -> tuple[_DirectoryIdentity, ...]:
    if len(descriptors) != len(names) + 1:
        raise RuntimeArtifactDescriptorPathError("运行观测产物数据根 descriptor 链无效")
    identities = tuple(
        _validate_directory(
            descriptor,
            service_uid=service_uid,
            service_gid=service_gid,
            is_root=index == len(descriptors) - 1,
        )
        for index, descriptor in enumerate(descriptors)
    )
    for index, name in enumerate(names, start=1):
        linked = _stat_link(descriptors[index - 1], name)
        if linked != identities[index]:
            raise RuntimeArtifactDescriptorPathError("运行观测产物数据根身份复验失败")
    return identities


def _validate_directory(
    descriptor: int,
    *,
    service_uid: int,
    service_gid: int,
    is_root: bool,
) -> _DirectoryIdentity:
    try:
        metadata = os.fstat(descriptor)
    except OSError:
        raise RuntimeArtifactDescriptorPathError("运行观测产物数据根身份复验失败") from None
    identity = _directory_identity(metadata)
    required = _service_execute_bit(identity, service_uid, service_gid)
    if not identity.mode & required:
        suffix = "" if is_root else "祖先"
        raise RuntimeArtifactDescriptorPathError(f"运行观测产物服务用户无法穿越数据根{suffix}")
    require_descriptor_without_acl(descriptor)
    return identity


def _stat_link(parent_fd: int, name: str) -> _DirectoryIdentity:
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        raise RuntimeArtifactDescriptorPathError("运行观测产物数据根身份复验失败") from None
    return _directory_identity(metadata)


def _directory_identity(metadata: os.stat_result) -> _DirectoryIdentity:
    mode = int(metadata.st_mode)
    if not stat.S_ISDIR(mode):
        raise RuntimeArtifactDescriptorPathError("运行观测产物数据根路径不安全：包含非目录")
    return _DirectoryIdentity(
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        kind=stat.S_IFMT(mode),
        uid=int(metadata.st_uid),
        gid=int(metadata.st_gid),
        mode=stat.S_IMODE(mode),
    )


def _service_execute_bit(
    identity: _DirectoryIdentity,
    service_uid: int,
    service_gid: int,
) -> int:
    if identity.uid == service_uid:
        return stat.S_IXUSR
    if identity.gid == service_gid:
        return stat.S_IXGRP
    return stat.S_IXOTH


def _path_names(path: Path) -> tuple[str, ...]:
    if not isinstance(path, Path) or not path.is_absolute() or path.anchor != "/":
        raise RuntimeArtifactDescriptorPathError("运行观测产物数据根词法路径无效")
    names = tuple(str(part) for part in path.parts[1:])
    if not names or any(not name or name in {".", ".."} or "/" in name for name in names):
        raise RuntimeArtifactDescriptorPathError("运行观测产物数据根词法路径无效")
    return names


def _require_platform_capabilities() -> None:
    if os.open not in getattr(os, "supports_dir_fd", frozenset()):
        raise RuntimeArtifactDescriptorPathError("当前平台无法安全绑定运行观测产物数据根")
    if os.stat not in getattr(os, "supports_dir_fd", frozenset()):
        raise RuntimeArtifactDescriptorPathError("当前平台无法安全绑定运行观测产物数据根")
    if os.stat not in getattr(os, "supports_follow_symlinks", frozenset()):
        raise RuntimeArtifactDescriptorPathError("当前平台无法安全绑定运行观测产物数据根")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | _required_flag("O_DIRECTORY")
        | _required_flag("O_NOFOLLOW")
        | _required_flag("O_CLOEXEC")
    )


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise RuntimeArtifactDescriptorPathError("当前平台无法安全绑定运行观测产物数据根")
    return value


def _require_service_ids(service_uid: int, service_gid: int) -> None:
    if any(
        type(value) is not int or not 0 < value < (1 << 32) - 1
        for value in (service_uid, service_gid)
    ):
        raise RuntimeArtifactDescriptorPathError("运行观测产物服务身份无效")


def _close_descriptors(descriptors: tuple[int, ...]) -> None:
    for descriptor in reversed(descriptors):
        try:
            os.close(descriptor)
        except OSError:
            pass


__all__ = [
    "BoundRuntimeArtifactPath",
    "RuntimeArtifactDescriptorPathError",
    "bind_runtime_artifact_path",
    "require_descriptor_without_acl",
]
