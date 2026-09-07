"""reindex dispatch trigger 测试 (deep-audit-2026-06-03-review 净新增②修复)。

验证 web "重建索引" 不再是空壳: make_reindex_dispatch_trigger 把 index_rebuild job
真正派进 reindex 队列 (用假队列捕获 enqueue, 不碰真实 spool):
- 单 kind → enqueue 一条 (project, kind)
- 'all' → 展开成 runners.kinds() 全集
- 非 index_rebuild 的 job_type → 不 enqueue (不拦截其它 job 类型)
- 经 JobService.submit 串起来; 派发失败 → 释放锁 (不悬挂堵后续提交)
"""
from __future__ import annotations

import pytest

from codev_platform.reindex.queue import JobMeta
from codev_platform.reindex.runners import kinds as runner_kinds
from codev_platform.web.domain.job import Job, create_job
from codev_platform.web.domain.locks import ProjectLockRegistry
from codev_platform.web.repositories.job_read_repo import InMemoryJobReadRepo
from codev_platform.web.repositories.job_write_repo import (
    InMemoryJobWriteRepo,
    new_in_memory_store,
)
from codev_platform.web.services.index_service import (
    job_type_for,
    make_reindex_dispatch_trigger,
)
from codev_platform.web.services.job_service import JobService


_TARGET_COMMIT = "c" * 40


class _FakeQueue:
    """捕获 enqueue 调用的假队列 (不落盘)。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, JobMeta | None]] = []

    def enqueue(self, project_id: str, kind: str, meta: JobMeta | None = None) -> None:
        self.calls.append((project_id, kind, meta))


def _trigger_with_fake() -> tuple[object, _FakeQueue]:
    q = _FakeQueue()
    trigger = make_reindex_dispatch_trigger(
        queue_factory=lambda: q,
        target_commit_resolver=lambda _project_id: _TARGET_COMMIT,
    )
    return trigger, q


def test_single_kind_enqueues_one():
    trigger, q = _trigger_with_fake()
    trigger(create_job("demo-proj", job_type_for("chroma")))
    assert q.calls == [(
        "demo-proj",
        "chroma",
        JobMeta(source="admin", pull_policy="never", target_commit=_TARGET_COMMIT),
    )]


def test_all_expands_to_runner_kinds():
    trigger, q = _trigger_with_fake()
    trigger(create_job("demo-proj", job_type_for("all")))
    enqueued = {kind for _pid, kind, _meta in q.calls}
    assert enqueued == set(runner_kinds())
    assert all(pid == "demo-proj" for pid, _kind, _meta in q.calls)
    assert all(
        meta == JobMeta(source="admin", pull_policy="never", target_commit=_TARGET_COMMIT)
        for _pid, _kind, meta in q.calls
    )


def test_non_index_rebuild_job_ignored():
    trigger, q = _trigger_with_fake()
    trigger(Job(job_id="x", project_id="demo-proj", job_type="some_other_job"))
    assert q.calls == []


def test_submit_dispatches_via_jobservice():
    q = _FakeQueue()
    store = new_in_memory_store()
    svc = JobService(
        read_repo=InMemoryJobReadRepo(store),
        write_repo=InMemoryJobWriteRepo(store),
        locks=ProjectLockRegistry(),
        trigger=make_reindex_dispatch_trigger(
            queue_factory=lambda: q,
            target_commit_resolver=lambda _project_id: _TARGET_COMMIT,
        ),
    )
    job = svc.submit("demo-proj", job_type_for("codegraph"))
    assert job.job_id
    assert q.calls == [(
        "demo-proj",
        "codegraph",
        JobMeta(source="admin", pull_policy="never", target_commit=_TARGET_COMMIT),
    )]


def test_dispatch_failure_releases_lock():
    def _boom() -> None:
        raise RuntimeError("spool write failed")

    locks = ProjectLockRegistry()
    store = new_in_memory_store()
    svc = JobService(
        read_repo=InMemoryJobReadRepo(store),
        write_repo=InMemoryJobWriteRepo(store),
        locks=locks,
        trigger=make_reindex_dispatch_trigger(
            queue_factory=_boom,
            target_commit_resolver=lambda _project_id: _TARGET_COMMIT,
        ),
    )
    with pytest.raises(RuntimeError):
        svc.submit("demo-proj", job_type_for("chroma"))
    # 派发失败后锁已释放 → 同 project 同 kind 可再次 try_acquire
    assert locks.try_acquire("demo-proj", job_type_for("chroma")) is True
