"""reindex-queue CLI 测试共享替身与参数构造器。"""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager

import pytest

from codev_platform.cli import build_parser
from codev_platform.ops import reindex_queue as rq
from codev_platform.reindex.queue import Job, QueueSnapshot


def _pending_path(root, key: str):
    return root / "pending" / f"{key}.json"


def _set_pending_enqueued_at(root, key: str, value: float):
    path = _pending_path(root, key)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["enqueued_at"] = value
    path.write_text(json.dumps(data), encoding="utf-8")


class _FakeQueue:
    def __init__(self, pending=(), active=(), results=(), expired_active=()):
        self.pending_jobs = list(pending)
        self.active_jobs = list(active)
        self.result_jobs = list(results)
        self.expired_active_jobs = list(expired_active)
        self.discarded: list[str] = []
        self.broken: list[str] = []

    def peek(self):
        return list(self.pending_jobs)

    def snapshot(self):
        return QueueSnapshot(
            pending=list(self.pending_jobs),
            active=list(self.active_jobs),
            results=list(self.result_jobs),
            expired_active=list(self.expired_active_jobs),
        )

    def discard(self, job):
        self.discarded.append(job.key)
        self.pending_jobs = [j for j in self.pending_jobs if j is not job]
        return True

    def break_lease(self, job):
        self.broken.append(job.key)
        self.active_jobs = [j for j in self.active_jobs if j.key != job.key]
        if not any(j.key == job.key for j in self.pending_jobs):
            self.pending_jobs.append(Job(job.project_id, job.kind, job.enqueued_at))
        return True


@contextmanager
def _maintenance_permit(permitted: bool):
    """以最小上下文替身验证 CLI 是否持有完整维护许可。"""
    yield permitted


@pytest.fixture
def _confirmed_queue_window(monkeypatch):
    """队列算法单测以已证明的维护窗口替身隔离 systemd 依赖。"""

    def _run(*, input_loader, action):
        return action(input_loader())

    monkeypatch.setattr(rq, "run_confirmed_maintenance_action", _run)


def _args(**kw):
    base = {
        "action": "prune-stale",
        "project": None,
        "kind": "all",
        "pull_policy": "never",
        "target_commit": None,
        "older_than_sec": 300,
        "yes": False,
        "force_file": False,
        "idle_exit_sec": None,
        "heartbeat_sec": None,
        "owner_token": None,
        "require_execution_mode": None,
        "pending_map": None,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _run_cli(argv: list[str]) -> int:
    parser = build_parser()
    ns = parser.parse_args(["reindex-queue", *argv])
    return ns.func(ns)
