"""隔离编排向既有 supervisor 状态面的无秘密适配测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from codev_platform.reindex.supervisor_status_adapter import SupervisorStatusAdapter


def test_编排阶段映射到既有supervisor阶段且只传job对象() -> None:
    calls: list[tuple] = []
    adapter = SupervisorStatusAdapter(
        "instance-a",
        record_phase=lambda *args, **kwargs: calls.append((args, kwargs)),
        record_job_event=lambda *args, **kwargs: None,
    )
    job = SimpleNamespace(project_id="demo", kind="codegraph")
    claim = SimpleNamespace(job=job, claim_token="不得泄露")

    adapter.heartbeat("executing", claim)

    assert calls == [(("instance-a", "runner"), {"job": job})]


def test_未知编排阶段失败关闭而不写状态() -> None:
    calls: list[tuple] = []
    adapter = SupervisorStatusAdapter(
        "instance-a",
        record_phase=lambda *args, **kwargs: calls.append((args, kwargs)),
        record_job_event=lambda *args, **kwargs: None,
    )

    with pytest.raises(ValueError, match="未知|阶段"):
        adapter.heartbeat("terminating")

    assert calls == []


def test_health失败只写固定事件且不传handle或token() -> None:
    events: list[tuple] = []
    adapter = SupervisorStatusAdapter(
        "instance-a",
        record_phase=lambda *args, **kwargs: None,
        record_job_event=lambda *args, **kwargs: events.append((args, kwargs)),
    )

    adapter.set_health_failed(failed=True)

    assert events == [(("instance-a", None, "health_refresh_failed"), {})]
