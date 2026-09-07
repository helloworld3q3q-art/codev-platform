"""systemd 布局迁移条件文件操作测试。"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_条件创建能力不依赖replace_dir_fd(monkeypatch: pytest.MonkeyPatch) -> None:
    """linkat 条件首建不应被 replace(dir_fd) 能力误拦。"""
    from codev_platform.ops import systemd_unit_layout_filesystem as module

    def 支持目标函数(function: object) -> bool:
        return function in {module.os.open, module.os.mkdir, module.os.unlink, module.os.link}

    monkeypatch.setattr(module, "_supports_dir_fd", 支持目标函数)
    monkeypatch.setattr(module, "_temporary_file_flags", lambda: 0)
    monkeypatch.setattr(module.os, "fchown", lambda *_args: None, raising=False)
    monkeypatch.setattr(module.os, "fchmod", lambda *_args: None, raising=False)

    assert module._supports_conditional_create() is True


def test_条件创建使用link而不是覆盖替换(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from codev_platform.ops import systemd_unit_layout_filesystem as module

    target = (tmp_path / "codev-mcp-codegraph.service").resolve()
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(module, "_supports_conditional_create", lambda: True)
    monkeypatch.setattr(module, "_temporary_file_flags", lambda: 0)
    monkeypatch.setattr(module, "_open_trusted_parent", lambda *_args, **_kwargs: (11, target.name))
    monkeypatch.setattr(module, "_open_temporary_file", lambda *_args: (".tmp", 12))
    monkeypatch.setattr(module, "_write_all", lambda *_args: calls.append(("write",)))
    monkeypatch.setattr(module, "_fchown", lambda *_args: calls.append(("chown",)))
    monkeypatch.setattr(module, "_fchmod", lambda *_args: calls.append(("chmod",)))
    monkeypatch.setattr(module, "_fsync", lambda *_args: calls.append(("fsync",)))
    monkeypatch.setattr(module, "_unlink_at", lambda *args: calls.append(("unlink", *args)))
    monkeypatch.setattr(module, "_close_quietly", lambda *_args: None)
    monkeypatch.setattr(
        module,
        "_link_at",
        lambda *args: calls.append(("link", *args)),
    )

    module.create_root_owned_regular_file_atomic_if_absent(target, b"unit\n", mode=0o644)

    assert ("link", ".tmp", target.name, 11, 11) in calls
    assert not any(item[0] == "replace" for item in calls)


def test_条件移动预检发现原像变化时不得移动(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from codev_platform.ops import systemd_unit_layout_filesystem as module
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        RootOwnedRegularFileSnapshot,
    )

    source = (tmp_path / "codev-mcp-codegraph.service").resolve()
    destination = source.with_name(".codev-layout-backup")
    expected = RootOwnedRegularFileSnapshot(b"expected\n", 0o644, 0, 0, 1, 2)
    changed = RootOwnedRegularFileSnapshot(b"changed\n", 0o644, 0, 0, 1, 3)
    monkeypatch.setattr(module, "_supports_conditional_move", lambda: True)
    monkeypatch.setattr(module, "_open_trusted_parent", lambda *_args, **_kwargs: (11, source.name))
    monkeypatch.setattr(module, "_close_quietly", lambda *_args: None)
    monkeypatch.setattr(module, "_read_snapshot_at", lambda *_args, **_kwargs: changed)
    monkeypatch.setattr(
        module,
        "_rename_noreplace_at",
        lambda *_args: pytest.fail("原像变化时不得移动"),
    )

    with pytest.raises(module.TrustedManagedPathError, match="原像已变化"):
        module.move_root_owned_regular_file_if_snapshot(source, destination, expected)
