"""Chroma daemon 入口必须先取得生命周期锁，再加载重量服务。"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from codev_platform.chroma.spawn_lock import acquire_spawn_lock, release_spawn_lock


def _entry():
    return importlib.import_module("codev_platform.chroma.daemon_entry")


def test_生命周期锁被占用时失败竞争者不加载重量服务(tmp_path: Path, monkeypatch) -> None:
    entry = _entry()
    path = tmp_path / "run" / "daemon.lifecycle.lock"
    holder = acquire_spawn_lock(path)
    assert holder is not None
    loaded = False

    def forbidden_loader():
        nonlocal loaded
        loaded = True
        raise AssertionError("失败竞争者不得加载重量服务")

    monkeypatch.setattr(entry, "DAEMON_LIFECYCLE_LOCK_PATH", path)
    monkeypatch.setattr(entry, "LIFECYCLE_LOCK_WAIT_TIMEOUT", 0.0)
    monkeypatch.setattr(entry, "_load_server_main", forbidden_loader)
    monkeypatch.setattr(entry, "require_managed_mcp_service", lambda _kind: "unit")
    try:
        assert entry.run() == 1
        assert loaded is False
    finally:
        release_spawn_lock(holder)


def test_daemon_运行全程持有生命周期锁并在正常退出后释放(
    tmp_path: Path,
    monkeypatch,
) -> None:
    entry = _entry()
    path = tmp_path / "run" / "daemon.lifecycle.lock"
    observed: list[str] = []

    async def serve() -> None:
        contender = acquire_spawn_lock(path)
        assert contender is None
        observed.append("serve")

    def load_server_main():
        contender = acquire_spawn_lock(path)
        assert contender is None
        observed.append("load")
        return serve

    monkeypatch.setattr(entry, "DAEMON_LIFECYCLE_LOCK_PATH", path)
    monkeypatch.setattr(entry, "LIFECYCLE_LOCK_WAIT_TIMEOUT", 0.1)
    monkeypatch.setattr(entry, "_load_server_main", load_server_main)
    monkeypatch.setattr(entry, "require_managed_mcp_service", lambda _kind: "unit")

    assert entry.run() == 0
    assert observed == ["load", "serve"]
    successor = acquire_spawn_lock(path)
    assert successor is not None
    release_spawn_lock(successor)


def test_daemon_竞争生命周期锁前验证systemd固定归属(monkeypatch) -> None:
    entry = _entry()

    monkeypatch.setattr(
        entry,
        "require_managed_mcp_service",
        lambda _kind: (_ for _ in ()).throw(RuntimeError("固定 unit 归属拒绝")),
        raising=False,
    )
    monkeypatch.setattr(
        entry,
        "acquire_spawn_lock_until",
        lambda *_args: pytest.fail("归属门禁必须早于生命周期锁"),
    )

    with pytest.raises(RuntimeError, match="固定 unit"):
        entry.run()
