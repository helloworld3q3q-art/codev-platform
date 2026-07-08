from __future__ import annotations

import json
import time

from codev_platform.reindex import supervisor
from codev_platform.reindex.queue import FileSpoolQueue, Job


class _PgLikeQueue:
    pass


def test_should_auto_start_file_queue_only_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    assert supervisor.should_auto_start(FileSpoolQueue(tmp_path / "spool"), {}) is True
    assert supervisor.should_auto_start(_PgLikeQueue(), {}) is False
    assert supervisor.should_auto_start(_PgLikeQueue(), {"reindex": {"worker_auto_start_pg": True}}) is True


def test_run_lock_blocks_second_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    token = supervisor.new_owner_token()
    with supervisor.acquire_run_lock(token) as acquired:
        assert acquired is True
        with supervisor.acquire_run_lock(supervisor.new_owner_token()) as second:
            assert second is False
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as third:
        assert third is True


def test_run_lock_recovers_dead_pid_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    supervisor._run_lock_path().write_text(
        json.dumps({"pid": 999999, "owner_token": "dead"}), encoding="utf-8")
    monkeypatch.setattr(supervisor, "is_pid_running", lambda pid: False)

    with supervisor.acquire_run_lock("new-token") as acquired:
        assert acquired is True
        assert supervisor._lock_state()["owner_token"] == "new-token"


def test_state_token_prevents_old_exit_overwriting_new_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    old = "old-token"
    new = "new-token"
    supervisor.record_spawned(111, old, cwd=None, idle_exit_sec=10, heartbeat_sec=1)
    supervisor.record_spawned(222, new, cwd=None, idle_exit_sec=10, heartbeat_sec=1)

    supervisor.record_worker_exit(old, "idle")

    st = supervisor.worker_status()
    assert st["owner_token"] == new
    assert st["pid"] == 222
    assert st["status"] == "starting"


def test_state_token_prevents_old_heartbeat_and_job_event_overwriting_new_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    old = "old-token"
    new = "new-token"
    supervisor.record_worker_start(new, mode="short")

    supervisor.record_heartbeat(old)
    supervisor.record_job_event(old, Job("old-proj", "chroma", time.time()), "running")

    st = supervisor.worker_status()
    assert st["owner_token"] == new
    assert st["mode"] == "short"
    assert "last_job" not in st


def test_ensure_worker_running_spawns_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    spawned: list[list[str]] = []

    monkeypatch.setattr("codev_platform.mcp_serve._venv_python", lambda cfg: py)
    monkeypatch.setattr(supervisor, "_spawn_worker_process",
                        lambda cmd, cwd, log_path, env=None: spawned.append(cmd) or 456)
    monkeypatch.setattr(supervisor, "is_pid_running", lambda pid: int(pid) == 456)

    first = supervisor.ensure_worker_running({}, cwd=tmp_path, idle_exit_sec=12, heartbeat_sec=3)
    second = supervisor.ensure_worker_running({}, cwd=tmp_path, idle_exit_sec=12, heartbeat_sec=3)

    assert first == {"action": "spawned", "pid": 456}
    assert second == {"action": "already-running", "pid": 456}
    assert len(spawned) == 1
    assert "--idle-exit-sec" in spawned[0]
    assert "--owner-token" in spawned[0]


def test_spawn_worker_process_uses_runtime_spawn(tmp_path, monkeypatch):
    captured = {}

    def fake_spawn(cmd, cwd, log_path, env=None):
        captured.update({"cmd": cmd, "cwd": cwd, "log_path": log_path, "env": env})
        return 789

    monkeypatch.setattr("codev_platform.mcp_runtime.spawn_detached", fake_spawn)

    pid = supervisor._spawn_worker_process(["py", "-m", "worker"], str(tmp_path),
                                           tmp_path / "worker.log", env={"X": "1"})

    assert pid == 789
    assert captured == {
        "cmd": ["py", "-m", "worker"],
        "cwd": str(tmp_path),
        "log_path": tmp_path / "worker.log",
        "env": {"X": "1"},
    }


def test_ensure_worker_running_returns_already_starting_when_start_lock_held(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    spawned: list[list[str]] = []
    monkeypatch.setattr(supervisor, "_spawn_worker_process",
                        lambda cmd, cwd, log_path, env=None: spawned.append(cmd) or 456)

    with supervisor._start_lock() as acquired:
        assert acquired is True
        result = supervisor.ensure_worker_running({}, cwd=tmp_path)

    assert result == {"action": "already-starting"}
    assert spawned == []


def test_heartbeat_thread_updates_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    token = supervisor.new_owner_token()
    supervisor.record_worker_start(token, mode="test")
    before = supervisor.worker_status()["heartbeat_at"]
    with supervisor.heartbeat_thread(token, 0.01):
        time.sleep(0.04)
    after = supervisor.worker_status()["heartbeat_at"]
    assert after > before
