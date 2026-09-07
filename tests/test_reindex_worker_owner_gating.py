"""reindex worker 的 owner 就绪门禁测试。"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from codev_platform.reindex import supervisor
from tests.reindex_linux_gate import provision_test_gate


@pytest.fixture(autouse=True)
def _预置普通行为测试的Linux门禁(monkeypatch, tmp_path) -> None:
    """普通 worker 测试应验证业务契约，不应依赖真实 WSL 的 root 门禁运行态。"""

    provision_test_gate(monkeypatch, tmp_path)


@pytest.mark.parametrize(
    ("readiness", "expected_action"),
    [
        ("bootstrap_required", "bootstrap-required"),
        ("recovery_required", "owner-recovery-required"),
        ("binding_mismatch", "owner-binding-mismatch"),
        ("unavailable", "owner-unavailable"),
    ],
)
def test_launcher在启动锁内按owner就绪状态阻断自动拉起(
    monkeypatch,
    readiness,
    expected_action,
):
    from codev_platform.reindex import worker_launcher
    from codev_platform.reindex.owner_readiness import (
        OwnerReadiness,
        OwnerReadinessReport,
    )
    from codev_platform.reindex.runtime_owner import QueueBackendBinding

    events: list[str] = []
    queue = object()
    binding = QueueBackendBinding("file", "a" * 64)

    @contextmanager
    def start_lock():
        events.append("start-lock")
        yield True

    ports = worker_launcher.WorkerLauncherPorts(
        worker_status=lambda: events.append("status") or {},
        run_lock_running=lambda: False,
        start_lock=start_lock,
        spawn_process=lambda *_args, **_kwargs: pytest.fail("owner 未就绪不得启动进程"),
        new_owner_token=lambda: pytest.fail("owner 未就绪不得生成候选 token"),
        record_spawned=lambda *_args, **_kwargs: pytest.fail("owner 未就绪不得写 worker 状态"),
    )
    monkeypatch.setattr(
        worker_launcher,
        "queue_backend_binding",
        lambda actual_queue, cfg: (
            events.append("binding")
            or (
                binding
                if actual_queue is queue and cfg["reindex"]["execution_mode"] == "isolated"
                else None
            )
        ),
        raising=False,
    )
    monkeypatch.setattr(
        worker_launcher,
        "inspect_owner_readiness",
        lambda actual_binding: events.append("inspect") or OwnerReadinessReport(
            OwnerReadiness(readiness)
            if actual_binding is binding
            else pytest.fail("owner binding 不得漂移")
        ),
        raising=False,
    )

    result = worker_launcher.ensure_worker_running(
        {"reindex": {"execution_mode": "isolated"}},
        ports=ports,
        queue=queue,
    )

    assert result == {"action": expected_action}
    assert events == ["status", "start-lock", "status", "binding", "inspect"]


def test_launcher_owner就绪后才生成token并启动(monkeypatch, tmp_path):
    from codev_platform.reindex import worker_launcher
    from codev_platform.reindex.owner_readiness import (
        OwnerReadiness,
        OwnerReadinessReport,
    )
    from codev_platform.reindex.runtime_owner import QueueBackendBinding

    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    events: list[str] = []
    queue = object()
    binding = QueueBackendBinding("file", "b" * 64)

    @contextmanager
    def start_lock():
        events.append("start-lock")
        yield True

    ports = worker_launcher.WorkerLauncherPorts(
        worker_status=lambda: {},
        run_lock_running=lambda: False,
        start_lock=start_lock,
        spawn_process=lambda *_args, **_kwargs: events.append("spawn") or 456,
        new_owner_token=lambda: events.append("token") or "candidate-token",
        record_spawned=lambda *_args, **_kwargs: events.append("record"),
    )
    monkeypatch.setattr(
        worker_launcher,
        "queue_backend_binding",
        lambda actual_queue, _cfg: events.append("binding") or (
            binding if actual_queue is queue else pytest.fail("必须使用已入队的同一 queue")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        worker_launcher,
        "inspect_owner_readiness",
        lambda actual_binding: events.append("inspect") or OwnerReadinessReport(
            OwnerReadiness.READY
            if actual_binding is binding
            else pytest.fail("owner binding 不得漂移")
        ),
        raising=False,
    )
    monkeypatch.setattr("codev_platform.mcp_serve._platform_runtime_python", lambda: python)

    result = worker_launcher.ensure_worker_running(
        {"reindex": {"execution_mode": "isolated"}},
        ports=ports,
        queue=queue,
    )

    assert result == {"action": "spawned", "pid": 456}
    assert events == ["start-lock", "binding", "inspect", "token", "spawn", "record"]


def test_launcher将owner探针普通异常视为不可用(monkeypatch):
    from codev_platform.reindex import worker_launcher
    from codev_platform.reindex.runtime_owner import QueueBackendBinding

    binding = QueueBackendBinding("file", "c" * 64)

    @contextmanager
    def start_lock():
        yield True

    ports = worker_launcher.WorkerLauncherPorts(
        worker_status=lambda: {},
        run_lock_running=lambda: False,
        start_lock=start_lock,
        spawn_process=lambda *_args, **_kwargs: pytest.fail("探针异常不得启动进程"),
        new_owner_token=lambda: pytest.fail("探针异常不得生成候选 token"),
        record_spawned=lambda *_args, **_kwargs: pytest.fail("探针异常不得写 worker 状态"),
    )
    monkeypatch.setattr(
        worker_launcher,
        "queue_backend_binding",
        lambda _queue, _cfg: binding,
        raising=False,
    )
    monkeypatch.setattr(
        worker_launcher,
        "inspect_owner_readiness",
        lambda _binding: (_ for _ in ()).throw(OSError("owner probe unavailable")),
        raising=False,
    )

    assert worker_launcher.ensure_worker_running(
        {"reindex": {"execution_mode": "isolated"}},
        ports=ports,
        queue=object(),
    ) == {"action": "owner-unavailable"}


def test_launcher透传owner探针终止性异常(monkeypatch):
    from codev_platform.reindex import worker_launcher
    from codev_platform.reindex.runtime_owner import QueueBackendBinding

    binding = QueueBackendBinding("file", "d" * 64)

    @contextmanager
    def start_lock():
        yield True

    ports = worker_launcher.WorkerLauncherPorts(
        worker_status=lambda: {},
        run_lock_running=lambda: False,
        start_lock=start_lock,
        spawn_process=lambda *_args, **_kwargs: pytest.fail("终止性异常不得启动进程"),
        new_owner_token=lambda: pytest.fail("终止性异常不得生成候选 token"),
        record_spawned=lambda *_args, **_kwargs: pytest.fail("终止性异常不得写 worker 状态"),
    )
    monkeypatch.setattr(
        worker_launcher,
        "queue_backend_binding",
        lambda _queue, _cfg: binding,
        raising=False,
    )
    monkeypatch.setattr(
        worker_launcher,
        "inspect_owner_readiness",
        lambda _binding: (_ for _ in ()).throw(KeyboardInterrupt()),
        raising=False,
    )

    with pytest.raises(KeyboardInterrupt):
        worker_launcher.ensure_worker_running(
            {"reindex": {"execution_mode": "isolated"}},
            ports=ports,
            queue=object(),
        )


def test_supervisor原样转发已入队的queue(monkeypatch, tmp_path):
    queue = object()
    captured = {}

    def launcher(config, **kwargs):
        captured.update(config=config, **kwargs)
        return {"action": "owner-unavailable"}

    monkeypatch.setattr(supervisor, "_ensure_worker_running", launcher)

    result = supervisor.ensure_worker_running({}, cwd=tmp_path, queue=queue)

    assert result == {"action": "owner-unavailable"}
    assert captured["queue"] is queue
    assert captured["cwd"] == tmp_path
