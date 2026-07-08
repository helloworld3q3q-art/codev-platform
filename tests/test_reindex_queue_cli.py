"""reindex-queue CLI 管理动作测试。"""
from __future__ import annotations

import argparse
import os
import time

from codev_platform.ops import reindex_queue as rq
from codev_platform.reindex.queue import FileSpoolQueue, Job


class _FakeQueue:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.discarded: list[str] = []

    def peek(self):
        return list(self.jobs)

    def discard(self, job):
        self.discarded.append(job.key)
        self.jobs = [j for j in self.jobs if j is not job]
        return True


def _args(**kw):
    base = {
        "action": "prune-stale",
        "project": None,
        "kind": "all",
        "older_than_sec": 300,
        "yes": False,
        "force_file": False,
        "idle_exit_sec": None,
        "heartbeat_sec": None,
        "owner_token": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def test_prune_stale_dry_run_does_not_discard(monkeypatch, capsys):
    old = Job("demo-proj", "chroma", time.time() - 1000)
    q = _FakeQueue([old])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args()) == 0

    out = capsys.readouterr().out
    assert "DRY-RUN STALE 1" in out
    assert "未删除任何任务" in out
    assert q.discarded == []


def test_prune_stale_yes_discards_only_matching_filter(monkeypatch, capsys):
    old_target = Job("demo-proj", "chroma", time.time() - 1000)
    old_other_kind = Job("demo-proj", "codegraph", time.time() - 1000)
    fresh_target = Job("demo-proj", "chroma", time.time())
    q = _FakeQueue([old_target, old_other_kind, fresh_target])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(project="demo-proj", kind="chroma", yes=True)) == 0

    out = capsys.readouterr().out
    assert "清理完成: removed=1" in out
    assert q.discarded == ["demo-proj__chroma"]
    assert [j.key for j in q.jobs] == ["demo-proj__codegraph", "demo-proj__chroma"]


def test_prune_stale_kind_all_includes_unknown_history_kind(monkeypatch):
    old = Job("demo-proj", "oldkind", time.time() - 1000)
    q = _FakeQueue([old])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(yes=True)) == 0

    assert q.discarded == ["demo-proj__oldkind"]


def test_prune_stale_yes_uses_fail_closed_queue_open(monkeypatch):
    seen = {}
    q = _FakeQueue([Job("demo-proj", "chroma", time.time() - 1000)])

    def _open_default_queue(**kw):
        seen.update(kw)
        return q

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", _open_default_queue)
    assert rq.cmd_reindex_queue(_args(yes=True)) == 0
    assert seen["fail_soft"] is False


def test_prune_stale_yes_refuses_file_backend_without_force(monkeypatch, tmp_path, capsys):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "chroma")
    marker = tmp_path / "demo-proj__chroma"
    os.utime(marker, (time.time() - 1000, time.time() - 1000))
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(yes=True)) == 1

    err = capsys.readouterr().err
    assert "--force-file" in err
    assert marker.exists()


def test_prune_stale_yes_force_file_discards(monkeypatch, tmp_path):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "chroma")
    marker = tmp_path / "demo-proj__chroma"
    os.utime(marker, (time.time() - 1000, time.time() - 1000))
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

    stale = rq._stale_jobs(jobs, now=now, older_than_sec=300,
                           project_id="p1", kinds={"chroma"})

    assert stale == [jobs[0]]


def test_drain_once_calls_worker(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    q = _FakeQueue([])

    class _Worker:
        def __init__(self, queue, cfg, on_heartbeat=None, on_job_event=None):
            assert queue is q
            assert callable(on_heartbeat)
            assert callable(on_job_event)

        def drain_once(self):
            return 3

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr("codev_platform.reindex.ReindexWorker", _Worker)

    assert rq.cmd_reindex_queue(_args(action="drain-once")) == 0

    assert "drain-once processed=3" in capsys.readouterr().out


def test_drain_once_refuses_when_worker_lock_held(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    q = _FakeQueue([])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    from codev_platform.reindex import supervisor
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True
        assert rq.cmd_reindex_queue(_args(action="drain-once")) == 1

    assert "already running" in capsys.readouterr().err


def test_status_reports_worker_and_pending(monkeypatch, capsys):
    old = Job("demo-proj", "chroma", time.time() - 1000)
    q = _FakeQueue([old])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr("codev_platform.reindex.supervisor.worker_status",
                        lambda: {"running": False, "pid": None})

    assert rq.cmd_reindex_queue(_args(action="status")) == 0

    out = capsys.readouterr().out
    assert "worker running: no" in out
    assert "queue backend: _FakeQueue" in out
    assert "worker not running" in out


def test_status_does_not_mark_stale_while_worker_running(monkeypatch, capsys):
    old = Job("demo-proj", "chroma", time.time() - 1000)
    q = _FakeQueue([old])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr("codev_platform.reindex.supervisor.worker_status",
                        lambda: {"running": True, "pid": 123, "heartbeat_age_sec": 1, "mode": "short"})

    assert rq.cmd_reindex_queue(_args(action="status")) == 0

    out = capsys.readouterr().out
    assert "worker running: yes" in out
    assert "STALE" not in out
    assert "worker not running" not in out


def test_status_does_not_mark_real_file_spool_marker_stale_while_worker_running(monkeypatch, tmp_path, capsys):
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "chroma")
    marker = tmp_path / "demo-proj__chroma"
    os.utime(marker, (time.time() - 1000, time.time() - 1000))
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr("codev_platform.reindex.supervisor.worker_status",
                        lambda: {"running": True, "pid": 123, "heartbeat_age_sec": 1, "mode": "short"})

    assert rq.cmd_reindex_queue(_args(action="status")) == 0

    out = capsys.readouterr().out
    assert "queue backend: FileSpoolQueue" in out
    assert "STALE" not in out


def test_worker_rejects_non_positive_timing(monkeypatch, capsys):
    q = _FakeQueue([])
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(action="worker", idle_exit_sec=0)) == 1
    assert "--idle-exit-sec" in capsys.readouterr().err

    assert rq.cmd_reindex_queue(_args(action="worker", heartbeat_sec=0)) == 1
    assert "--heartbeat-sec" in capsys.readouterr().err


def test_worker_short_lived_records_idle_exit_and_releases_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    q = _FakeQueue([])

    class _Worker:
        def __init__(self, queue, cfg, on_heartbeat=None, on_job_event=None):
            assert queue is q

        async def run_until_idle(self, idle_exit_sec, heartbeat_sec):
            assert idle_exit_sec == 0.1
            assert heartbeat_sec == 0.1

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr("codev_platform.reindex.ReindexWorker", _Worker)

    assert rq.cmd_reindex_queue(
        _args(action="worker", idle_exit_sec=0.1, heartbeat_sec=0.1)) == 0

    from codev_platform.reindex import supervisor
    st = supervisor.worker_status()
    assert st["exit_reason"] == "idle"
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True
