from __future__ import annotations

import ast
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from codev_platform.reindex import supervisor
from codev_platform.reindex.queue import FileSpoolQueue, Job
from tests.reindex_linux_gate import provision_test_gate


@pytest.fixture(autouse=True)
def _预置普通行为测试的Linux门禁(monkeypatch, tmp_path) -> None:
    """普通 worker 测试应验证业务契约，不应依赖真实 WSL 的 root 门禁运行态。"""
    provision_test_gate(monkeypatch, tmp_path)


class _PgLikeQueue:
    pass


_CONTROL_FLOW_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.With,
    ast.AsyncWith,
    ast.Try,
    ast.Match,
)


def _max_control_depth(node: ast.AST, depth: int = 0) -> int:
    current = depth + int(isinstance(node, _CONTROL_FLOW_NODES))
    children = (
        child for child in ast.iter_child_nodes(node)
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    )
    return max([current, *(_max_control_depth(child, current) for child in children)])


def _function_depths(module) -> dict[str, int]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    functions = (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    return {node.name: _max_control_depth(node) for node in functions}


def test_supervisor_and_launcher_control_flow_nesting_is_at_most_three():
    from codev_platform.reindex import worker_launcher

    for module in (supervisor, worker_launcher):
        excessive = {
            name: depth for name, depth in _function_depths(module).items()
            if depth > 3
        }
        assert excessive == {}


def test_supervisor_reexports_launcher_configuration_contract():
    from codev_platform.reindex import worker_launcher

    assert supervisor.WorkerSettings is worker_launcher.WorkerSettings
    assert supervisor.worker_settings is worker_launcher.worker_settings
    assert supervisor.should_auto_start is worker_launcher.should_auto_start


def test_worker_launcher_ports_are_explicit_and_narrow():
    from codev_platform.reindex import worker_launcher

    assert tuple(worker_launcher.WorkerLauncherPorts.__dataclass_fields__) == (
        "worker_status",
        "run_lock_running",
        "start_lock",
        "spawn_process",
        "new_owner_token",
        "record_spawned",
    )
    assert callable(worker_launcher.ensure_worker_running)


def test_worker_launcher_start_lock_recovers_stale_file(tmp_path):
    from codev_platform.reindex import worker_launcher

    lock_path = tmp_path / "worker-start.lock"
    lock_path.write_text("stale", encoding="ascii")
    stale_time = time.time() - 120
    os.utime(lock_path, (stale_time, stale_time))

    with worker_launcher.acquire_start_lock(lock_path, stale_sec=60) as acquired:
        assert acquired is True
        with worker_launcher.acquire_start_lock(lock_path, stale_sec=60) as second:
            assert second is False

    assert not lock_path.exists()


def test_supervisor_start_lock_path_uses_launcher_contract(tmp_path, monkeypatch):
    from codev_platform.reindex import worker_launcher

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))

    assert supervisor._start_lock_path() == worker_launcher.start_lock_path(
        supervisor.runtime_dir())


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


def test_run_lock回收缺少出生身份的旧锁即使pid已被复用(tmp_path, monkeypatch):
    """旧版仅凭存活 PID 的 lock 不能阻塞新 worker，防止系统进程复用 PID 后无限重启。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    supervisor._run_lock_path().write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "owner_token": "legacy-owner",
                "locked_at": time.time() - 60,
                "heartbeat_at": time.time() - 60,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "is_pid_running", lambda pid: pid == os.getpid())
    monkeypatch.setattr(
        supervisor,
        "_process_birth_identity",
        lambda _pid: "current-birth",
    )

    with supervisor.acquire_run_lock("new-token") as acquired:
        assert acquired is True
        lock = supervisor._lock_state()
        assert lock["owner_token"] == "new-token"
        assert lock["lock_schema_version"] == 1
        assert lock["process_birth_identity"] == "current-birth"


def test_run_lock_reclaims_live_pid_when_birth_identity_mismatches(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    supervisor._run_lock_path().write_text(
        json.dumps({
            "lock_schema_version": 1,
            "pid": os.getpid(),
            "owner_token": "stale-owner",
            "process_birth_identity": "stale-birth",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "_process_birth_identity", lambda _pid: "current-birth", raising=False)

    with supervisor.acquire_run_lock("new-token") as acquired:
        assert acquired is True
        assert supervisor._lock_state()["owner_token"] == "new-token"


def test_run_lock_release_requires_recorded_pid_to_match_current_process(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    lock_path = supervisor._run_lock_path()
    lock_path.write_text(
        json.dumps({
            "lock_schema_version": 1,
            "pid": os.getpid() + 1,
            "owner_token": "owner",
            "process_birth_identity": "current-birth",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "_process_birth_identity", lambda _pid: "current-birth")

    supervisor._release_run_lock(lock_path, "owner")

    assert lock_path.exists()


def test_non_linux未能确认活跃身份时运行锁拒绝回收(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    lock_path = supervisor._run_lock_path()
    lock_path.write_text(
        json.dumps({
            "lock_schema_version": 1,
            "pid": os.getpid(),
            "owner_token": "stale-owner",
            "process_birth_identity": "old-birth",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(supervisor, "is_pid_running", lambda _pid: True)
    monkeypatch.setattr(supervisor.sys, "platform", "darwin")

    with pytest.raises(supervisor.RunLockUnavailableError, match="活跃状态无法证明"):
        supervisor._reclaim_run_lock(lock_path)

    assert lock_path.exists()


def test_worker_status_rejects_legacy_state_without_birth_identity_and_health(tmp_path, monkeypatch):
    from codev_platform.reindex import status

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor._write_state({
        "state_schema_version": 2,
        "owner_token": "legacy-owner",
        "pid": os.getpid(),
        "status": "starting",
        "heartbeat_at": time.time(),
        "process_verified": True,
    })

    worker = supervisor.worker_status()
    summary = status.summarize(queue=FileSpoolQueue(tmp_path / "spool"))

    assert worker["running"] is False
    assert worker["process_verified"] is False
    assert summary["running"] is False
    assert summary["worker"]["running"] is False


def test_worker_status_rejects_state_with_mismatched_birth_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor._write_state({
        "state_schema_version": 3,
        "owner_token": "old-owner",
        "pid": os.getpid(),
        "status": "starting",
        "heartbeat_at": time.time(),
        "process_birth_identity": "stale-birth",
    })
    monkeypatch.setattr(supervisor, "_process_birth_identity", lambda _pid: "current-birth")

    worker = supervisor.worker_status()

    assert worker["running"] is False
    assert worker["process_verified"] is False


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


def test_state_v3_records_process_birth_identity_without_cmdline_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    token = supervisor.new_owner_token()

    supervisor.record_worker_start(token, mode="short", idle_exit_sec=12, heartbeat_sec=3)

    st = supervisor.worker_status()
    raw = supervisor.state_path().read_text(encoding="utf-8")
    assert st["state_schema_version"] == 3
    assert st["phase"] == "idle"
    assert st["pid"] == supervisor.os.getpid()
    assert st["process_executable"] == sys.executable
    assert st["process_cmd_hash"]
    assert "process_started_at" in st
    assert st["process_birth_identity"]
    assert st["process_verified"] is True
    assert "codev_platform.cli" not in raw
    assert raw.find(sys.argv[0]) == -1


def test_worker_status_records_execution_mode_for_isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    token = supervisor.new_owner_token()

    supervisor.record_worker_start(
        token,
        mode="forever",
        execution_mode="isolated",
    )

    assert supervisor.worker_status()["execution_mode"] == "isolated"


def test_health_refresh_success_clears_previous_failure_without_touching_last_result(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    token = supervisor.new_owner_token()
    job = Job("demo", "chroma", time.time())

    supervisor.record_worker_start(token, mode="short")
    supervisor.record_result(token, {
        "job_key": job.key,
        "project_id": job.project_id,
        "kind": job.kind,
        "status": "ok",
        "detail": "runner ok",
    })

    supervisor.record_job_event(token, None, "health_refresh_failed")
    failed = supervisor.worker_status()
    assert failed["health_failed"] is True
    assert failed["last_result"]["status"] == "ok"

    supervisor.record_job_event(token, None, "health_refresh")
    healed = supervisor.worker_status()
    assert healed["health_failed"] is False
    assert healed["last_result"]["status"] == "ok"


def test_old_owner_cannot_override_v2_phase_or_result(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    old = "old-token"
    new = "new-token"
    job = Job("demo", "chroma", time.time())

    supervisor.record_spawned(111, old, cwd=None, idle_exit_sec=10, heartbeat_sec=1)
    supervisor.record_spawned(222, new, cwd=None, idle_exit_sec=10, heartbeat_sec=1)
    supervisor.record_phase(new, "runner", job=job)
    supervisor.record_result(new, {"job_key": job.key, "project_id": job.project_id,
                                   "kind": job.kind, "status": "ok"})

    supervisor.record_phase(old, "stopped")
    supervisor.record_result(old, {"job_key": "old__job", "project_id": "old",
                                   "kind": "codegraph", "status": "failed"})

    st = supervisor.worker_status()
    assert st["owner_token"] == new
    assert st["phase"] == "runner"
    assert st["active_job"] == "demo__chroma"
    assert st["last_result"]["status"] == "ok"
    assert st["last_result"]["job_key"] == "demo__chroma"


def test_ensure_worker_running_spawns_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    spawned: list[list[str]] = []

    monkeypatch.setattr("codev_platform.mcp_serve._platform_runtime_python", lambda: py)
    monkeypatch.setattr(supervisor, "_spawn_worker_process",
                        lambda cmd, cwd, log_path, env=None: spawned.append(cmd) or 456)
    monkeypatch.setattr(supervisor, "is_pid_running", lambda pid: int(pid) == 456)
    monkeypatch.setattr(
        supervisor,
        "_process_birth_identity",
        lambda pid: f"test-birth-{pid}",
        raising=False,
    )

    first = supervisor.ensure_worker_running({}, cwd=tmp_path, idle_exit_sec=12, heartbeat_sec=3)
    second = supervisor.ensure_worker_running({}, cwd=tmp_path, idle_exit_sec=12, heartbeat_sec=3)

    assert first == {"action": "spawned", "pid": 456}
    assert second == {"action": "already-running", "pid": 456}
    assert len(spawned) == 1
    assert spawned[0][1:3] == ["-I", "-m"]
    assert "--idle-exit-sec" in spawned[0]
    assert "--owner-token" in spawned[0]
    assert supervisor.worker_status()["execution_mode"] == "isolated"


def test_ensure_worker_running_rejects_invalid_execution_mode_before_spawn(tmp_path, monkeypatch):
    from codev_platform.reindex.execution_mode import ExecutionModeError

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    monkeypatch.setattr("codev_platform.mcp_serve._platform_runtime_python", lambda: py)
    monkeypatch.setattr(
        supervisor,
        "_spawn_worker_process",
        lambda *_args, **_kwargs: pytest.fail("非法执行模式不得创建 worker 进程"),
    )

    with pytest.raises(ExecutionModeError):
        supervisor.ensure_worker_running(
            {"reindex": {"execution_mode": "unsupported"}},
            cwd=tmp_path,
        )


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


def test_ensure_worker_running_keeps_supervisor_start_lock_monkeypatch_path(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    lock_calls: list[str] = []

    @contextmanager
    def deny_start_lock():
        lock_calls.append("entered")
        yield False

    def should_not_spawn(*args, **kwargs):
        raise AssertionError("启动锁未获取时不得创建进程")

    monkeypatch.setattr(supervisor, "worker_status", lambda: {})
    monkeypatch.setattr(supervisor, "_lock_running", lambda: False)
    monkeypatch.setattr(supervisor, "_start_lock", deny_start_lock)
    monkeypatch.setattr(supervisor, "_spawn_worker_process", should_not_spawn)

    result = supervisor.ensure_worker_running({}, cwd=tmp_path)

    assert result == {"action": "already-starting"}
    assert lock_calls == ["entered"]


def test_运行锁严格读取未知时自动启动不生成候选进程(tmp_path, monkeypatch):
    """防止展示层 fail-soft 把真实 worker 的状态覆盖为候选 starting。"""
    from codev_platform.reindex import run_lock_protocol

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    supervisor.runtime_dir()
    supervisor._run_lock_path().write_bytes(b'{"lock_schema_version":1}')
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")

    def deny_read(*_args, **_kwargs) -> bytes:
        raise PermissionError("模拟运行锁读取被拒绝")

    monkeypatch.setattr(run_lock_protocol, "read_regular_file_bounded", deny_read)
    monkeypatch.setattr("codev_platform.mcp_serve._platform_runtime_python", lambda: python)
    monkeypatch.setattr(
        supervisor,
        "_spawn_worker_process",
        lambda *_args, **_kwargs: pytest.fail("运行锁未知时不得生成候选 worker"),
    )

    result = supervisor.ensure_worker_running(
        {"reindex": {"execution_mode": "isolated"}},
        cwd=tmp_path,
    )

    assert result == {"action": "already-running", "pid": None}


def test_heartbeat_thread_updates_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    token = supervisor.new_owner_token()
    supervisor.record_worker_start(token, mode="test")
    before = supervisor.worker_status()["heartbeat_at"]
    with supervisor.heartbeat_thread(token, 0.01):
        time.sleep(0.04)
    after = supervisor.worker_status()["heartbeat_at"]
    assert after > before
