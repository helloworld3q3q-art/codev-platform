from __future__ import annotations

import dataclasses
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform import _runtime_artifact_descriptor_path as descriptor_path
from codev_platform import runtime_artifact_access
from codev_platform.core.runtime_artifacts import RuntimeArtifactLayout
from codev_platform.runtime_artifact_access import (
    RuntimeArtifactAccessError,
    migrate_runtime_artifact_access,
)


_LINUX_ROOT = sys.platform.startswith("linux") and getattr(os, "geteuid", lambda: -1)() == 0
_SERVICE_UID = 12345
_SERVICE_GID = 23456
_NAMESPACES = ("logs", "audit", "mcp_serve_logs", "platform_meta", "run")


def _layout(tmp_path: Path) -> RuntimeArtifactLayout:
    for directory in (tmp_path, *tmp_path.parents):
        if directory == Path("/tmp"):
            break
        os.chmod(directory, 0o755)
    root = tmp_path / "data"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    return RuntimeArtifactLayout(root)


def _migrate(
    layout: RuntimeArtifactLayout,
    *,
    max_entries: int = 100,
):
    return migrate_runtime_artifact_access(
        layout,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        max_entries=max_entries,
    )


def test非Linux环境在访问文件系统前失败关闭(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_artifact_access.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime_artifact_access.os,
        "geteuid",
        lambda: 0,
        raising=False,
    )

    with pytest.raises(RuntimeArtifactAccessError, match="Linux root"):
        _migrate(RuntimeArtifactLayout(tmp_path / "missing"))

    assert not (tmp_path / "missing").exists()


def test非root身份在访问文件系统前失败关闭(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_artifact_access.sys, "platform", "linux")
    monkeypatch.setattr(
        runtime_artifact_access.os,
        "geteuid",
        lambda: 1000,
        raising=False,
    )

    with pytest.raises(RuntimeArtifactAccessError, match="Linux root"):
        _migrate(RuntimeArtifactLayout(tmp_path / "missing"))

    assert not (tmp_path / "missing").exists()


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test缺失命名空间被创建且旧树收敛后保持幂等(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    logs = layout.root / "logs"
    nested = logs / "nested"
    nested.mkdir(parents=True, mode=0o755)
    root_log = logs / "root.log"
    service_log = nested / "service.log"
    root_log.write_text("root\n", encoding="utf-8")
    service_log.write_text("service\n", encoding="utf-8")
    os.chmod(root_log, 0o644)
    os.chmod(service_log, 0o600)
    os.chown(nested, _SERVICE_UID, _SERVICE_GID)
    os.chown(service_log, _SERVICE_UID, _SERVICE_GID)

    first = _migrate(layout)
    second = _migrate(layout)

    assert first.schema_version == 1
    assert first.namespace_count == len(_NAMESPACES)
    assert first.entry_count == len(_NAMESPACES) + 3
    assert first.changed_entry_count > 0
    assert second.changed_entry_count == 0
    assert second.evidence_sha256 == first.evidence_sha256
    assert len(first.evidence_sha256) == 64
    int(first.evidence_sha256, 16)
    payload = dataclasses.asdict(first)
    assert not any(isinstance(value, Path) for value in payload.values())
    assert not any(name in repr(payload) for name in _NAMESPACES)

    for namespace in _NAMESPACES:
        namespace_root = layout.root / namespace
        assert namespace_root.is_dir()
        for current, directories, files in os.walk(namespace_root):
            current_path = Path(current)
            current_stat = current_path.stat()
            assert (current_stat.st_uid, current_stat.st_gid) == (
                _SERVICE_UID,
                _SERVICE_GID,
            )
            assert stat.S_IMODE(current_stat.st_mode) == 0o700
            for name in directories:
                child = current_path / name
                child_stat = child.stat()
                assert (child_stat.st_uid, child_stat.st_gid) == (
                    _SERVICE_UID,
                    _SERVICE_GID,
                )
                assert stat.S_IMODE(child_stat.st_mode) == 0o700
            for name in files:
                child = current_path / name
                child_stat = child.stat()
                assert (child_stat.st_uid, child_stat.st_gid) == (
                    _SERVICE_UID,
                    _SERVICE_GID,
                )
                assert stat.S_IMODE(child_stat.st_mode) == 0o600


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test目标服务用户无法穿越数据根时在创建前失败关闭(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    os.chmod(layout.root, 0o700)

    with pytest.raises(RuntimeArtifactAccessError, match="服务用户无法穿越数据根"):
        _migrate(layout)

    assert not any((layout.root / name).exists() for name in _NAMESPACES)
    assert layout.root.stat().st_uid == 0
    assert stat.S_IMODE(layout.root.stat().st_mode) == 0o700


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test目标服务用户无法穿越数据根祖先时在创建前失败关闭(tmp_path: Path) -> None:
    for directory in (tmp_path, *tmp_path.parents):
        if directory == Path("/tmp"):
            break
        os.chmod(directory, 0o755)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    root = private / "data"
    root.mkdir(mode=0o755)
    layout = RuntimeArtifactLayout(root)

    with pytest.raises(RuntimeArtifactAccessError, match="服务用户无法穿越数据根祖先"):
        _migrate(layout)

    assert not any((layout.root / name).exists() for name in _NAMESPACES)
    assert stat.S_IMODE(private.stat().st_mode) == 0o700


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test数据根祖先ACL在创建命名空间前失败关闭(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _layout(tmp_path)
    ancestor_inode = layout.root.parent.stat().st_ino
    real_listxattr = os.listxattr

    def listxattr_with_acl(descriptor: int) -> list[str]:
        if os.fstat(descriptor).st_ino == ancestor_inode:
            return ["system.posix_acl_access"]
        return list(real_listxattr(descriptor))

    monkeypatch.setattr(runtime_artifact_access.os, "listxattr", listxattr_with_acl)

    with pytest.raises(RuntimeArtifactAccessError, match="ACL"):
        _migrate(layout)

    assert not any((layout.root / name).exists() for name in _NAMESPACES)


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test祖先在校验后被替换时不跟随替代目录且不出具证明(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for directory in (tmp_path, *tmp_path.parents):
        if directory == Path("/tmp"):
            break
        os.chmod(directory, 0o755)
    live = tmp_path / "live"
    live.mkdir(mode=0o755)
    os.chown(live, _SERVICE_UID, _SERVICE_GID)
    root = live / "data"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    alternate = tmp_path / "alternate"
    alternate_root = alternate / "data"
    alternate_logs = alternate_root / "logs"
    alternate_logs.mkdir(parents=True, mode=0o755)
    victim = alternate_logs / "victim.log"
    victim.write_text("root-owned\n", encoding="utf-8")
    os.chmod(victim, 0o644)
    parked = tmp_path / "parked"
    real_open = os.open
    real_lstat = Path.lstat
    swapped = False

    def swap_ancestor() -> None:
        nonlocal swapped
        live.rename(parked)
        live.symlink_to(alternate, target_is_directory=True)
        swapped = True

    def racing_lstat(path: Path, *args, **kwargs):
        if not swapped and path == root:
            swap_ancestor()
        return real_lstat(path, *args, **kwargs)

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        text = os.fsdecode(path)
        if not swapped and dir_fd is None and Path(text) == root:
            swap_ancestor()
        if not swapped and dir_fd is not None and text == live.name:
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            swap_ancestor()
            return descriptor
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    dir_fd_support = set(os.supports_dir_fd)
    dir_fd_support.discard(real_open)
    dir_fd_support.add(racing_open)
    monkeypatch.setattr(Path, "lstat", racing_lstat)
    monkeypatch.setattr(runtime_artifact_access.os, "supports_dir_fd", dir_fd_support)
    monkeypatch.setattr(runtime_artifact_access.os, "open", racing_open)

    with pytest.raises(RuntimeArtifactAccessError, match="身份|祖先|安全"):
        _migrate(RuntimeArtifactLayout(root))

    metadata = victim.stat()
    assert (metadata.st_uid, metadata.st_gid) == (0, 0)
    assert stat.S_IMODE(metadata.st_mode) == 0o644


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test符号链接命名空间被拒绝且外部对象不变(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    (layout.root / "logs").symlink_to(external, target_is_directory=True)

    with pytest.raises(RuntimeArtifactAccessError, match="符号链接"):
        _migrate(layout)

    assert stat.S_IMODE(external.stat().st_mode) == 0o755
    assert external.stat().st_uid == 0


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test普通文件硬链接被拒绝且预检不修改权限(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    logs = layout.root / "logs"
    logs.mkdir(mode=0o755)
    first = logs / "first.log"
    second = logs / "second.log"
    first.write_text("same\n", encoding="utf-8")
    os.chmod(first, 0o644)
    os.link(first, second)

    with pytest.raises(RuntimeArtifactAccessError, match="硬链接"):
        _migrate(layout)

    assert first.stat().st_uid == 0
    assert stat.S_IMODE(first.stat().st_mode) == 0o644


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
@pytest.mark.parametrize("unsafe_mode", [0o660, 0o606])
def test组或全局可写对象被拒绝且不自动修复(
    tmp_path: Path,
    unsafe_mode: int,
) -> None:
    layout = _layout(tmp_path)
    logs = layout.root / "logs"
    logs.mkdir(mode=0o755)
    payload = logs / "unsafe.log"
    payload.write_text("unsafe\n", encoding="utf-8")
    os.chmod(payload, unsafe_mode)

    with pytest.raises(RuntimeArtifactAccessError, match="组或全局可写"):
        _migrate(layout)

    assert stat.S_IMODE(payload.stat().st_mode) == unsafe_mode


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test未知所有者对象被拒绝(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    logs = layout.root / "logs"
    logs.mkdir(mode=0o755)
    payload = logs / "foreign.log"
    payload.write_text("foreign\n", encoding="utf-8")
    os.chown(payload, 54321, 0)

    with pytest.raises(RuntimeArtifactAccessError, match="所有者"):
        _migrate(layout)

    assert payload.stat().st_uid == 54321


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
@pytest.mark.parametrize(
    "acl_name",
    ["system.posix_acl_access", "system.nfs4_acl", "security.NTACL"],
)
def testACL被拒绝(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    acl_name: str,
) -> None:
    layout = _layout(tmp_path)
    target_inode = layout.root.stat().st_ino
    real_listxattr = os.listxattr

    def listxattr_with_acl(descriptor: int) -> list[str]:
        if os.fstat(descriptor).st_ino == target_inode:
            return [acl_name]
        return list(real_listxattr(descriptor))

    monkeypatch.setattr(runtime_artifact_access.os, "listxattr", listxattr_with_acl)

    with pytest.raises(RuntimeArtifactAccessError, match="ACL"):
        _migrate(layout)


def test跨设备元数据被拒绝() -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=8,
        st_ino=9,
        st_uid=0,
        st_gid=0,
        st_nlink=1,
        st_size=0,
        st_mtime_ns=0,
        st_ctime_ns=0,
    )
    policy = runtime_artifact_access._AccessPolicy(
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        max_entries=10,
    )

    with pytest.raises(RuntimeArtifactAccessError, match="跨设备"):
        runtime_artifact_access._entry_identity(
            metadata,
            root_device=7,
            policy=policy,
        )


def test关闭后不再暴露数据根设备号(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = descriptor_path._DirectoryIdentity(
        device=7,
        inode=8,
        kind=stat.S_IFDIR,
        uid=0,
        gid=0,
        mode=0o755,
    )
    binding = descriptor_path.BoundRuntimeArtifactPath(
        path=Path("/srv/codev-platform/data"),
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        _names=("srv", "codev-platform", "data"),
        _descriptors=(11,),
        _identities=(identity,),
    )
    monkeypatch.setattr(descriptor_path, "_close_descriptors", lambda _items: None)

    binding.close()

    with pytest.raises(descriptor_path.RuntimeArtifactDescriptorPathError, match="已关闭"):
        _ = binding.root_device


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test条目预算在所有权变更前失败关闭(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    with pytest.raises(RuntimeArtifactAccessError, match="条目预算"):
        _migrate(layout, max_entries=len(_NAMESPACES) - 1)

    for namespace in _NAMESPACES:
        metadata = (layout.root / namespace).stat()
        assert metadata.st_uid == 0
        assert stat.S_IMODE(metadata.st_mode) == 0o700


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test两遍预检拒绝inode漂移且不发布(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    logs = layout.root / "logs"
    logs.mkdir(mode=0o755)
    payload = logs / "active.log"
    payload.write_text("same\n", encoding="utf-8")
    os.chmod(payload, 0o644)
    real_scan = runtime_artifact_access._scan_namespaces
    calls = 0

    def scan_with_drift(*args, **kwargs):
        nonlocal calls
        result = real_scan(*args, **kwargs)
        calls += 1
        if calls == 1:
            replacement = logs / "replacement"
            replacement.write_text("same\n", encoding="utf-8")
            os.chmod(replacement, 0o644)
            os.replace(replacement, payload)
        return result

    monkeypatch.setattr(runtime_artifact_access, "_scan_namespaces", scan_with_drift)

    with pytest.raises(RuntimeArtifactAccessError, match="两遍预检"):
        _migrate(layout)

    assert payload.stat().st_uid == 0
    assert stat.S_IMODE(payload.stat().st_mode) == 0o644


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test发布前再次复验inode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = _layout(tmp_path)
    logs = layout.root / "logs"
    logs.mkdir(mode=0o755)
    payload = logs / "active.log"
    payload.write_text("same\n", encoding="utf-8")
    os.chmod(payload, 0o644)
    real_scan = runtime_artifact_access._scan_namespaces
    calls = 0

    def scan_then_drift(*args, **kwargs):
        nonlocal calls
        result = real_scan(*args, **kwargs)
        calls += 1
        if calls == 2:
            replacement = logs / "replacement"
            replacement.write_text("same\n", encoding="utf-8")
            os.chmod(replacement, 0o644)
            os.replace(replacement, payload)
        return result

    monkeypatch.setattr(runtime_artifact_access, "_scan_namespaces", scan_then_drift)

    with pytest.raises(RuntimeArtifactAccessError, match="身份复验"):
        _migrate(layout)

    assert payload.stat().st_uid == 0
    assert stat.S_IMODE(payload.stat().st_mode) == 0o644


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test返回证明前复验数据根仍绑定原inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _layout(tmp_path)
    original = layout.root
    detached = tmp_path / "detached"
    real_scan = runtime_artifact_access._scan_namespaces
    calls = 0

    def scan_then_replace_root(*args, **kwargs):
        nonlocal calls
        result = real_scan(*args, **kwargs)
        calls += 1
        if calls == 2:
            original.rename(detached)
            original.mkdir(mode=0o755)
            os.chmod(original, 0o755)
        return result

    monkeypatch.setattr(
        runtime_artifact_access,
        "_scan_namespaces",
        scan_then_replace_root,
    )

    with pytest.raises(RuntimeArtifactAccessError, match="数据根身份复验"):
        _migrate(layout)

    assert original.stat().st_uid == 0
    assert detached.stat().st_uid == 0


@pytest.mark.skipif(not _LINUX_ROOT, reason="权限迁移集成要求 Linux root")
def test摘要计算期间替换数据根时不出具证明(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _layout(tmp_path)
    original = layout.root
    detached = tmp_path / "detached-during-digest"
    real_digest = runtime_artifact_access._evidence_digest

    def digest_then_replace_root(*args, **kwargs) -> str:
        digest = real_digest(*args, **kwargs)
        original.rename(detached)
        original.mkdir(mode=0o755)
        os.chmod(original, 0o755)
        return digest

    monkeypatch.setattr(
        runtime_artifact_access,
        "_evidence_digest",
        digest_then_replace_root,
    )

    with pytest.raises(RuntimeArtifactAccessError, match="数据根身份复验"):
        _migrate(layout)

    assert original.stat().st_uid == 0
    assert detached.stat().st_uid == 0
