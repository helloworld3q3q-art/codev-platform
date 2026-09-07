from __future__ import annotations

import os
import stat
import sys
from types import SimpleNamespace

import pytest

from codev_platform import runtime_fd_tree
from codev_platform.runtime_fd_tree import (
    CanonicalizeMode,
    ConvergeCompletedAccessRepair,
    PreflightGroup,
    PreflightCompletedAccessRepair,
    PublishGroup,
    RuntimeFdGroupPolicy,
    RuntimeFdModePolicy,
    RuntimeFdTreeSnapshot,
    RuntimeFdTreeError,
    RuntimeRootMarkerPolicy,
    VerifyGroup,
    VerifyOnly,
    walk_runtime_tree,
)


def _metadata(
    kind: int,
    mode: int,
    *,
    device: int = 7,
    owner: int = 0,
    group: int = 0,
    links: int = 1,
    size: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        st_mode=kind | mode,
        st_dev=device,
        st_ino=11,
        st_uid=owner,
        st_gid=group,
        st_nlink=links,
        st_size=size,
        st_atime_ns=12,
        st_mtime_ns=13,
        st_ctime_ns=17,
    )


def _mode_policy() -> RuntimeFdModePolicy:
    return RuntimeFdModePolicy(
        directory_mode=0o750,
        regular_mode=0o640,
        executable_mode=0o750,
        executable_mask=stat.S_IXUSR,
    )


def _marker_policy() -> RuntimeRootMarkerPolicy:
    return RuntimeRootMarkerPolicy(
        name=".incomplete",
        trusted_contents=(b"after_install\n",),
        max_bytes=64,
    )


def _group_policy(target_gid: int = 1234) -> RuntimeFdGroupPolicy:
    return RuntimeFdGroupPolicy(target_gid=target_gid)


def _snapshot() -> RuntimeFdTreeSnapshot:
    return runtime_fd_tree._new_identity_snapshot(
        identity_digest=b"x" * 32,
        entries=1,
        total_bytes=0,
        root_device=7,
        root_inode=11,
    )


def test_mode_policy只按声明式owner执行位映射() -> None:
    policy = _mode_policy()

    assert policy.canonical_mode(stat.S_IFDIR | 0o700) == 0o750
    assert policy.canonical_mode(stat.S_IFREG | 0o600) == 0o640
    assert policy.canonical_mode(stat.S_IFREG | 0o610) == 0o640
    assert policy.canonical_mode(stat.S_IFREG | 0o700) == 0o750


def test_operation是封闭声明式数据而非高权限回调() -> None:
    policy = _mode_policy()
    marker = _marker_policy()

    verify = VerifyOnly(mode_policy=policy, marker_policy=marker)
    seal = CanonicalizeMode(mode_policy=policy, marker_policy=marker)

    assert verify.require_marker is False
    assert seal.require_marker is True
    assert not hasattr(verify, "callback")
    assert not hasattr(seal, "callback")
    assert not hasattr(verify, "descriptor")
    assert not hasattr(seal, "descriptor")


def test_gid操作同样是封闭声明式数据并禁止未完成对象() -> None:
    policy = _mode_policy()
    marker = _marker_policy()
    group = _group_policy()

    operations = (
        PreflightGroup(mode_policy=policy, marker_policy=marker, group_policy=group),
        PublishGroup(
            mode_policy=policy,
            marker_policy=marker,
            group_policy=group,
            expected_snapshot=_snapshot(),
        ),
        VerifyGroup(
            mode_policy=policy,
            marker_policy=marker,
            group_policy=group,
            expected_snapshot=_snapshot(),
        ),
    )

    assert all(operation.forbid_marker for operation in operations)
    assert all(not hasattr(operation, "callback") for operation in operations)
    assert all(not hasattr(operation, "descriptor") for operation in operations)


def test_gid策略只接受严格正整数() -> None:
    for value in (0, -1, True, 1.5, 1 << 32):
        with pytest.raises((TypeError, ValueError), match="GID"):
            RuntimeFdGroupPolicy(target_gid=value)  # type: ignore[arg-type]


def test_gid预检只接受root或目标组且符号链接必须保持root组() -> None:
    operation = PreflightGroup(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
        group_policy=_group_policy(),
    )

    runtime_fd_tree._validate_group_metadata(
        _metadata(stat.S_IFREG, 0o640, group=0), operation, require_target=False
    )
    runtime_fd_tree._validate_group_metadata(
        _metadata(stat.S_IFREG, 0o640, group=1234), operation, require_target=False
    )
    with pytest.raises(RuntimeFdTreeError, match="服务组"):
        runtime_fd_tree._validate_group_metadata(
            _metadata(stat.S_IFREG, 0o640, group=4321),
            operation,
            require_target=False,
        )
    with pytest.raises(RuntimeFdTreeError, match="符号链接"):
        runtime_fd_tree._validate_group_metadata(
            _metadata(stat.S_IFLNK, 0o777, group=1234),
            operation,
            require_target=False,
        )


def test_gid发布只允许gid与ctime变化并强制持久化(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _metadata(stat.S_IFREG, 0o640, group=0)
    after = _metadata(stat.S_IFREG, 0o640, group=1234)
    after.st_ctime_ns += 1
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        runtime_fd_tree.os,
        "fchown",
        lambda fd, uid, gid: calls.append(("fchown", fd, uid, gid)),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_fd_tree.os,
        "fsync",
        lambda fd: calls.append(("fsync", fd)),
    )
    states = iter((before, after))
    monkeypatch.setattr(runtime_fd_tree.os, "fstat", lambda _fd: next(states))
    monkeypatch.setattr(runtime_fd_tree.os, "listxattr", lambda _fd: [], raising=False)
    operation = PublishGroup(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
        group_policy=_group_policy(),
        expected_snapshot=_snapshot(),
    )

    assert (
        runtime_fd_tree._publish_opened_entry(
            7,
            before,
            operation,
            fsync_existing=True,
            is_root=False,
        )
        is after
    )
    assert calls == [("fchown", 7, -1, 1234), ("fsync", 7)]


def test_gid发布拒绝mode等非授权身份漂移(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _metadata(stat.S_IFREG, 0o640, group=0)
    after = _metadata(stat.S_IFREG, 0o750, group=1234)
    after.st_ctime_ns += 1
    monkeypatch.setattr(runtime_fd_tree.os, "fchown", lambda *_args: None, raising=False)
    monkeypatch.setattr(runtime_fd_tree.os, "fsync", lambda _fd: None)
    states = iter((before, after))
    monkeypatch.setattr(runtime_fd_tree.os, "fstat", lambda _fd: next(states))
    monkeypatch.setattr(runtime_fd_tree.os, "listxattr", lambda _fd: [], raising=False)
    operation = PublishGroup(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
        group_policy=_group_policy(),
        expected_snapshot=_snapshot(),
    )

    with pytest.raises(RuntimeFdTreeError, match="内容身份"):
        runtime_fd_tree._publish_opened_entry(
            7,
            before,
            operation,
            fsync_existing=True,
            is_root=False,
        )


def test_gid发布在chown前拒绝已打开对象身份漂移(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _metadata(stat.S_IFREG, 0o640, group=0)
    drifted = _metadata(stat.S_IFREG, 0o640, group=0)
    drifted.st_ctime_ns += 1
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime_fd_tree.os, "fstat", lambda _fd: drifted)
    monkeypatch.setattr(
        runtime_fd_tree.os,
        "fchown",
        lambda *args: calls.append(args),
        raising=False,
    )
    operation = PublishGroup(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
        group_policy=_group_policy(),
        expected_snapshot=_snapshot(),
    )

    with pytest.raises(RuntimeFdTreeError, match="身份发生漂移"):
        runtime_fd_tree._publish_opened_entry(
            7,
            before,
            operation,
            fsync_existing=True,
            is_root=False,
        )

    assert calls == []


def test_gid发布对已提交树的目标组子对象保持只读幂等(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _metadata(stat.S_IFREG, 0o640, group=1234)
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime_fd_tree.os, "fstat", lambda _fd: metadata)
    monkeypatch.setattr(
        runtime_fd_tree.os,
        "fchown",
        lambda *args: calls.append(("fchown", *args)),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_fd_tree.os,
        "fsync",
        lambda fd: calls.append(("fsync", fd)),
    )
    monkeypatch.setattr(runtime_fd_tree.os, "listxattr", lambda _fd: [], raising=False)
    operation = PublishGroup(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
        group_policy=_group_policy(),
        expected_snapshot=_snapshot(),
    )

    assert (
        runtime_fd_tree._publish_opened_entry(
            7,
            metadata,
            operation,
            fsync_existing=False,
            is_root=False,
        )
        is metadata
    )
    assert calls == []


@pytest.mark.parametrize(
    ("fsync_existing", "is_root"),
    [(True, False), (False, True)],
)
def test_gid发布为未完成树或提交根补做fsync(
    monkeypatch: pytest.MonkeyPatch,
    fsync_existing: bool,
    is_root: bool,
) -> None:
    metadata = _metadata(stat.S_IFDIR, 0o750, group=1234)
    calls: list[int] = []
    monkeypatch.setattr(runtime_fd_tree.os, "fstat", lambda _fd: metadata)
    monkeypatch.setattr(runtime_fd_tree.os, "fsync", lambda fd: calls.append(fd))
    monkeypatch.setattr(runtime_fd_tree.os, "listxattr", lambda _fd: [], raising=False)
    operation = PublishGroup(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
        group_policy=_group_policy(),
        expected_snapshot=_snapshot(),
    )

    runtime_fd_tree._publish_opened_entry(
        7,
        metadata,
        operation,
        fsync_existing=fsync_existing,
        is_root=is_root,
    )

    assert calls == [7]


def test身份快照不能由调用方伪造() -> None:
    with pytest.raises(TypeError, match="遍历器"):
        RuntimeFdTreeSnapshot(  # type: ignore[call-arg]
            identity_digest=b"x" * 32,
            entries=1,
            total_bytes=0,
            root_device=7,
            root_inode=11,
        )


def test_operation拒绝可覆写策略行为的子类() -> None:
    class OverridePolicy(RuntimeFdModePolicy):
        def canonical_mode(self, mode: int) -> int:
            return 0o777

    overridden = OverridePolicy(
        directory_mode=0o750,
        regular_mode=0o640,
        executable_mode=0o750,
        executable_mask=stat.S_IXUSR,
    )

    with pytest.raises(TypeError, match="操作策略"):
        VerifyOnly(mode_policy=overridden, marker_policy=_marker_policy())


def test_verify操作拒绝ctime漂移(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _metadata(stat.S_IFREG, 0o640)
    after = _metadata(stat.S_IFREG, 0o640)
    after.st_ctime_ns += 1
    monkeypatch.setattr(runtime_fd_tree.os, "listxattr", lambda _fd: [], raising=False)
    monkeypatch.setattr(runtime_fd_tree.os, "fstat", lambda _fd: after)

    with pytest.raises(RuntimeFdTreeError, match="身份发生漂移"):
        runtime_fd_tree._prepare_opened_entry(
            7,
            before,
            root_device=7,
            operation=VerifyOnly(
                mode_policy=_mode_policy(),
                marker_policy=_marker_policy(),
            ),
        )


def test_walker拒绝未封闭的operation(tmp_path) -> None:
    with pytest.raises(RuntimeFdTreeError, match="操作"):
        walk_runtime_tree(tmp_path, object())  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", [0o660, 0o602, 0o777])
def test_mode操作在映射前拒绝group或other写入(mode: int) -> None:
    with pytest.raises(RuntimeFdTreeError, match="非 root 写"):
        runtime_fd_tree._validate_entry_metadata(
            _metadata(stat.S_IFREG, mode),
            root_device=7,
            require_canonical=False,
            mode_policy=_mode_policy(),
        )


@pytest.mark.parametrize("special", [stat.S_ISUID, stat.S_ISGID, stat.S_ISVTX])
def test_walker拒绝特殊权限位(special: int) -> None:
    with pytest.raises(RuntimeFdTreeError, match="特殊权限"):
        runtime_fd_tree._validate_entry_metadata(
            _metadata(stat.S_IFREG, special | 0o600),
            root_device=7,
            require_canonical=False,
            mode_policy=_mode_policy(),
        )


@pytest.mark.parametrize(
    "metadata",
    [
        _metadata(stat.S_IFDIR, 0o400),
        _metadata(stat.S_IFDIR, 0o100),
        _metadata(stat.S_IFREG, 0o000),
    ],
)
def test_mode操作不得补授缺失的root读取或遍历(metadata: SimpleNamespace) -> None:
    with pytest.raises(RuntimeFdTreeError, match="root 访问"):
        runtime_fd_tree._validate_entry_metadata(
            metadata,
            root_device=7,
            require_canonical=False,
            mode_policy=_mode_policy(),
        )


def test_walker要求root_owner同设备且普通文件单链接() -> None:
    with pytest.raises(RuntimeFdTreeError, match="root"):
        runtime_fd_tree._validate_entry_metadata(
            _metadata(stat.S_IFREG, 0o600, owner=1000),
            root_device=7,
            require_canonical=False,
            mode_policy=_mode_policy(),
        )
    with pytest.raises(RuntimeFdTreeError, match="设备"):
        runtime_fd_tree._validate_entry_metadata(
            _metadata(stat.S_IFREG, 0o600, device=8),
            root_device=7,
            require_canonical=False,
            mode_policy=_mode_policy(),
        )
    with pytest.raises(RuntimeFdTreeError, match="硬链接"):
        runtime_fd_tree._validate_entry_metadata(
            _metadata(stat.S_IFREG, 0o600, links=2),
            root_device=7,
            require_canonical=False,
            mode_policy=_mode_policy(),
        )


@pytest.mark.parametrize(
    "metadata",
    [
        _metadata(stat.S_IFDIR, 0o700),
        _metadata(stat.S_IFREG, 0o600),
        _metadata(stat.S_IFREG, 0o755),
    ],
)
def test_verify操作拒绝非规范mode(metadata: SimpleNamespace) -> None:
    with pytest.raises(RuntimeFdTreeError, match="访问模式漂移"):
        runtime_fd_tree._validate_entry_metadata(
            metadata,
            root_device=7,
            require_canonical=True,
            mode_policy=_mode_policy(),
        )


def test_root_owned稳定符号链接不按777误判可写() -> None:
    runtime_fd_tree._validate_entry_metadata(
        _metadata(stat.S_IFLNK, 0o777),
        root_device=7,
        require_canonical=True,
        mode_policy=_mode_policy(),
    )


def test已完成对象访问修复只接受目录0755漂移() -> None:
    operation = PreflightCompletedAccessRepair(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
    )

    runtime_fd_tree._validate_completed_access_repair_metadata(
        _metadata(stat.S_IFDIR, 0o755),
        root_device=7,
        operation=operation,
    )
    runtime_fd_tree._validate_completed_access_repair_metadata(
        _metadata(stat.S_IFDIR, 0o750),
        root_device=7,
        operation=operation,
    )
    with pytest.raises(RuntimeFdTreeError, match="已完成对象访问模式不受支持"):
        runtime_fd_tree._validate_completed_access_repair_metadata(
            _metadata(stat.S_IFDIR, 0o700),
            root_device=7,
            operation=operation,
        )
    with pytest.raises(RuntimeFdTreeError, match="已完成对象访问模式不受支持"):
        runtime_fd_tree._validate_completed_access_repair_metadata(
            _metadata(stat.S_IFREG, 0o644),
            root_device=7,
            operation=operation,
        )


def test条目与字节预算失败关闭(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_fd_tree, "_MAX_TREE_ENTRIES", 1)
    monkeypatch.setattr(runtime_fd_tree, "_MAX_TREE_BYTES", 3)
    assert runtime_fd_tree._advance_budget(0, 0, regular_size=3) == (1, 3)
    with pytest.raises(RuntimeFdTreeError, match="条目"):
        runtime_fd_tree._advance_budget(1, 0, regular_size=0)
    with pytest.raises(RuntimeFdTreeError, match="大小"):
        runtime_fd_tree._advance_budget(0, 0, regular_size=4)


def test大型树遍历按固定条目间隔报告无路径进度(caplog) -> None:
    budget = runtime_fd_tree._TraversalBudget(next_progress=1)

    with caplog.at_level("INFO", logger=runtime_fd_tree.__name__):
        budget.add(regular_size=3)

    assert "条目=1，字节=3" in caplog.text
    assert "路径" not in caplog.text


def test扩展属性显式拒绝(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runtime_fd_tree.os,
        "listxattr",
        lambda _fd: ["user.demo"],
        raising=False,
    )
    with pytest.raises(RuntimeFdTreeError, match="扩展属性或 ACL"):
        runtime_fd_tree._require_no_extended_attributes(7)


def test策略数据拒绝不受支持的权限映射() -> None:
    with pytest.raises(ValueError, match="不受支持"):
        RuntimeFdModePolicy(
            directory_mode=0o755,
            regular_mode=0o644,
            executable_mode=0o755,
            executable_mask=0o111,
        )


def test标记策略拒绝逃逸名称和重复内容() -> None:
    with pytest.raises(ValueError, match="名称"):
        RuntimeRootMarkerPolicy(
            name="../.incomplete",
            trusted_contents=(b"after_install\n",),
            max_bytes=64,
        )
    with pytest.raises(ValueError, match="内容"):
        RuntimeRootMarkerPolicy(
            name=".incomplete",
            trusted_contents=(b"same\n", b"same\n"),
            max_bytes=64,
        )


_LINUX_ROOT = sys.platform.startswith("linux") and getattr(os, "geteuid", lambda: -1)() == 0


@pytest.mark.skipif(not _LINUX_ROOT, reason="结构快照集成要求 Linux root")
def test_gid发布拒绝预检后发生的inode替换(tmp_path) -> None:
    root = tmp_path / "object"
    root.mkdir(mode=0o750)
    payload = root / "payload"
    payload.write_bytes(b"same")
    os.chmod(payload, 0o640)
    os.chmod(root, 0o750)
    policy = RuntimeFdGroupPolicy(target_gid=1234)
    preflight = walk_runtime_tree(
        root,
        PreflightGroup(
            mode_policy=_mode_policy(),
            marker_policy=_marker_policy(),
            group_policy=policy,
        ),
    )
    replacement = root / "replacement"
    replacement.write_bytes(b"same")
    os.chmod(replacement, 0o640)
    os.replace(replacement, payload)

    with pytest.raises(RuntimeFdTreeError, match="身份快照"):
        walk_runtime_tree(
            root,
            PublishGroup(
                mode_policy=_mode_policy(),
                marker_policy=_marker_policy(),
                group_policy=policy,
                expected_snapshot=preflight.identity_snapshot,
            ),
        )


@pytest.mark.skipif(not _LINUX_ROOT, reason="O_NOATIME 集成要求 Linux root")
def test_linux目录打开固定请求noatime(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_open = os.open
    captured: list[int] = []

    def recording_open(path, flags, *, dir_fd=None):
        captured.append(flags)
        return real_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(runtime_fd_tree.os, "open", recording_open)
    descriptor = runtime_fd_tree._open_directory(tmp_path)
    os.close(descriptor)

    assert captured[0] & os.O_NOATIME


@pytest.mark.skipif(not _LINUX_ROOT, reason="已完成对象访问修复集成要求 Linux root")
def test已完成对象访问修复先完整预检再收敛(tmp_path) -> None:
    root = tmp_path / "object"
    nested = root / "nested"
    root.mkdir(mode=0o755)
    nested.mkdir(mode=0o755)
    payload = nested / "payload"
    payload.write_bytes(b"same")
    os.chmod(payload, 0o640)
    operation = PreflightCompletedAccessRepair(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
    )
    preflight = walk_runtime_tree(root, operation)
    assert preflight.access_repair_snapshot is not None

    invalid = root / "invalid"
    invalid.write_bytes(b"invalid")
    os.chmod(invalid, 0o600)

    with pytest.raises(RuntimeFdTreeError, match="已完成对象访问模式不受支持"):
        walk_runtime_tree(
            root,
            ConvergeCompletedAccessRepair(
                mode_policy=_mode_policy(),
                marker_policy=_marker_policy(),
                expected_snapshot=preflight.access_repair_snapshot,
            ),
        )

    assert stat.S_IMODE(root.stat().st_mode) == 0o755
    assert stat.S_IMODE(nested.stat().st_mode) == 0o755


@pytest.mark.skipif(not _LINUX_ROOT, reason="已完成对象访问修复集成要求 Linux root")
def test已完成对象访问修复收敛目录并可重试(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "object"
    nested = root / "nested"
    root.mkdir(mode=0o755)
    nested.mkdir(mode=0o755)
    payload = nested / "payload"
    payload.write_bytes(b"same")
    os.chmod(payload, 0o640)
    preflight = walk_runtime_tree(
        root,
        PreflightCompletedAccessRepair(
            mode_policy=_mode_policy(),
            marker_policy=_marker_policy(),
        ),
    )
    expected = preflight.access_repair_snapshot
    assert expected is not None
    real_fsync = os.fsync
    failures = 0

    def fail_nested_once(descriptor: int) -> None:
        nonlocal failures
        path = os.readlink(f"/proc/self/fd/{descriptor}")
        if path == str(nested) and failures == 0:
            failures += 1
            raise OSError("模拟中断")
        real_fsync(descriptor)

    monkeypatch.setattr(runtime_fd_tree.os, "fsync", fail_nested_once)
    operation = ConvergeCompletedAccessRepair(
        mode_policy=_mode_policy(),
        marker_policy=_marker_policy(),
        expected_snapshot=expected,
    )
    with pytest.raises(RuntimeFdTreeError, match="访问模式无法安全收敛"):
        walk_runtime_tree(root, operation)

    assert {stat.S_IMODE(path.stat().st_mode) for path in (root, nested)} <= {
        0o750,
        0o755,
    }
    monkeypatch.setattr(runtime_fd_tree.os, "fsync", real_fsync)
    report = walk_runtime_tree(root, operation)

    assert report.access_repair_snapshot == expected
    assert [stat.S_IMODE(path.stat().st_mode) for path in (root, nested)] == [
        0o750,
        0o750,
    ]
