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
