from __future__ import annotations

import os
import pickle
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform import runtime_fd_tree, runtime_service_access
from codev_platform.core.runtime_models import RUNTIME_ACCESS_PROFILE, sha256_file
from codev_platform.runtime_service_access import (
    RuntimeServiceAccessError,
    converge_runtime_service_namespace,
    publish_runtime_service_objects,
    verify_runtime_service_access,
)


_LINUX_ROOT = sys.platform.startswith("linux") and getattr(os, "geteuid", lambda: -1)() == 0
_BASE_ID = "a" * 64
_RELEASE_ID = "b" * 64
_SERVICE_UID = 23456
_SERVICE_GID = 23457


def test_拆分后公开证明类型保持同一对象与_pickle_路径() -> None:
    from codev_platform import _runtime_service_access_contracts as contracts

    names = (
        "RuntimeServiceAccessError",
        "RuntimeServiceAccessProof",
        "RuntimeServiceContentProof",
        "RuntimeServiceNamespaceProof",
    )
    for name in names:
        public_type = getattr(runtime_service_access, name)
        assert public_type is getattr(contracts, name)
        assert public_type.__module__ == "codev_platform.runtime_service_access"
        assert pickle.loads(pickle.dumps(public_type)) is public_type


def _chmod(path: Path, mode: int) -> None:
    os.chmod(path, mode, follow_symlinks=False)


def _runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    for name, mode in (
        ("bases", 0o750),
        ("releases", 0o755),
        ("locks", 0o700),
        ("deployments", 0o700),
        ("candidates", 0o700),
        ("quarantine", 0o700),
    ):
        child = root / name
        child.mkdir(mode=mode)
        _chmod(child, mode)
    _chmod(root, 0o700)
    return root


def _private_snapshot(root: Path) -> dict[str, tuple[int, ...] | bytes]:
    secret = root / "candidates" / "secret"
    if not secret.exists():
        secret.write_bytes(b"private")
        _chmod(secret, 0o600)
    result: dict[str, tuple[int, ...] | bytes] = {}
    for name in ("locks", "deployments", "candidates", "quarantine"):
        path = root / name
        metadata = path.lstat()
        result[name] = (
            metadata.st_mode,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
    result["secret"] = secret.read_bytes()
    return result


def _write_object(root: Path, kind: str, object_id: str) -> Path:
    directory = root / f"{kind}s" / object_id
    directory.mkdir(mode=0o750)
    payload = directory / "payload.py"
    payload.write_bytes(b"value = 1\n")
    _chmod(payload, 0o640)
    executable = directory / "python"
    executable.write_bytes(b"python")
    _chmod(executable, 0o750)
    metadata = directory / ("base.json" if kind == "base" else "release.json")
    metadata.write_bytes(b"{}\n")
    _chmod(metadata, 0o640)
    _chmod(directory, 0o750)
    return directory


def _fake_ports(
    events: list[tuple[object, ...]],
    *,
    base_schema: int = 3,
) -> SimpleNamespace:
    base = SimpleNamespace(
        schema_version=base_schema,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=_BASE_ID,
        purelib_inventory_sha256="c" * 64,
    )
    release = SimpleNamespace(
        schema_version=1,
        release_id=_RELEASE_ID,
        base_id=_BASE_ID,
    )

    @contextmanager
    def id_lock(root: Path, kind: str, object_id: str, *, shared: bool):
        events.append(("lock-enter", kind, object_id, shared))
        try:
            yield
        finally:
            events.append(("lock-exit", kind, object_id, shared))

    def read_release_base_id_locked(root: Path, release_id: str) -> str:
        events.append(("read-release-base", release_id))
        return _BASE_ID

    def verify_base_locked(root: Path, base_id: str) -> SimpleNamespace:
        events.append(("verify-base", base_id))
        return base

    def verify_release_locked(
        root: Path,
        release_id: str,
        *,
        verified_base: SimpleNamespace,
    ) -> SimpleNamespace:
        events.append(("verify-release", release_id, verified_base.base_id))
        return release

    return SimpleNamespace(
        id_lock=id_lock,
        read_release_base_id_locked=read_release_base_id_locked,
        verify_base_locked=verify_base_locked,
        verify_release_locked=verify_release_locked,
        sha256_file=sha256_file,
    )


@pytest.mark.skipif(not _LINUX_ROOT, reason="服务主组发布集成要求 Linux root")
def test_namespace只收敛三个公开父目录且幂等(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path)
    os.chown(root / "bases", 0, _SERVICE_GID)
    before_private = _private_snapshot(root)

    first = converge_runtime_service_namespace(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
    )
    before_second = {name: (root / name).lstat() for name in (".", "bases", "releases")}
    second = converge_runtime_service_namespace(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
    )

    for path in (root, root / "bases", root / "releases"):
        assert path.stat().st_uid == 0
        assert path.stat().st_gid == _SERVICE_GID
        assert stat.S_IMODE(path.stat().st_mode) == 0o710
    assert _private_snapshot(root) == before_private
    assert first == second
    assert all((root / name).lstat() == metadata for name, metadata in before_second.items())
    assert not hasattr(first, "root")
    assert not hasattr(first, "path")


@pytest.mark.skipif(not _LINUX_ROOT, reason="服务主组发布集成要求 Linux root")
def test_namespace全量预检失败时一个目录也不修改(tmp_path: Path) -> None:
    root = _runtime_root(tmp_path)
    _chmod(root / "releases", 0o770)
    before = {
        path: (path.lstat().st_mode, path.lstat().st_gid, path.lstat().st_ctime_ns)
        for path in (root, root / "bases", root / "releases")
    }

    with pytest.raises(RuntimeServiceAccessError, match="命名空间"):
        converge_runtime_service_namespace(
            root,
            service_uid=_SERVICE_UID,
            service_gid=_SERVICE_GID,
        )

    assert {
        path: (path.lstat().st_mode, path.lstat().st_gid, path.lstat().st_ctime_ns)
        for path in before
    } == before


@pytest.mark.skipif(not _LINUX_ROOT, reason="服务主组发布集成要求 Linux root")
def test内容对象先全量预检再叶子优先发布并精确复验(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    converge_runtime_service_namespace(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
    )
    base_dir = _write_object(root, "base", _BASE_ID)
    release_dir = _write_object(root, "release", _RELEASE_ID)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    _chmod(outside, 0o600)
    link = release_dir / "base-link"
    link.symlink_to(outside)
    old = _write_object(root, "base", "d" * 64)
    before_old = {
        path.relative_to(old): (path.lstat().st_mode, path.lstat().st_gid)
        for path in (old, *old.rglob("*"))
    }
    before_modes = {
        path: stat.S_IMODE(path.lstat().st_mode)
        for directory in (base_dir, release_dir)
        for path in (directory, *directory.rglob("*"))
    }
    before_bytes = {
        path: path.read_bytes()
        for directory in (base_dir, release_dir)
        for path in directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime_service_access, "_default_ports", lambda: _fake_ports(events))
    published_paths: list[Path] = []
    real_fchown = os.fchown

    def recording_fchown(descriptor: int, uid: int, gid: int) -> None:
        published_paths.append(Path(os.readlink(f"/proc/self/fd/{descriptor}")))
        real_fchown(descriptor, uid, gid)

    monkeypatch.setattr(runtime_fd_tree.os, "fchown", recording_fchown)

    proof = publish_runtime_service_objects(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        base_ids=(_BASE_ID,),
        release_ids=(_RELEASE_ID,),
    )
    verified = verify_runtime_service_access(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        base_ids=(_BASE_ID,),
        release_ids=(_RELEASE_ID,),
    )

    for directory in (base_dir, release_dir):
        for path in (directory, *directory.rglob("*")):
            if path.is_symlink():
                assert path.lstat().st_gid == 0
            else:
                assert path.lstat().st_gid == _SERVICE_GID
            assert stat.S_IMODE(path.lstat().st_mode) == before_modes[path]
    assert {path: path.read_bytes() for path in before_bytes} == before_bytes
    assert {
        path.relative_to(old): (path.lstat().st_mode, path.lstat().st_gid)
        for path in (old, *old.rglob("*"))
    } == before_old
    assert outside.read_bytes() == b"outside"
    assert outside.stat().st_gid == 0
    assert published_paths.index(base_dir / "payload.py") < published_paths.index(base_dir)
    assert published_paths.index(release_dir / "payload.py") < published_paths.index(release_dir)
    assert proof.access_profile == RUNTIME_ACCESS_PROFILE
    assert proof.base_ids == (_BASE_ID,)
    assert proof.release_ids == (_RELEASE_ID,)
    assert verified.content == proof
    lock_enters = [event for event in events if event[0] == "lock-enter"]
    assert lock_enters[:2] == [
        ("lock-enter", "release", _RELEASE_ID, False),
        ("lock-enter", "base", _BASE_ID, False),
    ]
    assert lock_enters[-2:] == [
        ("lock-enter", "release", _RELEASE_ID, True),
        ("lock-enter", "base", _BASE_ID, True),
    ]
    assert events.count(("verify-base", _BASE_ID)) == 2
    assert events.count(("verify-release", _RELEASE_ID, _BASE_ID)) == 2


@pytest.mark.skipif(not _LINUX_ROOT, reason="服务主组发布集成要求 Linux root")
def test任一对象预检失败时其他对象也保持root组(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    converge_runtime_service_namespace(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
    )
    base_dir = _write_object(root, "base", _BASE_ID)
    release_dir = _write_object(root, "release", _RELEASE_ID)
    _chmod(release_dir / "payload.py", 0o660)
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime_service_access, "_default_ports", lambda: _fake_ports(events))

    with pytest.raises(RuntimeServiceAccessError, match="内容对象"):
        publish_runtime_service_objects(
            root,
            service_uid=_SERVICE_UID,
            service_gid=_SERVICE_GID,
            base_ids=(_BASE_ID,),
            release_ids=(_RELEASE_ID,),
        )

    assert all(
        path.lstat().st_gid == 0
        for directory in (base_dir, release_dir)
        for path in (directory, *directory.rglob("*"))
    )


@pytest.mark.skipif(not _LINUX_ROOT, reason="服务主组发布集成要求 Linux root")
def test内容发布中断只留下合法可重试状态(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    converge_runtime_service_namespace(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
    )
    base_dir = _write_object(root, "base", _BASE_ID)
    release_dir = _write_object(root, "release", _RELEASE_ID)
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime_service_access, "_default_ports", lambda: _fake_ports(events))
    real_fchown = os.fchown
    call_count = 0

    def interrupted_fchown(descriptor: int, uid: int, gid: int) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("模拟发布中断")
        real_fchown(descriptor, uid, gid)

    monkeypatch.setattr(runtime_fd_tree.os, "fchown", interrupted_fchown)
    with pytest.raises(RuntimeServiceAccessError, match="内容对象发布"):
        publish_runtime_service_objects(
            root,
            service_uid=_SERVICE_UID,
            service_gid=_SERVICE_GID,
            base_ids=(_BASE_ID,),
            release_ids=(_RELEASE_ID,),
        )

    assert {
        path.lstat().st_gid
        for directory in (base_dir, release_dir)
        for path in (directory, *directory.rglob("*"))
        if not path.is_symlink()
    } <= {0, _SERVICE_GID}
    monkeypatch.setattr(runtime_fd_tree.os, "fchown", real_fchown)

    publish_runtime_service_objects(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        base_ids=(_BASE_ID,),
        release_ids=(_RELEASE_ID,),
    )
    assert all(
        path.lstat().st_gid == _SERVICE_GID
        for directory in (base_dir, release_dir)
        for path in (directory, *directory.rglob("*"))
        if not path.is_symlink()
    )


@pytest.mark.skipif(not _LINUX_ROOT, reason="服务主组发布集成要求 Linux root")
@pytest.mark.parametrize("failure_at", ["leaf", "root"])
def test_chgrp后fsync失败会在重试时补做持久化(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_at: str,
) -> None:
    root = _runtime_root(tmp_path)
    converge_runtime_service_namespace(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
    )
    base_dir = _write_object(root, "base", _BASE_ID)
    _write_object(root, "release", _RELEASE_ID)
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime_service_access, "_default_ports", lambda: _fake_ports(events))
    real_fsync = os.fsync
    failed_path: Path | None = None

    def fail_once(descriptor: int) -> None:
        nonlocal failed_path
        path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        should_fail = (failure_at == "leaf" and path == base_dir / "payload.py") or (
            failure_at == "root" and path == base_dir
        )
        if should_fail and failed_path is None:
            failed_path = path
            raise OSError("模拟 fsync 中断")
        real_fsync(descriptor)

    monkeypatch.setattr(runtime_fd_tree.os, "fsync", fail_once)
    with pytest.raises(RuntimeServiceAccessError, match="内容对象发布"):
        publish_runtime_service_objects(
            root,
            service_uid=_SERVICE_UID,
            service_gid=_SERVICE_GID,
            base_ids=(_BASE_ID,),
            release_ids=(_RELEASE_ID,),
        )

    assert failed_path is not None
    assert failed_path.lstat().st_gid == _SERVICE_GID
    retried: list[Path] = []

    def record_retry(descriptor: int) -> None:
        retried.append(Path(os.readlink(f"/proc/self/fd/{descriptor}")))
        real_fsync(descriptor)

    monkeypatch.setattr(runtime_fd_tree.os, "fsync", record_retry)
    publish_runtime_service_objects(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        base_ids=(_BASE_ID,),
        release_ids=(_RELEASE_ID,),
    )

    assert failed_path in retried


@pytest.mark.skipif(not _LINUX_ROOT, reason="服务主组发布集成要求 Linux root")
def test_base集合不等于release精确引用时锁内拒绝且不改对象(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    converge_runtime_service_namespace(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
    )
    release_dir = _write_object(root, "release", _RELEASE_ID)
    other_base = "d" * 64
    base_dir = _write_object(root, "base", other_base)
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime_service_access, "_default_ports", lambda: _fake_ports(events))

    with pytest.raises(RuntimeServiceAccessError, match="base 集合"):
        publish_runtime_service_objects(
            root,
            service_uid=_SERVICE_UID,
            service_gid=_SERVICE_GID,
            base_ids=(other_base,),
            release_ids=(_RELEASE_ID,),
        )

    assert all(
        path.lstat().st_gid == 0
        for directory in (base_dir, release_dir)
        for path in (directory, *directory.rglob("*"))
    )
    assert [event for event in events if event[0] == "lock-enter"] == [
        ("lock-enter", "release", _RELEASE_ID, False)
    ]


@pytest.mark.skipif(not _LINUX_ROOT, reason="服务主组发布集成要求 Linux root")
def test旧schema2对象被拒绝且保持原样(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runtime_root(tmp_path)
    converge_runtime_service_namespace(
        root,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
    )
    base_dir = _write_object(root, "base", _BASE_ID)
    release_dir = _write_object(root, "release", _RELEASE_ID)
    before = {
        path: (path.lstat().st_mode, path.lstat().st_gid, path.read_bytes())
        for directory in (base_dir, release_dir)
        for path in directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    events: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        runtime_service_access,
        "_default_ports",
        lambda: _fake_ports(events, base_schema=2),
    )

    with pytest.raises(RuntimeServiceAccessError, match="访问模型"):
        publish_runtime_service_objects(
            root,
            service_uid=_SERVICE_UID,
            service_gid=_SERVICE_GID,
            base_ids=(_BASE_ID,),
            release_ids=(_RELEASE_ID,),
        )

    assert {
        path: (path.lstat().st_mode, path.lstat().st_gid, path.read_bytes()) for path in before
    } == before


def test对象ID必须完整非零且base集合必须与release引用一致(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_service_access, "_require_linux_root", lambda: None)
    root = tmp_path.resolve()

    with pytest.raises(RuntimeServiceAccessError, match="对象 ID"):
        publish_runtime_service_objects(
            root,
            service_uid=_SERVICE_UID,
            service_gid=_SERVICE_GID,
            base_ids=("0" * 64,),
            release_ids=(_RELEASE_ID,),
        )

    with pytest.raises(RuntimeServiceAccessError, match="UID"):
        publish_runtime_service_objects(
            root,
            service_uid=1 << 32,
            service_gid=_SERVICE_GID,
            base_ids=(_BASE_ID,),
            release_ids=(_RELEASE_ID,),
        )


@pytest.mark.parametrize("publish", [True, False])
def test多对象锁严格按release后base及完整ID排序(
    monkeypatch: pytest.MonkeyPatch,
    publish: bool,
) -> None:
    base_ids = ("a" * 64, "c" * 64)
    release_ids = ("b" * 64, "d" * 64)
    request = runtime_service_access._ContentRequest(
        root=Path("/runtime"),
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        base_ids=base_ids,
        release_ids=release_ids,
    )
    references = dict(zip(release_ids, base_ids, strict=True))
    events: list[tuple[object, ...]] = []

    @contextmanager
    def id_lock(root: Path, kind: str, object_id: str, *, shared: bool):
        events.append(("lock", kind, object_id, shared))
        yield

    ports = SimpleNamespace(
        id_lock=id_lock,
        read_release_base_id_locked=lambda _root, release_id: (
            events.append(("read", release_id)) or references[release_id]
        ),
    )
    expected = object()
    monkeypatch.setattr(
        runtime_service_access,
        "_access_content_locked",
        lambda *_args, **_kwargs: events.append(("access",)) or expected,
    )

    assert (
        runtime_service_access._access_content_objects(
            request,
            ports,
            publish=publish,
        )
        is expected
    )
    shared = not publish
    assert events == [
        ("lock", "release", release_ids[0], shared),
        ("lock", "release", release_ids[1], shared),
        ("read", release_ids[0]),
        ("read", release_ids[1]),
        ("lock", "base", base_ids[0], shared),
        ("lock", "base", base_ids[1], shared),
        ("access",),
    ]


def test_namespace的chgrp未达到目标组时失败关闭(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = SimpleNamespace(
        st_dev=7,
        st_ino=11,
        st_mode=stat.S_IFDIR | 0o700,
        st_uid=0,
        st_gid=0,
        st_nlink=2,
        st_size=0,
        st_mtime_ns=13,
        st_ctime_ns=17,
    )
    entry = runtime_service_access._NamespaceEntry(
        name="",
        descriptor=7,
        metadata=metadata,
    )
    monkeypatch.setattr(runtime_service_access.os, "fstat", lambda _fd: metadata)
    monkeypatch.setattr(
        runtime_service_access.os,
        "fchown",
        lambda *_args: None,
        raising=False,
    )
    monkeypatch.setattr(runtime_service_access.os, "fsync", lambda _fd: None)
    monkeypatch.setattr(
        runtime_service_access.os,
        "listxattr",
        lambda _fd: [],
        raising=False,
    )
    monkeypatch.setattr(
        runtime_service_access,
        "_namespace_reference",
        lambda *_args: metadata,
    )

    with pytest.raises(RuntimeServiceAccessError, match="GID"):
        runtime_service_access._publish_namespace_gid(
            (entry,),
            root=Path("/runtime"),
            target_gid=_SERVICE_GID,
        )


def test_namespace每次mutation前重新绑定预检身份(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = SimpleNamespace(
        st_dev=7,
        st_ino=11,
        st_mode=stat.S_IFDIR | 0o700,
        st_uid=0,
        st_gid=0,
        st_nlink=2,
        st_size=0,
        st_mtime_ns=13,
        st_ctime_ns=17,
    )
    drifted = SimpleNamespace(**vars(before))
    drifted.st_ino += 1
    entry = runtime_service_access._NamespaceEntry(
        name="",
        descriptor=7,
        metadata=before,
    )
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(runtime_service_access.os, "fstat", lambda _fd: drifted)
    monkeypatch.setattr(
        runtime_service_access,
        "_namespace_reference",
        lambda *_args: drifted,
    )
    monkeypatch.setattr(
        runtime_service_access.os,
        "fchown",
        lambda *args: calls.append(args),
        raising=False,
    )

    with pytest.raises(RuntimeServiceAccessError, match="身份发生漂移"):
        runtime_service_access._publish_namespace_gid(
            (entry,),
            root=Path("/runtime"),
            target_gid=_SERVICE_GID,
        )

    assert calls == []


def test内容操作前后都复验父命名空间(monkeypatch: pytest.MonkeyPatch) -> None:
    request = SimpleNamespace(
        root=Path("/runtime"),
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        base_ids=(_BASE_ID,),
        release_ids=(_RELEASE_ID,),
    )
    namespace = runtime_service_access.RuntimeServiceNamespaceProof(
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        directory_count=3,
        mode=0o710,
    )
    content = runtime_service_access.RuntimeServiceContentProof(
        access_profile=RUNTIME_ACCESS_PROFILE,
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        base_ids=(_BASE_ID,),
        release_ids=(_RELEASE_ID,),
        base_metadata_sha256=("c" * 64,),
        base_inventory_sha256=("d" * 64,),
        release_metadata_sha256=("e" * 64,),
        entries=2,
        total_bytes=3,
    )
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        runtime_service_access, "_content_request", lambda *_args, **_kwargs: request
    )
    monkeypatch.setattr(runtime_service_access, "_default_ports", lambda: object())
    monkeypatch.setattr(
        runtime_service_access,
        "_verify_namespace",
        lambda *args: calls.append(("namespace", *args)) or namespace,
    )
    monkeypatch.setattr(
        runtime_service_access,
        "_access_content_objects",
        lambda req, _ports, *, publish: calls.append(("content", req, publish)) or content,
    )

    assert (
        publish_runtime_service_objects(
            Path("/ignored"),
            service_uid=1,
            service_gid=2,
            base_ids=("f" * 64,),
            release_ids=("1" * 64,),
        )
        is content
    )
    assert [call[0] for call in calls] == ["namespace", "content", "namespace"]
    calls.clear()

    proof = verify_runtime_service_access(
        Path("/ignored"),
        service_uid=1,
        service_gid=2,
        base_ids=("f" * 64,),
        release_ids=("1" * 64,),
    )
    assert proof == runtime_service_access.RuntimeServiceAccessProof(
        namespace=namespace,
        content=content,
    )
    assert [call[0] for call in calls] == ["namespace", "content", "namespace"]


def test发布热路径固定三轮walker且静态深验只执行一次(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = runtime_service_access._ContentRequest(
        root=Path("/runtime"),
        service_uid=_SERVICE_UID,
        service_gid=_SERVICE_GID,
        base_ids=(_BASE_ID,),
        release_ids=(_RELEASE_ID,),
    )
    snapshot = runtime_fd_tree._new_identity_snapshot(
        identity_digest=b"p" * 32,
        entries=1,
        total_bytes=2,
        root_device=7,
        root_inode=11,
    )
    report = runtime_fd_tree.RuntimeFdTreeReport(
        entries=1,
        total_bytes=2,
        root_device=7,
        root_inode=11,
        identity_snapshot=snapshot,
    )
    static = runtime_service_access._StaticSnapshot(
        base_models=(object(),),
        release_models=(object(),),
        base_metadata_sha256=("c" * 64,),
        base_inventory_sha256=("d" * 64,),
        release_metadata_sha256=("e" * 64,),
    )
    calls: list[object] = []
    monkeypatch.setattr(
        runtime_service_access,
        "_static_snapshot",
        lambda *_args, **_kwargs: calls.append("static") or static,
    )
    monkeypatch.setattr(
        runtime_service_access,
        "_metadata_sha256_snapshot",
        lambda *_args, **_kwargs: (
            calls.append("metadata")
            or (static.base_metadata_sha256, static.release_metadata_sha256)
        ),
    )
    monkeypatch.setattr(
        runtime_service_access,
        "walk_runtime_tree",
        lambda root, operation: calls.append((root.name, type(operation))) or report,
    )

    runtime_service_access._access_content_locked(
        request,
        object(),
        release_bases=(_BASE_ID,),
        publish=True,
    )

    assert calls == [
        "static",
        (_BASE_ID, runtime_service_access.PreflightGroup),
        (_RELEASE_ID, runtime_service_access.PreflightGroup),
        (_BASE_ID, runtime_service_access.PublishGroup),
        (_RELEASE_ID, runtime_service_access.PublishGroup),
        (_BASE_ID, runtime_service_access.VerifyGroup),
        (_RELEASE_ID, runtime_service_access.VerifyGroup),
        "metadata",
    ]
