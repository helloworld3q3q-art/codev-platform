"""File/PG 队列状态转换共用的行为断言。"""

from __future__ import annotations

import dataclasses
import time

from codev_platform.reindex.queue_ports import ClaimedJob, JobMeta

_OID_A = "a" * 40
_TIMEOUT = 0.5


def _enqueue(queue, project_id: str) -> None:
    queue.enqueue(
        project_id,
        "chroma",
        JobMeta(source="contract", pull_policy="never", target_commit=_OID_A),
    )


def _enqueue_dependency(queue, project_id: str, target_commit: str) -> None:
    queue.enqueue(
        project_id,
        "codegraph",
        JobMeta(
            source="contract",
            pull_policy="never",
            target_commit=target_commit,
        ),
    )


def _claim_one(queue) -> ClaimedJob:
    claims = queue.claim(
        owner_token="contract-worker",
        projects=None,
        limit=1,
        timeout_sec=_TIMEOUT,
    )
    assert len(claims) == 1
    return claims[0]


def assert_retry_moves_claim_to_tail(queue, *, prefix: str) -> None:
    first_project = f"{prefix}-first"
    second_project = f"{prefix}-second"
    _enqueue(queue, first_project)
    time.sleep(0.01)
    _enqueue(queue, second_project)
    claim = _claim_one(queue)
    assert claim.job.project_id == first_project

    assert queue.retry(
        claim,
        reason="依赖尚未就绪",
        timeout_sec=_TIMEOUT,
    ) is True

    pending = [job for job in queue.peek() if job.project_id.startswith(prefix)]
    assert [job.project_id for job in pending] == [second_project, first_project]
    assert pending[1].enqueued_at > pending[0].enqueued_at > claim.job.enqueued_at


def assert_reject_is_token_fenced(queue, *, prefix: str) -> None:
    project_id = f"{prefix}-reject"
    _enqueue(queue, project_id)
    claim = _claim_one(queue)
    stale = dataclasses.replace(claim, claim_token="stale-claim")
    wrong_owner = dataclasses.replace(claim, owner_token="other-worker")

    assert queue.reject(
        stale,
        reason="非法目标版本",
        timeout_sec=_TIMEOUT,
    ) is False
    assert queue.reject(
        wrong_owner,
        reason="非法目标版本",
        timeout_sec=_TIMEOUT,
    ) is False
    assert queue.recover_owned(
        owner_token=claim.owner_token,
        timeout_sec=_TIMEOUT,
    ) == [claim]

    assert queue.reject(
        claim,
        reason="非法目标版本",
        timeout_sec=_TIMEOUT,
    ) is True
    snapshot = queue.snapshot()
    assert all(job.key != claim.job.key for job in snapshot.active)
    assert all(job.key != claim.job.key for job in snapshot.pending)
    assert any(job.key == claim.job.key for job in snapshot.results)


def assert_dependency_state_is_exact_and_read_only(queue, *, prefix: str) -> None:
    project_id = f"{prefix}-dependency"
    target_a = "a" * 40
    target_b = "b" * 40
    unrelated = "c" * 40

    assert queue.dependency_state(
        project_id,
        "codegraph",
        target_a,
        timeout_sec=_TIMEOUT,
    ) == "absent"
    _enqueue_dependency(queue, project_id, target_a)
    before = queue.snapshot()
    assert queue.dependency_state(
        project_id,
        "codegraph",
        target_a,
        timeout_sec=_TIMEOUT,
    ) == "pending"
    assert queue.snapshot() == before

    claim = queue.claim(
        owner_token="dependency-worker",
        projects={project_id},
        limit=1,
        timeout_sec=_TIMEOUT,
    )[0]
    _enqueue_dependency(queue, project_id, target_b)
    before = queue.snapshot()
    assert queue.dependency_state(
        project_id,
        "codegraph",
        target_a,
        timeout_sec=_TIMEOUT,
    ) == "active"
    assert queue.dependency_state(
        project_id,
        "codegraph",
        target_b,
        timeout_sec=_TIMEOUT,
    ) == "pending"
    assert queue.dependency_state(
        project_id,
        "codegraph",
        unrelated,
        timeout_sec=_TIMEOUT,
    ) == "replacement"
    assert queue.snapshot() == before
    assert claim.job.meta.target_commit == target_a


__all__ = [
    "assert_dependency_state_is_exact_and_read_only",
    "assert_reject_is_token_fenced",
    "assert_retry_moves_claim_to_tail",
]
