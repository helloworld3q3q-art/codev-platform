"""受管恢复文件的 dirfd 可信路径测试。"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.name != "posix", reason="renameat2 边界只在 WSL/Linux 验证")
def test_rename_noreplace_rejects_nul_without_c_string_truncation(tmp_path: Path) -> None:
    import codev_platform._runtime_renameat2 as module

    source = tmp_path / "source.tmp"
    source.write_bytes(b"payload")
    parent_descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            module.rename_noreplace_at(
                source.name,
                "victim\0suffix",
                parent_descriptor,
                parent_descriptor,
            )
    finally:
        os.close(parent_descriptor)

    assert source.read_bytes() == b"payload"
    assert not (tmp_path / "victim").exists()


def test_legacy_path_rejects_nul_before_opening_any_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    monkeypatch.setattr(
        module,
        "_open_at",
        lambda *_args, **_kwargs: pytest.fail("NUL 路径不得触发任何 open"),
    )
    with pytest.raises(module.TrustedManagedPathError):
        module.read_optional_root_owned_regular_file(
            tmp_path / "victim\0suffix",
            max_bytes=128,
        )


def _安全目录() -> SimpleNamespace:
    return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)


def _安全文件(
    *,
    permissions: int = 0o600,
    gid: int = 0,
    links: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        st_mode=stat.S_IFREG | permissions,
        st_uid=0,
        st_gid=gid,
        st_nlink=links,
    )


def _绑定安全标志(monkeypatch: pytest.MonkeyPatch, module) -> None:
    monkeypatch.setattr(module.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(module.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(module.os, "O_CLOEXEC", 0x80000, raising=False)
    monkeypatch.setattr(module.os, "O_NONBLOCK", 0x4000, raising=False)


def test_ops兼容门面重导出全部历史调用面() -> None:
    from codev_platform.ops import reindex_codegraph_resume_managed_path as compatibility

    import codev_platform.runtime_managed_file as source

    names = (
        "RootOwnedRegularFileSnapshot",
        "TrustedManagedPathError",
        "_close_quietly",
        "_fchmod",
        "_fchown",
        "_fstat",
        "_fsync",
        "_open_optional_regular_file",
        "_open_temporary_file",
        "_open_trusted_parent",
        "_read_bounded",
        "_read_file_flags",
        "_require_write_arguments",
        "_root_owned_regular_file_metadata",
        "_supports_dir_fd",
        "_temporary_file_flags",
        "_unlink_at",
        "_unlink_temporary_quietly",
        "_write_all",
        "read_optional_root_owned_regular_file",
        "read_optional_root_owned_regular_file_snapshot",
        "remove_root_owned_regular_file",
        "write_root_owned_regular_file_atomic",
    )
    assert all(getattr(compatibility, name) is getattr(source, name) for name in names)


def _绑定可信父目录(
    monkeypatch: pytest.MonkeyPatch,
    module,
    target: Path,
    *,
    final_descriptor: int | None = None,
) -> tuple[list[object], int]:
    """模拟逐级 dirfd 打开；最终文件由调用方决定是否存在。"""
    descriptors = iter(range(10, 10 + len(target.parts)))
    parent_descriptor = 10 + len(target.parts) - 2
    calls: list[object] = []

    def 打开(name: str, flags: int, *, dir_fd: int | None = None, mode: int = 0o777) -> int:
        calls.append(("open", name, flags, dir_fd, mode))
        if name == target.name and final_descriptor is None:
            raise FileNotFoundError
        if (name == target.name or name.startswith(".")) and final_descriptor is not None:
            return final_descriptor
        return next(descriptors)

    def 取状态(descriptor: int) -> object:
        calls.append(("fstat", descriptor))
        return _安全文件() if descriptor == final_descriptor else _安全目录()

    monkeypatch.setattr(module, "_open_at", 打开)
    monkeypatch.setattr(module, "_fstat", 取状态)
    monkeypatch.setattr(module, "_close", lambda descriptor: calls.append(("close", descriptor)))
    return calls, parent_descriptor


def test_可选读取只以dirfd链判定最终文件缺失并使用非阻塞标志(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "trusted" / "resume.env").resolve()
    _绑定安全标志(monkeypatch, module)
    calls, _parent = _绑定可信父目录(monkeypatch, module, target)
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(module, "_read", lambda *_args: pytest.fail("缺失文件不得读取"))

    assert module.read_optional_root_owned_regular_file(target, max_bytes=128) is None

    final_open = next(item for item in calls if item[0] == "open" and item[1] == target.name)
    assert final_open[2] & module.os.O_NONBLOCK
    assert not any(item[0] == "read" for item in calls)


def test_原像读取将内容权限及完整属主绑定到同一受信描述符(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "trusted" / "resume.env").resolve()
    _绑定安全标志(monkeypatch, module)
    calls, _parent = _绑定可信父目录(monkeypatch, module, target, final_descriptor=91)
    chunks = iter((b"old\n", b""))
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(module, "_read", lambda _descriptor, _size: next(chunks))
    def 取状态(descriptor: int) -> SimpleNamespace:
        calls.append(("fstat", descriptor))
        if descriptor == 91:
            return _安全文件(permissions=0o640, gid=1234)
        return _安全目录()

    monkeypatch.setattr(module, "_fstat", 取状态)

    snapshot = module.read_optional_root_owned_regular_file_snapshot(target, max_bytes=128)

    assert snapshot == module.RootOwnedRegularFileSnapshot(
        content=b"old\n",
        mode=0o640,
        uid=0,
        gid=1234,
    )
    assert any(item == ("fstat", 91) for item in calls)


def test_可信读取拒绝硬链接文件(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "trusted" / "receipt.json").resolve()
    _绑定安全标志(monkeypatch, module)
    calls, _parent = _绑定可信父目录(
        monkeypatch,
        module,
        target,
        final_descriptor=91,
    )
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(
        module,
        "_read",
        lambda *_args: pytest.fail("硬链接应在读取内容前被拒绝"),
    )

    def 取状态(descriptor: int) -> SimpleNamespace:
        calls.append(("fstat", descriptor))
        return _安全文件(links=2) if descriptor == 91 else _安全目录()

    monkeypatch.setattr(module, "_fstat", 取状态)

    with pytest.raises(module.TrustedManagedPathError, match="文件不受信任"):
        module.read_optional_root_owned_regular_file_snapshot(target, max_bytes=128)


def test_可选读取遇到缺失父目录时关闭已打开的可信描述符(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "missing" / "resume.env").resolve()
    _绑定安全标志(monkeypatch, module)
    calls: list[object] = []
    descriptors = iter([10])
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(
        module,
        "_open_at",
        lambda name, _flags, **_kwargs: calls.append(("open", name))
        or (next(descriptors) if len(calls) == 1 else (_ for _ in ()).throw(FileNotFoundError())),
    )
    monkeypatch.setattr(module, "_fstat", lambda _descriptor: _安全目录())
    monkeypatch.setattr(module, "_close", lambda descriptor: calls.append(("close", descriptor)))

    assert module.read_optional_root_owned_regular_file(target, max_bytes=128) is None
    assert calls[-1] == ("close", 10)


def test_原子写入只通过同一父dirfd替换并同步目录(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "trusted" / "resume.env").resolve()
    _绑定安全标志(monkeypatch, module)
    calls, parent = _绑定可信父目录(monkeypatch, module, target, final_descriptor=91)
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(module, "_supports_write", lambda: True)
    monkeypatch.setattr(module, "_temporary_name", lambda: ".resume.tmp")
    monkeypatch.setattr(module, "_write", lambda descriptor, data: calls.append(("write", descriptor, data)) or len(data))
    monkeypatch.setattr(
        module,
        "_fchown",
        lambda descriptor, uid, gid: calls.append(("fchown", descriptor, uid, gid)),
    )
    monkeypatch.setattr(module, "_fchmod", lambda descriptor, mode: calls.append(("fchmod", descriptor, mode)))
    monkeypatch.setattr(module, "_fsync", lambda descriptor: calls.append(("fsync", descriptor)))
    monkeypatch.setattr(
        module,
        "_replace_at",
        lambda source, destination, source_parent, destination_parent: calls.append(
            ("replace", source, destination, source_parent, destination_parent)
        ),
    )
    monkeypatch.setattr(
        module,
        "_unlink_at",
        lambda name, descriptor: calls.append(("unlink", name, descriptor)),
    )

    module.write_root_owned_regular_file_atomic(target, b"safe\n", mode=0o640, uid=0, gid=1234)

    assert ("write", 91, b"safe\n") in calls
    assert ("fchown", 91, 0, 1234) in calls
    assert ("fchmod", 91, 0o640) in calls
    assert calls.index(("fchown", 91, 0, 1234)) < calls.index(("fchmod", 91, 0o640))
    assert ("replace", ".resume.tmp", target.name, parent, parent) in calls
    assert calls[-1] == ("close", parent)
    assert ("fsync", parent) in calls


def test_原子写入能力可使用rename_dir_fd替代replace_dir_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX rename 支持 dirfd 时，不应因 replace 不支持 dirfd 而拒绝原子写。"""
    import codev_platform._runtime_managed_file_fd as module

    def 支持目标函数(function: object) -> bool:
        return function in {module.os.open, module.os.mkdir, module.os.rename, module.os.unlink}

    monkeypatch.setattr(module, "_supports_dir_fd", 支持目标函数)
    monkeypatch.setattr(module.os, "fchown", lambda *_args: None, raising=False)

    assert module._supports_write() is True


def test_不可信父目录时不得创建临时文件或替换(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "trusted" / "resume.env").resolve()
    _绑定安全标志(monkeypatch, module)
    calls, _parent = _绑定可信父目录(monkeypatch, module, target, final_descriptor=91)
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(module, "_supports_write", lambda: True)
    original = module._fstat
    first_child = 11
    monkeypatch.setattr(
        module,
        "_fstat",
        lambda descriptor: SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_uid=0)
        if descriptor == first_child
        else original(descriptor),
    )
    monkeypatch.setattr(module, "_temporary_name", lambda: pytest.fail("不得创建临时文件"))

    with pytest.raises(module.TrustedManagedPathError):
        module.write_root_owned_regular_file_atomic(target, b"safe\n", mode=0o600)

    assert not any(item[0] == "replace" for item in calls)


def test_安全删除先验证叶子再用dirfd删除并同步父目录(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "trusted" / "resume.env").resolve()
    _绑定安全标志(monkeypatch, module)
    calls, parent = _绑定可信父目录(monkeypatch, module, target, final_descriptor=91)
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(module, "_supports_remove", lambda: True)
    monkeypatch.setattr(
        module,
        "_unlink_at",
        lambda name, descriptor: calls.append(("unlink", name, descriptor)),
    )
    monkeypatch.setattr(module, "_fsync", lambda descriptor: calls.append(("fsync", descriptor)))

    assert module.remove_root_owned_regular_file(target) is True

    assert ("unlink", target.name, parent) in calls
    assert ("fsync", parent) in calls
    assert calls[-1] == ("close", parent)


def test_缺少非阻塞打开标志时拒绝读取而不退化为路径访问(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "resume.env").resolve()
    _绑定安全标志(monkeypatch, module)
    monkeypatch.setattr(module.os, "O_NONBLOCK", 0, raising=False)
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(module, "_open_at", lambda *_args, **_kwargs: pytest.fail("不得打开路径"))

    with pytest.raises(module.TrustedManagedPathError):
        module.read_optional_root_owned_regular_file(target, max_bytes=128)


def test_缺少关闭继承标志时拒绝读取而不泄露描述符(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import codev_platform._runtime_managed_file_fd as module

    target = (tmp_path / "resume.env").resolve()
    _绑定安全标志(monkeypatch, module)
    monkeypatch.setattr(module.os, "O_CLOEXEC", 0, raising=False)
    monkeypatch.setattr(module, "_supports_read", lambda: True)
    monkeypatch.setattr(module, "_open_at", lambda *_args, **_kwargs: pytest.fail("不得打开路径"))

    with pytest.raises(module.TrustedManagedPathError):
        module.read_optional_root_owned_regular_file(target, max_bytes=128)
