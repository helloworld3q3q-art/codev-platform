"""隔离 worker 控制循环的启动顺序回归测试。"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from codev_platform.reindex.attempt_process import Deadline
from codev_platform.reindex.health_refresh import HealthRefreshReport


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now


class _Orchestrator:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def recover_incomplete(self) -> None:
        self._events.append("recover")

    def drain_once(self) -> int:
        self._events.append("claim")
        return 1


class _Health:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._requested: list[str] = []

    def request(self, project_id: str) -> None:
        self._requested.append(project_id)
        self._events.append(f"health:{project_id}")

    def flush(self, deadline: Deadline) -> HealthRefreshReport:
        assert isinstance(deadline, Deadline)
        self._events.append("health:flush")
        projects = tuple(self._requested)
        return HealthRefreshReport(projects, projects, (), True)


def test_startup恢复并刷新health且每次claim前审计legacy() -> None:
    from codev_platform.reindex.isolated_worker_loop import IsolatedWorkerLoop

    events: list[str] = []
    loop = IsolatedWorkerLoop(
        orchestrator=_Orchestrator(events),
        health_refresh=_Health(events),
        projects={"project-b", "project-a"},
        clock=_Clock(),
        health_timeout_sec=30.0,
        legacy_audit=lambda: events.append("legacy-audit"),
    )

    assert loop.drain_once() == 1
    assert events == [
        "recover",
        "health:project-a",
        "health:project-b",
        "health:flush",
        "legacy-audit",
        "claim",
    ]
    assert loop.drain_once() == 1
    assert events[-2:] == ["legacy-audit", "claim"]


def test_legacy_audit_failure阻止claim但不跳过既有启动health() -> None:
    from codev_platform.reindex.isolated_worker_loop import IsolatedWorkerLoop

    events: list[str] = []

    def _reject_legacy() -> None:
        events.append("legacy-audit")
        raise RuntimeError("需要迁移旧任务")

    loop = IsolatedWorkerLoop(
        orchestrator=_Orchestrator(events),
        health_refresh=_Health(events),
        projects={"project-a"},
        clock=_Clock(),
        health_timeout_sec=30.0,
        legacy_audit=_reject_legacy,
    )

    try:
        loop.drain_once()
    except RuntimeError as error:
        assert str(error) == "需要迁移旧任务"
    else:
        raise AssertionError("legacy 审计失败必须阻止 claim")
    assert events == ["recover", "health:project-a", "health:flush", "legacy-audit"]


def test_startup_refuses_claim_when_managed_health_projects_are_not_all_flushed() -> None:
    from codev_platform.reindex.isolated_worker_loop import (
        IsolatedWorkerLoop,
        IsolatedWorkerLoopError,
    )

    events: list[str] = []

    class _IncompleteHealth(_Health):
        def flush(self, deadline: Deadline) -> HealthRefreshReport:
            super().flush(deadline)
            return HealthRefreshReport(("project-a",), ("project-a",), (), True)

    loop = IsolatedWorkerLoop(
        orchestrator=_Orchestrator(events),
        health_refresh=_IncompleteHealth(events),
        projects={"project-a", "project-b"},
        clock=_Clock(),
        health_timeout_sec=30.0,
        legacy_audit=lambda: events.append("legacy-audit"),
    )

    with pytest.raises(IsolatedWorkerLoopError):
        loop.drain_once()
    assert events == [
        "recover",
        "health:project-a",
        "health:project-b",
        "health:flush",
    ]


def test_startup_allows_completed_health_report_with_regular_project_failure() -> None:
    from codev_platform.reindex.isolated_worker_loop import IsolatedWorkerLoop

    events: list[str] = []

    class _FailedHealth(_Health):
        def flush(self, deadline: Deadline) -> HealthRefreshReport:
            super().flush(deadline)
            return HealthRefreshReport(("project-a",), (), ("project-a",), True)

    loop = IsolatedWorkerLoop(
        orchestrator=_Orchestrator(events),
        health_refresh=_FailedHealth(events),
        projects={"project-a"},
        clock=_Clock(),
        health_timeout_sec=30.0,
        legacy_audit=lambda: events.append("legacy-audit"),
    )

    assert loop.drain_once() == 1
    assert events[-1] == "claim"


def test_projects_rejects_a_bare_string() -> None:
    from codev_platform.reindex.isolated_worker_loop import IsolatedWorkerLoop

    with pytest.raises(ValueError):
        IsolatedWorkerLoop(
            orchestrator=_Orchestrator([]),
            health_refresh=_Health([]),
            projects="project-a",
            clock=_Clock(),
            health_timeout_sec=30.0,
            legacy_audit=lambda: None,
        )


def test_每个drain写阶段都重新取得并释放维护许可() -> None:
    """常驻 worker 不能把共享许可持有到整个 forever 生命周期。"""
    from codev_platform.reindex.isolated_worker_loop import IsolatedWorkerLoop

    events: list[str] = []

    @contextmanager
    def _write_permit():
        events.append("permit-enter")
        yield True
        events.append("permit-exit")

    loop = IsolatedWorkerLoop(
        orchestrator=_Orchestrator(events),
        health_refresh=_Health(events),
        projects={"project-a"},
        clock=_Clock(),
        health_timeout_sec=30.0,
        legacy_audit=lambda: events.append("legacy-audit"),
        maintenance_write_permit=_write_permit,
    )

    assert loop.drain_once() == 1
    assert events == [
        "permit-enter",
        "recover",
        "health:project-a",
        "health:flush",
        "legacy-audit",
        "claim",
        "permit-exit",
    ]
    assert loop.drain_once() == 1
    assert events[-4:] == ["permit-enter", "legacy-audit", "claim", "permit-exit"]
