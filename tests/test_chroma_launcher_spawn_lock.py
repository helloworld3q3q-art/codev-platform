"""Chroma 冷启动锁必须由操作系统绑定持有者，禁止 stale 路径 ABA。"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from codev_platform.chroma import spawn_lock as spawn_lock_module


def _launcher(tmp_path: Path, monkeypatch):
    venv = tmp_path / "venv"
    (venv / "Scripts").mkdir(parents=True)
    monkeypatch.setenv("PLATFORM_DOCS_VENV", str(venv))
    sys.modules.pop("codev_platform.chroma.launcher", None)
    launcher = importlib.import_module("codev_platform.chroma.launcher")
    monkeypatch.setattr(
        launcher,
        "DAEMON_LIFECYCLE_LOCK_PATH",
        tmp_path / "run" / "daemon.lifecycle.lock",
    )
    return launcher


def test_取得系统锁后才初始化_windows_首字节(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "run" / "daemon.spawn.lock"
    observed_sizes: list[int] = []

    def acquire_empty_file(stream) -> bool:
        stream.seek(0, os.SEEK_END)
        observed_sizes.append(stream.tell())
        return True

    monkeypatch.setattr(spawn_lock_module, "_try_lock", acquire_empty_file)
    monkeypatch.setattr(spawn_lock_module, "_unlock", lambda _stream: None)

    lock = spawn_lock_module.acquire_spawn_lock(path)

    assert lock is not None
    spawn_lock_module.release_spawn_lock(lock)
    assert observed_sizes == [0]
    assert path.read_bytes() == b"\0"


def test_旧持有者超时也不能被第二实例抢占或删除新锁(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launcher = _launcher(tmp_path, monkeypatch)
    path = tmp_path / "run" / "daemon.spawn.lock"
    monkeypatch.setattr(launcher, "SPAWN_LOCK_PATH", path)
    first = launcher._acquire_spawn_lock()
    assert first is not None
    os.utime(path, (time.time() - 3600, time.time() - 3600))

    second = launcher._acquire_spawn_lock()

    assert second is None
    launcher._release_spawn_lock(first)
    assert path.is_file()
    third = launcher._acquire_spawn_lock()
    assert third is not None
    launcher._release_spawn_lock(third)


def test_等待中的实例会在原持锁者退出后接管启动(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launcher = _launcher(tmp_path, monkeypatch)
    acquired = object()
    process = SimpleNamespace(poll=lambda: None)
    lock_attempts = iter((None, None, acquired))
    events: list[str] = []
    monkeypatch.setattr(launcher, "_check_daemon", lambda: False)
    monkeypatch.setattr(launcher, "_acquire_spawn_lock", lambda: next(lock_attempts))
    monkeypatch.setattr(
        launcher,
        "_spawn_daemon",
        lambda: events.append("spawn") or process,
    )
    monkeypatch.setattr(launcher, "_wait_daemon_handoff", lambda child, _timeout: child is process)
    monkeypatch.setattr(launcher, "_wait_daemon_ready", lambda _timeout: True)
    monkeypatch.setattr(
        launcher,
        "_release_spawn_lock",
        lambda lock: events.append("release") if lock is acquired else None,
    )
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(launcher.time, "monotonic", lambda: 10.0)

    assert launcher._ensure_daemon_ready() is True
    assert events == ["spawn", "release"]


def test_daemon_预热持生命周期锁时接管者不重复启动(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launcher = _launcher(tmp_path, monkeypatch)
    gate = object()
    events: list[str] = []
    monkeypatch.setattr(launcher, "_check_daemon", lambda: False)
    monkeypatch.setattr(
        launcher,
        "_daemon_lifecycle_lock_is_held",
        lambda: True,
        raising=False,
    )
    monkeypatch.setattr(launcher, "_spawn_daemon", lambda: events.append("spawn"))
    monkeypatch.setattr(
        launcher,
        "_release_spawn_lock",
        lambda lock: events.append("release") if lock is gate else None,
    )
    monkeypatch.setattr(
        launcher,
        "_wait_daemon_ready",
        lambda _timeout: events.append("wait") or True,
    )

    assert launcher._start_daemon_under_lock(gate) is True
    assert events == ["release", "wait"]


def test_launcher_确认_daemon_锁交接后才释放短期启动门(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launcher = _launcher(tmp_path, monkeypatch)
    gate = object()
    process = SimpleNamespace(poll=lambda: None)
    events: list[str] = []
    monkeypatch.setattr(launcher, "_check_daemon", lambda: False)
    monkeypatch.setattr(
        launcher,
        "_daemon_lifecycle_lock_is_held",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        launcher,
        "_spawn_daemon",
        lambda: events.append("spawn") or process,
    )
    monkeypatch.setattr(
        launcher,
        "_wait_daemon_handoff",
        lambda child, _timeout: events.append("handoff") or child is process,
        raising=False,
    )
    monkeypatch.setattr(
        launcher,
        "_release_spawn_lock",
        lambda lock: events.append("release") if lock is gate else None,
    )
    monkeypatch.setattr(
        launcher,
        "_wait_daemon_ready",
        lambda _timeout: events.append("wait") or True,
    )

    assert launcher._start_daemon_under_lock(gate) is True
    assert events == ["spawn", "handoff", "release", "wait"]


def test_等待接管有固定时限且不会无锁启动(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launcher = _launcher(tmp_path, monkeypatch)
    clock = iter((10.0, 10.0, 131.0))
    monkeypatch.setattr(launcher, "_check_daemon", lambda: False)
    monkeypatch.setattr(launcher, "_acquire_spawn_lock", lambda: None)
    monkeypatch.setattr(
        launcher,
        "_spawn_daemon",
        lambda: (_ for _ in ()).throw(AssertionError("无锁时禁止启动")),
    )
    monkeypatch.setattr(launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(launcher.time, "monotonic", lambda: next(clock))

    assert launcher._ensure_daemon_ready() is False


def test_真实持锁进程异常退出后等待者接管(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launcher = _launcher(tmp_path, monkeypatch)
    path = tmp_path / "run" / "daemon.spawn.lock"
    monkeypatch.setattr(launcher, "SPAWN_LOCK_PATH", path)
    monkeypatch.setattr(launcher, "SPAWN_LOCK_WAIT_TIMEOUT", 3.0)
    child_code = """
import sys,time
from pathlib import Path
from codev_platform.chroma.spawn_lock import acquire_spawn_lock
lock = acquire_spawn_lock(Path(sys.argv[1]))
assert lock is not None
print("ready", flush=True)
time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    timer: threading.Timer | None = None
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        events: list[str] = []
        process = SimpleNamespace(poll=lambda: None)
        monkeypatch.setattr(launcher, "_check_daemon", lambda: False)
        monkeypatch.setattr(
            launcher,
            "_spawn_daemon",
            lambda: events.append("spawn") or process,
        )
        monkeypatch.setattr(
            launcher,
            "_wait_daemon_handoff",
            lambda candidate, _timeout: candidate is process,
        )
        monkeypatch.setattr(launcher, "_wait_daemon_ready", lambda _timeout: True)
        timer = threading.Timer(0.1, child.terminate)
        timer.start()

        assert launcher._ensure_daemon_ready() is True
        assert events == ["spawn"]
        child.wait(timeout=5)
    finally:
        if timer is not None:
            timer.cancel()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_锁描述符被外部提前关闭时释放仍保持幂等(
    tmp_path: Path,
    monkeypatch,
) -> None:
    launcher = _launcher(tmp_path, monkeypatch)
    path = tmp_path / "run" / "daemon.spawn.lock"
    monkeypatch.setattr(launcher, "SPAWN_LOCK_PATH", path)
    lock = launcher._acquire_spawn_lock()
    assert lock is not None
    lock.stream.close()

    launcher._release_spawn_lock(lock)
    launcher._release_spawn_lock(lock)

    assert lock.released is True
