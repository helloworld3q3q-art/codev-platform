"""运行时代际 descriptor-safe 受管文件原语测试。"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform._runtime_managed_file_bound as managed
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    ManagedFileIdentityError,
    ManagedFilePolicy,
    create_managed_bytes_exclusive,
    fsync_managed_directory,
    open_managed_regular_descriptor,
    read_managed_bytes,
    write_managed_bytes_atomic,
)


_POSIX = os.name == "posix"


def _policy(
    *, uid: int | None = None, mode: int = 0o600, max_bytes: int = 128
) -> ManagedFilePolicy:
    return ManagedFilePolicy(mode=mode, require_uid=uid, max_bytes=max_bytes)


def test_policy_rejects_unsafe_or_ambiguous_values() -> None:
    for mode in (-1, 0o000, 0o200, 0o1000, 0o620, True):
        with pytest.raises(ManagedFileError, match="权限"):
            ManagedFilePolicy(mode=mode, require_uid=None, max_bytes=128)  # type: ignore[arg-type]

    for uid in (-1, True, "0"):
        with pytest.raises(ManagedFileError, match="属主"):
            ManagedFilePolicy(mode=0o600, require_uid=uid, max_bytes=128)  # type: ignore[arg-type]

    for gid in (-1, True, "0"):
        with pytest.raises(ManagedFileError, match="属组"):
            ManagedFilePolicy(
                mode=0o600,
                require_uid=None,
                require_gid=gid,
                max_bytes=128,
            )  # type: ignore[arg-type]

    for max_bytes in (0, -1, True):
        with pytest.raises(ManagedFileError, match="上限"):
            ManagedFilePolicy(mode=0o600, require_uid=None, max_bytes=max_bytes)  # type: ignore[arg-type]


def test受管文件策略拒绝gid漂移() -> None:
    """显式声明 gid 时，叶子元数据必须同时满足 uid 与 gid。"""
    policy = ManagedFilePolicy(
        mode=0o600,
        require_uid=1000,
        require_gid=1000,
        max_bytes=128,
    )
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_uid=1000,
        st_gid=1001,
        st_size=1,
        st_nlink=1,
    )

    with pytest.raises(ManagedFileIdentityError, match="组"):
        managed._require_managed_regular(metadata, policy)


def test未显式声明gid时保持既有兼容行为() -> None:
    """旧调用点未传 require_gid 时，不应因叶子属组差异被误拒绝。"""
    policy = ManagedFilePolicy(mode=0o600, require_uid=1000, max_bytes=128)
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_uid=1000,
        st_gid=1001,
        st_size=1,
        st_nlink=1,
    )

    managed._require_managed_regular(metadata, policy)


def test受管文件稳定身份纳入gid() -> None:
    """读取前后即使 gid 恢复，稳定性比较也必须看见中间属组变化。"""
    common = {
        "st_dev": 1,
        "st_ino": 2,
        "st_size": 3,
        "st_mode": stat.S_IFREG | 0o600,
        "st_uid": 1000,
        "st_mtime_ns": 4,
        "st_ctime_ns": 5,
    }
    before = SimpleNamespace(**common, st_gid=1000)
    after = SimpleNamespace(**common, st_gid=1001)

    assert managed._file_stability_identity(before) != managed._file_stability_identity(after)


@pytest.mark.skipif(not _POSIX, reason="受管 fd 写入仅在 WSL/Linux 验证")
def test受管写入将显式gid传递给fchown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 gid 必须在同一次 fd chown 中写入，不能仅停留在读取策略。"""
    target = tmp_path / "payload.bin"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        managed,
        "_fchown",
        lambda _descriptor, uid, gid: calls.append((uid, gid)),
    )
    policy = ManagedFilePolicy(
        mode=0o600,
        require_uid=1000,
        require_gid=1001,
        max_bytes=128,
    )
    try:
        managed._write_managed_descriptor(descriptor, b"payload", policy)
    finally:
        os.close(descriptor)

    assert target.read_bytes() == b"payload"
    assert calls == [(1000, 1001)]


def test_path_escape_is_rejected_before_any_file_operation(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    escaped = root / ".." / "outside" / "state.json"

    with pytest.raises(ManagedFileError, match="逃逸"):
        read_managed_bytes(escaped, root=root, policy=_policy())


def test_nul_path_is_rejected_before_publishing_any_leaf(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "attempt.json\0suffix"

    with pytest.raises(ManagedFileError, match="路径"):
        create_managed_bytes_exclusive(target, b"payload", root=root, policy=_policy())
    with pytest.raises(ManagedFileError, match="路径"):
        fsync_managed_directory(Path(f"{root}\0suffix"))

    assert tuple(root.iterdir()) == ()


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
@pytest.mark.parametrize("root_kind", ("missing", "symlink"))
def test_public_operations_map_unsafe_root_to_managed_error(
    tmp_path: Path,
    root_kind: str,
) -> None:
    root = tmp_path / "runtime"
    if root_kind == "symlink":
        outside = tmp_path / "outside"
        outside.mkdir()
        root.symlink_to(outside, target_is_directory=True)
    target = root / "state.json"
    policy = _policy(uid=os.geteuid())
    operations = (
        lambda: read_managed_bytes(target, root=root, policy=policy),
        lambda: write_managed_bytes_atomic(target, b"state", root=root, policy=policy),
        lambda: create_managed_bytes_exclusive(target, b"state", root=root, policy=policy),
        lambda: fsync_managed_directory(root),
        lambda: open_managed_regular_descriptor(
            target,
            root=root,
            mode=0o600,
            read_only=False,
        ),
    )

    for operation in operations:
        with pytest.raises(ManagedFileError):
            operation()


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_read_rejects_symlink_chain_and_non_regular_leaf(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "state.json").write_bytes(b"outside")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ManagedFileError, match="符号链接|安全打开"):
        read_managed_bytes(root / "linked" / "state.json", root=root, policy=_policy())

    (root / "directory-leaf").mkdir()
    with pytest.raises(ManagedFileError, match="普通文件"):
        read_managed_bytes(root / "directory-leaf", root=root, policy=_policy())


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_read_rejects_mode_owner_and_size_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "state.json"
    target.write_bytes(b"payload")
    target.chmod(0o640)

    with pytest.raises(ManagedFileError, match="权限"):
        read_managed_bytes(target, root=root, policy=_policy(uid=os.geteuid()))

    target.chmod(0o600)
    original_fstat = managed._fstat

    def report_wrong_leaf_owner(descriptor: int) -> object:
        metadata = original_fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return metadata
        return SimpleNamespace(
            st_mode=metadata.st_mode,
            st_uid=os.geteuid() + 1,
            st_size=metadata.st_size,
            st_nlink=metadata.st_nlink,
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns,
        )

    with monkeypatch.context() as owner_patch:
        owner_patch.setattr(managed, "_fstat", report_wrong_leaf_owner)
        with pytest.raises(ManagedFileError, match="属主"):
            read_managed_bytes(target, root=root, policy=_policy(uid=os.geteuid()))

    with pytest.raises(ManagedFileError, match="上限"):
        read_managed_bytes(target, root=root, policy=_policy(max_bytes=3))


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_atomic_replace_fsyncs_file_and_parent_and_returns_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "generation-state.json"
    calls: list[str] = []
    original_fsync = managed._fsync

    def record_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        calls.append("directory" if stat.S_ISDIR(mode) else "file")
        original_fsync(descriptor)

    monkeypatch.setattr(managed, "_fsync", record_fsync)
    evidence = write_managed_bytes_atomic(
        target,
        b'{"state":1}\n',
        root=root,
        policy=_policy(uid=os.geteuid(), mode=0o640),
    )

    assert target.read_bytes() == b'{"state":1}\n'
    assert evidence.path == "generation-state.json"
    assert evidence.size == len(b'{"state":1}\n')
    assert evidence.mode == 0o640
    assert evidence.uid == os.geteuid()
    assert len(evidence.sha256) == 64
    assert "file" in calls
    assert "directory" in calls


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_temporary_file_is_private_until_payload_is_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "public-state.json"
    initial_modes: list[int] = []
    original_write = managed._write_all

    def inspect_before_write(descriptor: int, payload: bytes) -> None:
        initial_modes.append(stat.S_IMODE(os.fstat(descriptor).st_mode))
        original_write(descriptor, payload)

    monkeypatch.setattr(managed, "_write_all", inspect_before_write)
    write_managed_bytes_atomic(
        target,
        b"public-after-complete",
        root=root,
        policy=_policy(uid=os.geteuid(), mode=0o644),
    )

    assert initial_modes == [0o600]
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_atomic_replace_existing_regular_file_uses_complete_new_inode(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "generation-state.json"
    target.write_bytes(b"old-state")
    target.chmod(0o600)
    original_inode = target.stat().st_ino

    evidence = write_managed_bytes_atomic(
        target,
        b"new-state",
        root=root,
        policy=_policy(uid=os.geteuid()),
    )

    assert target.read_bytes() == b"new-state"
    assert target.stat().st_ino != original_inode
    assert evidence.size == len(b"new-state")
    assert not tuple(root.glob(".codev-reindex-*.tmp"))


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_atomic_replace_detects_digest_drift_after_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "state.json"
    original_replace = managed._replace_at

    def replace_then_corrupt(
        source: str,
        destination: str,
        source_parent: int,
        destination_parent: int,
    ) -> None:
        original_replace(source, destination, source_parent, destination_parent)
        descriptor = os.open(destination, os.O_WRONLY | os.O_TRUNC, dir_fd=destination_parent)
        try:
            os.write(descriptor, b"drift")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    monkeypatch.setattr(managed, "_replace_at", replace_then_corrupt)

    with pytest.raises(ManagedFileError, match="摘要漂移"):
        write_managed_bytes_atomic(
            target,
            b"expected",
            root=root,
            policy=_policy(uid=os.geteuid()),
        )


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_atomic_replace_rejects_existing_symlink_without_touching_target(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    target = root / "state.json"
    target.symlink_to(outside)

    with pytest.raises(ManagedFileError, match="符号链接|安全普通文件"):
        write_managed_bytes_atomic(
            target,
            b"replacement",
            root=root,
            policy=_policy(uid=os.geteuid()),
        )

    assert target.is_symlink()
    assert outside.read_bytes() == b"outside"


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_exclusive_create_never_overwrites_existing_file(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "attempt.json"

    evidence = create_managed_bytes_exclusive(
        target,
        b"first",
        root=root,
        policy=_policy(uid=os.geteuid()),
    )
    assert evidence.size == 5

    with pytest.raises(FileExistsError):
        create_managed_bytes_exclusive(
            target,
            b"second",
            root=root,
            policy=_policy(uid=os.geteuid()),
        )
    assert target.read_bytes() == b"first"


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_exclusive_create_publishes_only_after_complete_file_is_durable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "attempt.json"
    payload = b"complete-at-publication"
    original_publish = managed._rename_noreplace_at
    original_fsync = managed._fsync
    observed = False
    durable_inodes: set[tuple[int, int]] = set()

    def record_fsync(descriptor: int) -> None:
        original_fsync(descriptor)
        metadata = os.fstat(descriptor)
        if stat.S_ISREG(metadata.st_mode):
            durable_inodes.add((metadata.st_dev, metadata.st_ino))

    def inspect_then_publish(
        source: str,
        destination: str,
        source_parent: int,
        destination_parent: int,
    ) -> None:
        nonlocal observed
        with pytest.raises(FileNotFoundError):
            os.open(destination, os.O_RDONLY, dir_fd=destination_parent)
        descriptor = os.open(source, os.O_RDONLY, dir_fd=source_parent)
        try:
            assert os.read(descriptor, len(payload) + 1) == payload
            metadata = os.fstat(descriptor)
            assert (metadata.st_dev, metadata.st_ino) in durable_inodes
        finally:
            os.close(descriptor)
        observed = True
        original_publish(source, destination, source_parent, destination_parent)

    monkeypatch.setattr(managed, "_fsync", record_fsync)
    monkeypatch.setattr(managed, "_rename_noreplace_at", inspect_then_publish)

    evidence = create_managed_bytes_exclusive(
        target,
        payload,
        root=root,
        policy=_policy(uid=os.geteuid()),
    )

    assert observed
    assert evidence.size == len(payload)
    assert target.read_bytes() == payload
    assert not tuple(root.glob(".codev-reindex-*.tmp"))


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_exclusive_create_conflict_preserves_competing_leaf_and_only_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "attempt.json"
    cleaned: list[str] = []
    original_cleanup = managed._unlink_temporary_quietly

    def publish_competing_leaf(
        _source: str,
        destination: str,
        _source_parent: int,
        destination_parent: int,
    ) -> None:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=destination_parent,
        )
        try:
            os.write(descriptor, b"competitor")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        raise FileExistsError("并发发布已占用最终名称")

    def record_cleanup(name: str, parent_descriptor: int) -> None:
        cleaned.append(name)
        original_cleanup(name, parent_descriptor)

    monkeypatch.setattr(managed, "_rename_noreplace_at", publish_competing_leaf)
    monkeypatch.setattr(managed, "_unlink_temporary_quietly", record_cleanup)

    with pytest.raises(FileExistsError):
        create_managed_bytes_exclusive(
            target,
            b"ours",
            root=root,
            policy=_policy(uid=os.geteuid()),
        )

    assert target.read_bytes() == b"competitor"
    assert cleaned
    assert "attempt.json" not in cleaned


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_exclusive_create_parent_fsync_failure_keeps_complete_final_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "attempt.json"
    published = False
    original_publish = managed._rename_noreplace_at
    original_fsync = managed._fsync

    def record_publish(
        source: str,
        destination: str,
        source_parent: int,
        destination_parent: int,
    ) -> None:
        nonlocal published
        original_publish(source, destination, source_parent, destination_parent)
        published = True

    def fail_published_parent_fsync(descriptor: int) -> None:
        if published and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("注入父目录 fsync 失败")
        original_fsync(descriptor)

    monkeypatch.setattr(managed, "_rename_noreplace_at", record_publish)
    monkeypatch.setattr(managed, "_fsync", fail_published_parent_fsync)

    with pytest.raises(ManagedFileError, match="独占创建"):
        create_managed_bytes_exclusive(
            target,
            b"complete",
            root=root,
            policy=_policy(uid=os.geteuid()),
        )

    assert target.read_bytes() == b"complete"
    assert not tuple(root.glob(".codev-reindex-*.tmp"))


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_exclusive_create_write_failure_never_reserves_final_leaf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "attempt.json"

    def write_partial_then_fail(descriptor: int, _payload: bytes) -> None:
        os.write(descriptor, b"partial")
        raise OSError("注入临时文件写入失败")

    monkeypatch.setattr(managed, "_write_all", write_partial_then_fail)

    with pytest.raises(ManagedFileError, match="独占创建"):
        create_managed_bytes_exclusive(
            target,
            b"complete",
            root=root,
            policy=_policy(uid=os.geteuid()),
        )

    assert not target.exists()
    assert not tuple(root.glob(".codev-reindex-*.tmp"))


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_read_returns_bytes_from_the_verified_descriptor(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "manifest.json"
    target.write_bytes(b"verified")
    target.chmod(0o600)

    assert (
        read_managed_bytes(
            target,
            root=root,
            policy=_policy(uid=os.geteuid()),
        )
        == b"verified"
    )
