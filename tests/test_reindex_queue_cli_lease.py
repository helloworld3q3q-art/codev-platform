"""reindex-queue CLI 的 break-lease 测试。"""

from __future__ import annotations

import time

import pytest

from codev_platform.cli import build_parser
from codev_platform.reindex.queue import Job, QueueSnapshot
from tests.reindex_queue_cli_support import (
    _FakeQueue,
    _confirmed_queue_window,
    _run_cli,
    rq,
)

pytestmark = pytest.mark.usefixtures(_confirmed_queue_window.__name__)


def test_break_lease_yes_clears_active_and_keeps_pending(monkeypatch, capsys):
    pending = Job("demo-proj", "chroma", time.time() - 100)
    active = Job("demo-proj", "chroma", time.time() - 200, token="lease-1")
    q = _FakeQueue(pending=[pending], active=[active])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **kw: {
            "running": False,
            "worker": {"running": False, "pid": None},
            "phase": "stopped",
            "phase_age_sec": 10,
            "expired_active_count": 1,
        },
    )

    rc = _run_cli(["break-lease", "demo-proj", "--kind", "chroma", "--yes"])

    assert rc == 0
    assert q.broken == ["demo-proj__chroma"]
    assert [j.key for j in q.pending_jobs] == ["demo-proj__chroma"]
    assert q.active_jobs == []
    assert "break active lease" in capsys.readouterr().out


def test_break_lease确认写入只在受控维护窗口内打开队列(monkeypatch, capsys):
    active = Job("demo-proj", "chroma", time.time() - 200, token="lease-1")
    q = _FakeQueue(active=[active])
    events: list[str] = []

    def _confirmed_action(*, input_loader, action):
        events.append("window")
        return action(input_loader())

    monkeypatch.setattr(rq, "run_confirmed_maintenance_action", _confirmed_action, raising=False)
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **kwargs: (
            events.append("queue") or q
            if kwargs == {"fail_soft": False}
            else pytest.fail("确认释放必须严格打开队列")
        ),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **_kwargs: {
            "running": False,
            "worker": {"running": False, "pid": None},
            "phase": "stopped",
            "phase_age_sec": 10,
            "expired_active_count": 1,
        },
    )

    assert _run_cli(["break-lease", "demo-proj", "--kind", "chroma", "--yes"]) == 0
    assert events == ["window", "queue"]
    assert q.broken == [active.key]
    assert "break active lease: done" in capsys.readouterr().out


def test_break_lease_yes_opens_queue_fail_closed(monkeypatch):
    seen = {}
    q = _FakeQueue(active=[Job("demo-proj", "chroma", time.time() - 200, token="lease-1")])

    def _open_default_queue(**kw):
        seen.update(kw)
        return q

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", _open_default_queue)
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **kw: {
            "running": False,
            "worker": {"running": False, "pid": None},
            "phase": "stopped",
            "phase_age_sec": 10,
            "expired_active_count": 1,
        },
    )

    assert _run_cli(["break-lease", "demo-proj", "--kind", "chroma", "--yes"]) == 0
    assert seen["fail_soft"] is False


def test_break_lease_yes_refuses_healthy_running_worker(monkeypatch, capsys):
    q = _FakeQueue(active=[Job("demo-proj", "chroma", time.time() - 400, token="lease-1")])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **kw: {
            "running": True,
            "worker": {"running": True, "pid": 123, "heartbeat_age_sec": 5},
            "phase": "runner",
            "phase_age_sec": 30,
            "expired_active_count": 0,
        },
    )

    assert _run_cli(["break-lease", "--project", "demo-proj", "--kind", "chroma", "--yes"]) == 1

    err = capsys.readouterr().err
    assert "status" in err
    assert q.broken == []


def test_break_lease_yes_rechecks_snapshot_before_break(monkeypatch, capsys):
    now = time.time()
    first_target = Job(
        "demo-proj",
        "chroma",
        now - 400,
        token="lease-old",
        lease_expires_at=now - 10,
    )
    second_target = Job(
        "demo-proj",
        "chroma",
        now - 60,
        token="lease-new",
        lease_expires_at=now + 300,
    )

    class _RecheckQueue(_FakeQueue):
        def __init__(self):
            super().__init__()
            self.snapshot_calls = 0

        def snapshot(self):
            self.snapshot_calls += 1
            if self.snapshot_calls == 1:
                return QueueSnapshot(expired_active=[first_target])
            return QueueSnapshot(active=[second_target])

    q = _RecheckQueue()
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **kw: {
            "running": True,
            "worker": {"running": True, "pid": 123, "heartbeat_age_sec": 1},
            "phase": "runner",
            "phase_age_sec": 30,
            "heartbeat_severity": "OK",
            "phase_severity": "OK",
            "expired_active_count": 0,
        },
    )

    assert _run_cli(["break-lease", "--project", "demo-proj", "--kind", "chroma", "--yes"]) == 1

    err = capsys.readouterr().err
    assert "健康运行" in err
    assert q.snapshot_calls >= 2
    assert q.broken == []


def test_break_lease_ignores_other_key_expired_active_when_target_is_healthy(monkeypatch, capsys):
    now = time.time()
    target = Job(
        "demo-proj",
        "chroma",
        now - 400,
        token="lease-1",
        lease_expires_at=now + 300,
    )
    other_expired = Job(
        "other-proj",
        "chroma",
        now - 500,
        token="lease-2",
        lease_expires_at=now - 10,
    )
    q = _FakeQueue(active=[target], expired_active=[other_expired])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **kw: {
            "running": True,
            "worker": {"running": True, "pid": 123, "heartbeat_age_sec": 1},
            "phase": "runner",
            "phase_age_sec": 30,
            "heartbeat_severity": "OK",
            "phase_severity": "OK",
            "expired_active_count": 1,
        },
    )

    assert _run_cli(["break-lease", "--project", "demo-proj", "--kind", "chroma", "--yes"]) == 1

    err = capsys.readouterr().err
    assert "健康运行" in err
    assert q.broken == []


def test_break_lease_yes_allows_stopped_worker_over_threshold(monkeypatch):
    q = _FakeQueue(active=[Job("demo-proj", "chroma", time.time() - 400, token="lease-1")])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **kw: {
            "running": False,
            "worker": {"running": False, "pid": None},
            "phase": "stopped",
            "phase_age_sec": 10,
            "expired_active_count": 1,
        },
    )

    assert (
        _run_cli(
            [
                "break-lease",
                "--project",
                "demo-proj",
                "--kind",
                "chroma",
                "--older-than-sec",
                "300",
                "--yes",
            ]
        )
        == 0
    )
    assert q.broken == ["demo-proj__chroma"]


def test_break_lease_targets_expired_active_snapshot_entry(monkeypatch):
    expired = Job(
        "demo-proj", "chroma", time.time() - 400, token="lease-1", lease_expires_at=time.time() - 10
    )
    q = _FakeQueue(expired_active=[expired])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **kw: {
            "running": True,
            "worker": {"running": True, "pid": 123, "heartbeat_age_sec": 1},
            "phase": "runner",
            "phase_age_sec": 30,
            "expired_active_count": 1,
        },
    )

    assert _run_cli(["break-lease", "--project", "demo-proj", "--kind", "chroma", "--yes"]) == 0
    assert q.broken == ["demo-proj__chroma"]


def test_break_lease_yes_rejects_when_active_age_below_threshold(monkeypatch, capsys):
    q = _FakeQueue(active=[Job("demo-proj", "chroma", time.time() - 120, token="lease-1")])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda **kw: {
            "running": False,
            "worker": {"running": False, "pid": None},
            "phase": "stopped",
            "phase_age_sec": 10,
            "expired_active_count": 1,
        },
    )

    assert (
        _run_cli(
            [
                "break-lease",
                "--project",
                "demo-proj",
                "--kind",
                "chroma",
                "--older-than-sec",
                "300",
                "--yes",
            ]
        )
        == 1
    )

    err = capsys.readouterr().err
    assert "older-than-sec" in err
    assert q.broken == []


def test_break_lease_parser_accepts_project_option():
    parser = build_parser()
    ns = parser.parse_args(
        ["reindex-queue", "break-lease", "--project", "demo-proj", "--kind", "chroma"]
    )

    assert ns.project == "demo-proj"


def test_break_lease_rejects_force_file(monkeypatch, capsys):
    q = _FakeQueue(active=[Job("demo-proj", "chroma", time.time() - 200, token="lease-1")])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert _run_cli(["break-lease", "demo-proj", "--kind", "chroma", "--force-file"]) == 1

    err = capsys.readouterr().err
    assert "break-lease 不接受这些参数" in err
    assert "--force-file" in err
