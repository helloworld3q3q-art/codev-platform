"""reindex-queue CLI 的管理、入队、清理与状态测试。"""

from __future__ import annotations

import time

import pytest

from codev_platform.cli import build_parser
from codev_platform.reindex.queue import FileSpoolQueue, Job, JobMeta
from tests.reindex_queue_cli_support import (
    _FakeQueue,
    _args,
    _confirmed_queue_window,
    _pending_path,
    _run_cli,
    _set_pending_enqueued_at,
    rq,
)

pytestmark = pytest.mark.usefixtures(_confirmed_queue_window.__name__)


def test_prune_stale_dry_run_does_not_discard(monkeypatch, capsys):
    old = Job("demo-proj", "chroma", time.time() - 1000)
    q = _FakeQueue(pending=[old])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args()) == 0

    out = capsys.readouterr().out
    assert "DRY-RUN STALE 1" in out
    assert "未删除任何任务" in out
    assert q.discarded == []


def test_prune_stale确认写入只在受控维护窗口内打开队列(monkeypatch, capsys):
    """队列构造可能 mkdir 或建 PG schema，必须晚于维护证明。"""
    old = Job("demo-proj", "chroma", time.time() - 1000)
    q = _FakeQueue(pending=[old])
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
            else pytest.fail("确认清理必须严格打开队列")
        ),
    )

    assert rq.cmd_reindex_queue(_args(yes=True)) == 0
    assert events == ["window", "queue"]
    assert q.discarded == [old.key]
    assert "清理完成" in capsys.readouterr().out


def test_prune_stale确认写入被维护窗口拒绝时不得打开队列(monkeypatch, capsys):
    from codev_platform.ops.reindex_admin import ReindexAdminPreconditionError

    monkeypatch.setattr(
        rq,
        "run_confirmed_maintenance_action",
        lambda **_kwargs: (_ for _ in ()).throw(ReindexAdminPreconditionError("拒绝")),
        raising=False,
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: pytest.fail("维护窗口拒绝后不得打开队列"),
    )

    assert rq.cmd_reindex_queue(_args(yes=True)) == 1
    assert "维护窗口" in capsys.readouterr().err


def test_enqueue_cli_persists_manual_ff_only_meta(tmp_path, monkeypatch):
    q = FileSpoolQueue(tmp_path)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.target_commit.resolve_project_head",
        lambda _project: "a" * 40,
    )

    assert _run_cli(["enqueue", "demo-proj", "--kind", "chroma", "--pull-policy", "ff-only"]) == 0

    assert q.peek()[0].meta == JobMeta(
        source="manual",
        pull_policy="ff_only",
        target_commit="a" * 40,
    )


def test_enqueue_cli_defaults_to_manual_never_pull(tmp_path, monkeypatch):
    q = FileSpoolQueue(tmp_path)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.target_commit.resolve_project_head",
        lambda _project: "b" * 40,
    )

    assert _run_cli(["enqueue", "demo-proj", "--kind", "chroma"]) == 0

    assert q.peek()[0].meta == JobMeta(
        source="manual",
        pull_policy="never",
        target_commit="b" * 40,
    )


def test_init_owner动作只委派给受控运维壳而不自行打开队列(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        "codev_platform.ops.reindex_admin.cmd_reindex_admin",
        lambda args: seen.append(args.action) or 7,
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: pytest.fail("init-owner 不得由通用队列命令预先打开队列"),
    )

    assert rq.cmd_reindex_queue(_args(action="init-owner", yes=True)) == 7
    assert seen == ["init-owner"]


def test_init_owner_parser接受显式确认参数() -> None:
    parser = build_parser()

    args = parser.parse_args(["reindex-queue", "init-owner", "--yes"])

    assert args.action == "init-owner"
    assert args.yes is True


def test_migrate_legacy动作只委派给受控运维壳而不自行打开队列(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        "codev_platform.ops.reindex_admin.cmd_reindex_admin",
        lambda args: seen.append(args.action) or 8,
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: pytest.fail("migrate-legacy 不得由通用队列命令预先打开队列"),
    )

    assert (
        rq.cmd_reindex_queue(
            _args(action="migrate-legacy", yes=True, pending_map="pending-map.json")
        )
        == 8
    )
    assert seen == ["migrate-legacy"]


def test_migrate_legacy_parser接受映射与显式确认参数() -> None:
    parser = build_parser()

    args = parser.parse_args(
        ["reindex-queue", "migrate-legacy", "--pending-map", "pending-map.json", "--yes"]
    )

    assert args.action == "migrate-legacy"
    assert args.pending_map == "pending-map.json"
    assert args.yes is True


def test_migrate_legacy拒绝隐藏worker身份参数(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "codev_platform.ops.reindex_admin.cmd_reindex_admin",
        lambda _args: pytest.fail("非法参数不得进入运维壳"),
    )

    assert (
        rq.cmd_reindex_queue(_args(action="migrate-legacy", yes=True, owner_token="worker-token"))
        == 1
    )

    assert "--owner-token" in capsys.readouterr().err


def test_enqueue_cli_refuses_when_target_commit_cannot_be_resolved(monkeypatch, capsys):
    q = _FakeQueue()
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.target_commit.resolve_project_head",
        lambda _project: (_ for _ in ()).throw(RuntimeError("git unavailable")),
    )

    assert _run_cli(["enqueue", "demo-proj", "--kind", "chroma"]) == 1
    assert "目标提交" in capsys.readouterr().err


def test_status_rejects_irrelevant_pull_policy(monkeypatch, capsys):
    q = _FakeQueue()
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert _run_cli(["status", "--pull-policy", "ff-only"]) == 1

    err = capsys.readouterr().err
    assert "status 不接受这些参数" in err
    assert "--pull-policy" in err


def test_status_reports_owner_boundary_without_traceback(monkeypatch, capsys):
    from codev_platform.reindex.producer_route import LocalHookRelayRequired

    def refuse_cross_owner_queue(**_kwargs):
        raise LocalHookRelayRequired("queue owner requires HTTP status")

    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        refuse_cross_owner_queue,
    )

    assert rq.cmd_reindex_queue(_args(action="status")) == 1

    err = capsys.readouterr().err
    assert err.strip() == "FATAL: queue owner requires HTTP status"
    assert "Traceback" not in err


def test_prune_stale_yes_discards_only_matching_filter(monkeypatch, capsys):
    old_target = Job("demo-proj", "chroma", time.time() - 1000)
    old_other_kind = Job("demo-proj", "codegraph", time.time() - 1000)
    fresh_target = Job("demo-proj", "chroma", time.time())
    q = _FakeQueue(pending=[old_target, old_other_kind, fresh_target])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(project="demo-proj", kind="chroma", yes=True)) == 0

    out = capsys.readouterr().out
    assert "清理完成: removed=1" in out
    assert q.discarded == ["demo-proj__chroma"]
    assert [j.key for j in q.pending_jobs] == ["demo-proj__codegraph", "demo-proj__chroma"]


def test_prune_stale_kind_all_includes_unknown_history_kind(monkeypatch):
    old = Job("demo-proj", "oldkind", time.time() - 1000)
    q = _FakeQueue(pending=[old])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(yes=True)) == 0

    assert q.discarded == ["demo-proj__oldkind"]


def test_prune_stale_yes_uses_fail_closed_queue_open(monkeypatch):
    seen = {}
    q = _FakeQueue(pending=[Job("demo-proj", "chroma", time.time() - 1000)])

    def _open_default_queue(**kw):
        seen.update(kw)
        return q

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", _open_default_queue)
    assert rq.cmd_reindex_queue(_args(yes=True)) == 0
    assert seen["fail_soft"] is False


def test_prune_stale_yes_refuses_file_backend_without_force(monkeypatch, tmp_path, capsys):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "chroma")
    marker = _pending_path(tmp_path, "demo-proj__chroma")
    _set_pending_enqueued_at(tmp_path, "demo-proj__chroma", time.time() - 1000)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(yes=True)) == 1

    err = capsys.readouterr().err
    assert "--force-file" in err
    assert marker.exists()


def test_prune_stale_yes_force_file_discards(monkeypatch, tmp_path):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "chroma")
    _set_pending_enqueued_at(tmp_path, "demo-proj__chroma", time.time() - 1000)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(yes=True, force_file=True)) == 0

    assert q.peek() == []


def test_stale_jobs_filters_project_kind_and_age():
    now = time.time()
    jobs = [
        Job("p1", "chroma", now - 1000),
        Job("p1", "codegraph", now - 1000),
        Job("p2", "chroma", now - 1000),
        Job("p1", "chroma", now - 1),
    ]

    stale = rq._stale_jobs(jobs, now=now, older_than_sec=300, project_id="p1", kinds={"chroma"})

    assert stale == [jobs[0]]


def test_status_reports_worker_and_pending(monkeypatch, capsys):
    old = Job("demo-proj", "chroma", time.time() - 1000)
    q = _FakeQueue(pending=[old])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status", lambda: {"running": False, "pid": None}
    )

    assert rq.cmd_reindex_queue(_args(action="status")) == 0

    out = capsys.readouterr().out
    assert "worker running: no" in out
    assert "queue backend: _FakeQueue" in out
    assert "worker not running" in out


def test_status_does_not_mark_stale_while_worker_running(monkeypatch, capsys):
    old = Job("demo-proj", "chroma", time.time() - 1000)
    q = _FakeQueue(pending=[old])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda: {
            "running": True,
            "pid": 123,
            "heartbeat_age_sec": 1,
            "mode": "short",
            "execution_mode": "isolated",
        },
    )

    assert rq.cmd_reindex_queue(_args(action="status")) == 0

    out = capsys.readouterr().out
    assert "worker running: yes" in out
    assert "execution_mode=isolated" in out
    assert "STALE" not in out
    assert "worker not running" not in out


def test_status_does_not_mark_real_file_spool_marker_stale_while_worker_running(
    monkeypatch, tmp_path, capsys
):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "chroma")
    _set_pending_enqueued_at(tmp_path, "demo-proj__chroma", time.time() - 1000)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda: {"running": True, "pid": 123, "heartbeat_age_sec": 1, "mode": "short"},
    )

    assert rq.cmd_reindex_queue(_args(action="status")) == 0

    out = capsys.readouterr().out
    assert "queue backend: FileSpoolQueue" in out
    assert "STALE" not in out


def test_prune_stale_refuses_pending_when_same_key_active(monkeypatch, capsys):
    old = Job("demo-proj", "chroma", time.time() - 1000)
    active = Job("demo-proj", "chroma", time.time() - 1200, token="lease-1")
    q = _FakeQueue(pending=[old], active=[active])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(yes=True)) == 1

    err = capsys.readouterr().err
    assert "break-lease" in err
    assert q.discarded == []


def test_prune_stale_refuses_pending_when_same_key_expired_active(monkeypatch, capsys):
    old = Job("demo-proj", "chroma", time.time() - 1000)
    expired = Job(
        "demo-proj",
        "chroma",
        time.time() - 1200,
        token="lease-1",
        lease_expires_at=time.time() - 10,
    )
    q = _FakeQueue(pending=[old], expired_active=[expired])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(yes=True)) == 1

    err = capsys.readouterr().err
    assert "break-lease" in err
    assert q.discarded == []


def test_status_lists_expired_active_and_not_empty(monkeypatch, capsys):
    now = time.time()
    expired = Job(
        "demo-proj",
        "chroma",
        now - 400,
        token="lease-1",
        lease_expires_at=now - 10,
    )
    q = _FakeQueue(expired_active=[expired])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.worker_status",
        lambda: {"running": False, "pid": None, "phase": "stopped", "phase_at": 0},
    )

    assert rq.cmd_reindex_queue(_args(action="status")) == 0

    out = capsys.readouterr().out
    assert "队列空" not in out
    assert "expired active 1:" in out
    assert "demo-proj__chroma" in out
