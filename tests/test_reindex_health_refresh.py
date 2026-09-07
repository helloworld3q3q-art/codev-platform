from __future__ import annotations

import dataclasses
import inspect
import sys
from collections import deque
from pathlib import Path

import pytest

from codev_platform.reindex import health_refresh as health_refresh_module
from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ExecutionHandle,
    RecoveryReport,
    RecoveryState,
    TerminationReport,
    build_process_identity,
)
from codev_platform.reindex.attempts import ConfirmedProcessDeath
from codev_platform.reindex.health_refresh import (
    ContainedHealthRefresher,
    HealthOperationEntry,
    HealthOperationJournalPort,
    HealthRefreshCommand,
    HealthRefreshFatalError,
    HealthRefreshPort,
    HealthRefreshReport,
    HealthStatusPort,
)


def _handle(operation_id: str = "health-operation") -> ExecutionHandle:
    native_ref = f"health-native:{operation_id}"
    return ExecutionHandle(
        attempt_id=operation_id,
        pid=4321,
        process_identity=build_process_identity(
            pid=4321,
            native_ref=native_ref,
            birth_marker="health-birth:1",
        ),
        containment_kind="test_health_containment",
        native_ref=native_ref,
        started_at=100.0,
    )


def _death_proof(handle: ExecutionHandle) -> ConfirmedProcessDeath:
    return ConfirmedProcessDeath(
        process_identity=handle.process_identity,
        containment_kind=handle.containment_kind,
        confirmed_at=101.0,
        evidence="测试 containment 已空",
    )


class _Backend:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.poll_values: deque[int | None] = deque([0])
        self.activate_error: Exception | None = None
        self.activate_with_proof = False
        self.prepare_error: Exception | None = None
        self.terminate_error: Exception | None = None
        self.termination_confirmed = True
        self.termination_proof: ConfirmedProcessDeath | None = None
        self.recovery_report: RecoveryReport | None = None

    def prepare(
        self,
        *,
        attempt_id: str,
        argv,
        cwd: Path,
        bootstrap_log: Path,
        deadline: Deadline,
    ) -> ExecutionHandle:
        del cwd, bootstrap_log, deadline
        project_id = argv[-1]
        self.events.append(f"prepare:{project_id}")
        if self.prepare_error is not None:
            raise self.prepare_error
        return _handle(attempt_id)

    def activate(self, handle: ExecutionHandle, deadline: Deadline) -> None:
        del deadline
        self.events.append(f"activate:{handle.attempt_id}")
        if self.activate_with_proof:
            raise AttemptProcessStartError(
                handle=handle,
                death_proof=_death_proof(handle),
                retryable=True,
                note="激活失败但死亡已确认",
            )
        if self.activate_error is not None:
            raise self.activate_error

    def poll(self, handle: ExecutionHandle) -> int | None:
        self.events.append(f"poll:{handle.attempt_id}")
        value = self.poll_values[0]
        if len(self.poll_values) > 1:
            self.poll_values.popleft()
        return value

    def terminate(
        self,
        handle: ExecutionHandle,
        *,
        grace_sec: float,
        deadline: Deadline,
    ) -> TerminationReport:
        del grace_sec, deadline
        self.events.append(f"terminate:{handle.attempt_id}")
        if self.terminate_error is not None:
            raise self.terminate_error
        proof = self.termination_proof
        if proof is None and self.termination_confirmed:
            proof = _death_proof(handle)
        return TerminationReport(
            requested_at=100.0,
            finished_at=101.0,
            graceful=False,
            forced=True,
            confirmed_dead=proof is not None,
            death_proof=proof,
            note="测试终止结果",
        )

    def recover_handle(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> RecoveryReport:
        del deadline
        self.events.append(f"recover:{handle.attempt_id}")
        if self.recovery_report is None:
            return RecoveryReport(
                RecoveryState.ACTIVE,
                handle,
                None,
                "测试恢复到活动进程",
            )
        return self.recovery_report


class _Journal:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.entry: HealthOperationEntry | None = None
        self.save_error: Exception | None = None

    def load_health(self) -> HealthOperationEntry | None:
        self.events.append("load_health")
        return self.entry

    def save_health(self, entry: HealthOperationEntry) -> None:
        self.events.append(f"save:{entry.operation_id}")
        if self.save_error is not None:
            raise self.save_error
        self.entry = entry

    def clear_health(self, *, operation_id: str) -> None:
        self.events.append(f"clear:{operation_id}")
        assert self.entry is not None
        assert self.entry.operation_id == operation_id
        self.entry = None


class _Status:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.values: list[bool] = []

    def set_health_failed(self, *, failed: bool) -> None:
        self.events.append(f"health_failed:{failed}")
        self.values.append(failed)


def _command_factory(tmp_path: Path, events: list[str]):
    def build(project_id: str) -> HealthRefreshCommand:
        events.append(f"command:{project_id}")
        return HealthRefreshCommand(
            argv=(str(Path(sys.executable).resolve()), "-c", "pass", project_id),
            cwd=tmp_path.resolve(),
            bootstrap_log=(tmp_path / f"health-{project_id}.log").resolve(),
        )

    return build


def _refresher(
    tmp_path: Path,
    *,
    events: list[str] | None = None,
    backend: _Backend | None = None,
    journal: _Journal | None = None,
    status: _Status | None = None,
    timeout_sec: float = 0.05,
    poll_interval_sec: float = 0.001,
    command_factory=None,
) -> tuple[ContainedHealthRefresher, _Backend, _Journal, _Status, list[str]]:
    resolved_events = events if events is not None else []
    resolved_backend = backend or _Backend(resolved_events)
    resolved_journal = journal or _Journal(resolved_events)
    resolved_status = status or _Status(resolved_events)
    refresher = ContainedHealthRefresher(
        process_backend=resolved_backend,
        journal=resolved_journal,
        status=resolved_status,
        command_factory=command_factory or _command_factory(tmp_path, resolved_events),
        timeout_sec=timeout_sec,
        kill_grace_sec=0.0,
        poll_interval_sec=poll_interval_sec,
    )
    return (
        refresher,
        resolved_backend,
        resolved_journal,
        resolved_status,
        resolved_events,
    )


def test_health_contracts_are_narrow_and_explicit() -> None:
    load = inspect.signature(HealthOperationJournalPort.load_health)
    save = inspect.signature(HealthOperationJournalPort.save_health)
    clear = inspect.signature(HealthOperationJournalPort.clear_health)
    status = inspect.signature(HealthStatusPort.set_health_failed)
    request = inspect.signature(HealthRefreshPort.request)
    flush = inspect.signature(HealthRefreshPort.flush)
    recover = inspect.signature(HealthRefreshPort.recover)

    assert tuple(load.parameters) == ("self",)
    assert tuple(save.parameters) == ("self", "entry")
    assert tuple(clear.parameters) == ("self", "operation_id")
    assert tuple(status.parameters) == ("self", "failed")
    assert tuple(request.parameters) == ("self", "project_id")
    assert tuple(flush.parameters) == ("self", "deadline")
    assert tuple(recover.parameters) == ("self", "deadline")


def test_health_values_are_strict_frozen_and_bind_operation_to_handle(
    tmp_path: Path,
) -> None:
    handle = _handle()
    entry = HealthOperationEntry(1, handle.attempt_id, "project-a", handle, 100.0, 10.0)
    command = HealthRefreshCommand(
        (str(Path(sys.executable).resolve()), "-c", "pass"),
        tmp_path.resolve(),
        (tmp_path / "health.log").resolve(),
    )
    report = HealthRefreshReport(("project-a",), ("project-a",), (), True)

    assert not hasattr(entry, "__dict__")
    assert command.argv[0] == str(Path(sys.executable).resolve())
    assert report.containment_confirmed_dead is True
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.project_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="operation|handle"):
        HealthOperationEntry(1, "other-operation", "project-a", handle, 100.0, 10.0)
    with pytest.raises(ValueError, match="重复|互斥"):
        HealthRefreshReport(("project-a",), (), ("project-a", "project-a"), True)


def test_health_requests_are_deduplicated_and_flushed_in_first_request_order(
    tmp_path: Path,
) -> None:
    refresher, backend, journal, status, events = _refresher(tmp_path)
    backend.poll_values = deque([0])
    refresher.request("project-a")
    refresher.request("project-b")
    refresher.request("project-a")

    report = refresher.flush(Deadline.start(1.0))

    assert report == HealthRefreshReport(
        ("project-a", "project-b"),
        ("project-a", "project-b"),
        (),
        True,
    )
    assert [event for event in events if event.startswith("prepare:")] == [
        "prepare:project-a",
        "prepare:project-b",
    ]
    assert status.values == [False]
    assert journal.entry is None


def test_health_handle_is_saved_before_activation(tmp_path: Path) -> None:
    refresher, _backend, _journal, _status, events = _refresher(tmp_path)
    refresher.request("project-a")

    refresher.flush(Deadline.start(1.0))

    save_index = next(index for index, value in enumerate(events) if value.startswith("save:"))
    activate_index = next(
        index for index, value in enumerate(events) if value.startswith("activate:")
    )
    assert events.index("prepare:project-a") < save_index < activate_index


def test_health_warning_exit_code_is_recorded_as_success(tmp_path: Path) -> None:
    """health 的告警退出码不应被误记为健康刷新失败。"""
    refresher, backend, journal, status, _events = _refresher(tmp_path)
    backend.poll_values = deque([2])
    refresher.request("project-a")

    report = refresher.flush(Deadline.start(1.0))

    assert report == HealthRefreshReport(("project-a",), ("project-a",), (), True)
    assert status.values == [False]
    assert journal.entry is None


def test_health_command_factory_failure_marks_failed_without_process(
    tmp_path: Path,
) -> None:
    def fail_command(_project_id: str):
        raise ValueError("命令配置缺失")

    refresher, _backend, _journal, status, events = _refresher(
        tmp_path,
        command_factory=fail_command,
    )
    refresher.request("project-a")

    report = refresher.flush(Deadline.start(1.0))

    assert report.failed_projects == ("project-a",)
    assert status.values == [True]
    assert not any(value.startswith("prepare:") for value in events)


def test_health_prepare_failure_without_handle_marks_failed_without_activation(
    tmp_path: Path,
) -> None:
    refresher, backend, _journal, status, events = _refresher(tmp_path)
    backend.prepare_error = AttemptProcessStartError(
        handle=None,
        death_proof=None,
        retryable=True,
        note="创建进程前失败",
    )
    refresher.request("project-a")

    report = refresher.flush(Deadline.start(1.0))

    assert report.failed_projects == ("project-a",)
    assert status.values == [True]
    assert not any(value.startswith("activate:") for value in events)


def test_health_handle_save_failure_terminates_and_never_activates(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    journal = _Journal(events)
    journal.save_error = OSError("磁盘写失败")
    refresher, _backend, _journal, status, events = _refresher(
        tmp_path,
        events=events,
        journal=journal,
    )
    refresher.request("project-a")

    report = refresher.flush(Deadline.start(1.0))

    assert report.failed_projects == ("project-a",)
    assert any(value.startswith("terminate:") for value in events)
    assert not any(value.startswith("activate:") for value in events)
    assert status.values == [True]


def test_health_activate_error_reuses_exact_death_proof_without_second_terminate(
    tmp_path: Path,
) -> None:
    refresher, backend, journal, status, events = _refresher(tmp_path)
    backend.activate_with_proof = True
    backend.terminate_error = AssertionError("已有证明时不得重复终止")
    refresher.request("project-a")

    report = refresher.flush(Deadline.start(1.0))

    assert report.failed_projects == ("project-a",)
    assert journal.entry is None
    assert status.values == [True]
    assert not any(value.startswith("terminate:") for value in events)


def test_health_timeout_terminates_within_outer_deadline_and_marks_failed(
    tmp_path: Path,
) -> None:
    refresher, backend, journal, status, events = _refresher(
        tmp_path,
        timeout_sec=0.001,
        poll_interval_sec=0.001,
    )
    backend.poll_values = deque([None])
    refresher.request("project-a")

    report = refresher.flush(Deadline.start(0.1))

    assert report.failed_projects == ("project-a",)
    assert any(value.startswith("terminate:") for value in events)
    assert journal.entry is None
    assert status.values == [True]


def test_health_unconfirmed_termination_keeps_journal_and_raises_fatal(
    tmp_path: Path,
) -> None:
    refresher, backend, journal, status, _events = _refresher(tmp_path)
    backend.poll_values = deque([1])
    backend.termination_confirmed = False
    refresher.request("project-a")

    with pytest.raises(HealthRefreshFatalError, match="死亡|containment"):
        refresher.flush(Deadline.start(1.0))

    assert journal.entry is not None
    assert status.values == [True]


def test_health_错配死亡证明必须保留_journal并停止(tmp_path: Path) -> None:
    refresher, backend, journal, status, events = _refresher(tmp_path)
    foreign_ref = "foreign-health-native"
    foreign_identity = build_process_identity(
        pid=9876,
        native_ref=foreign_ref,
        birth_marker="foreign-health-birth",
    )
    backend.poll_values = deque([1])
    backend.termination_proof = ConfirmedProcessDeath(
        foreign_identity,
        "foreign-health-containment",
        101.0,
        "外来 health containment 已死亡",
    )
    refresher.request("project-a")

    with pytest.raises(HealthRefreshFatalError, match="死亡|containment|handle"):
        refresher.flush(Deadline.start(1.0))

    assert journal.entry is not None
    assert status.values == [True]
    assert not any(value.startswith("clear:") for value in events)


def test_health_recovery_active_terminates_requeues_and_clears_record(
    tmp_path: Path,
) -> None:
    refresher, backend, journal, status, events = _refresher(tmp_path)
    handle = _handle("persisted-health")
    journal.entry = HealthOperationEntry(
        1,
        handle.attempt_id,
        "project-a",
        handle,
        handle.started_at,
        10.0,
    )

    refresher.recover(Deadline.start(1.0))
    refresher.request("project-a")
    report = refresher.flush(Deadline.start(1.0))

    recover_index = events.index("recover:persisted-health")
    prepare_index = events.index("prepare:project-a")
    assert recover_index < prepare_index
    assert report.attempted_projects == ("project-a",)
    assert status.values == [True, False]
    assert journal.entry is None
    assert any(value == "terminate:persisted-health" for value in events)
    assert backend.recovery_report is None


def test_health_recovery_confirmed_dead_does_not_guess_success(
    tmp_path: Path,
) -> None:
    refresher, backend, journal, status, events = _refresher(tmp_path)
    handle = _handle("persisted-dead")
    journal.entry = HealthOperationEntry(
        1,
        handle.attempt_id,
        "project-a",
        handle,
        handle.started_at,
        10.0,
    )
    backend.recovery_report = RecoveryReport(
        RecoveryState.CONFIRMED_DEAD,
        handle,
        _death_proof(handle),
        "测试历史 health 已死",
    )

    refresher.recover(Deadline.start(1.0))

    assert status.values == [True]
    assert journal.entry is None
    assert "terminate:persisted-dead" not in events
    refresher.request("project-a")
    report = refresher.flush(Deadline.start(1.0))
    assert report.attempted_projects == ("project-a",)


@pytest.mark.parametrize("state", [RecoveryState.UNCONFIRMED, RecoveryState.NEVER_STARTED])
def test_health_recovery_ambiguous_state_stops_without_clearing(
    tmp_path: Path,
    state: RecoveryState,
) -> None:
    refresher, backend, journal, status, events = _refresher(tmp_path)
    handle = _handle("persisted-ambiguous")
    journal.entry = HealthOperationEntry(
        1,
        handle.attempt_id,
        "project-a",
        handle,
        handle.started_at,
        10.0,
    )
    backend.recovery_report = RecoveryReport(state, None, None, "测试恢复有歧义")

    with pytest.raises(HealthRefreshFatalError, match="恢复|死亡"):
        refresher.recover(Deadline.start(1.0))

    assert journal.entry is not None
    assert status.values == [True]
    assert not any(value.startswith("clear:") for value in events)


def test_health_flush_with_expired_deadline_defers_without_spawning(
    tmp_path: Path,
) -> None:
    refresher, _backend, _journal, status, events = _refresher(tmp_path)
    refresher.request("project-a")

    deferred = refresher.flush(Deadline(0.0))
    completed = refresher.flush(Deadline.start(1.0))

    assert deferred.attempted_projects == ()
    assert completed.attempted_projects == ("project-a",)
    assert [value for value in events if value == "prepare:project-a"] == [
        "prepare:project-a"
    ]
    assert status.values == [False]


def test_health_budget_exhausted_after_flush_check_marks_failed_and_defers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    refresher, _backend, _journal, status, events = _refresher(tmp_path)
    deadline = Deadline.start(1.0)
    monkeypatch.setattr(Deadline, "expired", lambda _self: False)
    monkeypatch.setattr(
        health_refresh_module.time,
        "monotonic",
        lambda: deadline.expires_at + 1.0,
    )
    refresher.request("project-a")

    report = refresher.flush(deadline)

    assert report.failed_projects == ("project-a",)
    assert status.values == [True]
    assert "prepare:project-a" not in events


def test_health_module_has_no_supervisor_queue_manifest_artifact_or_thread_import() -> None:
    source = inspect.getsource(sys.modules[ContainedHealthRefresher.__module__])

    for forbidden in ("supervisor", "queue", "manifest", "artifact", "threading"):
        assert forbidden not in source
