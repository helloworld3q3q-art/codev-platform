"""File 队列 retry-to-tail 与 reject 转换契约。"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager

import pytest

import codev_platform.reindex.file_queue_transitions as transitions
from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex.queue import DependencyQueueViewPort
from codev_platform.reindex.queue_ports import (
    JobMeta,
    QueueOperationTimeout,
    WorkerQueuePort,
)
from tests.queue_transition_contract import (
    assert_dependency_state_is_exact_and_read_only,
    assert_reject_is_token_fenced,
    assert_retry_moves_claim_to_tail,
)


def test_worker_queue_port_exposes_explicit_reject() -> None:
    assert "reject" in WorkerQueuePort.__dict__


def test_dependency_view_port_is_exposed_by_file_queue(tmp_path) -> None:
    assert "dependency_state" in DependencyQueueViewPort.__dict__
    queue = FileSpoolQueue(tmp_path / "queue")
    assert_dependency_state_is_exact_and_read_only(queue, prefix="file")


def test_file_dependency_view_treats_quarantine_as_absent(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue(
        "file-quarantine",
        "codegraph",
        meta=JobMeta(target_commit="a" * 40),
    )
    claim = queue.claim(
        owner_token="worker-1",
        projects={"file-quarantine"},
        limit=1,
        timeout_sec=0.5,
    )[0]
    queue.quarantine(
        claim,
        attempt_id="attempt-1",
        fence="fence-1",
        process_identity="process-1",
        containment_kind="test",
        native_ref="native-1",
        reason="存活状态不明",
        timeout_sec=0.5,
    )

    assert queue.dependency_state(
        "file-quarantine",
        "codegraph",
        "a" * 40,
        timeout_sec=0.5,
    ) == "absent"


@pytest.mark.parametrize(
    "target",
    ["abc", "A" * 40, "0" * 40, "0" * 64],
)
def test_file_dependency_view_rejects_non_canonical_target(tmp_path, target) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")

    with pytest.raises(ValueError, match="target_commit"):
        queue.dependency_state(
            "demo",
            "codegraph",
            target,
            timeout_sec=0.5,
        )


def test_file_dependency_view_accepts_full_sha256_target(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")

    assert queue.dependency_state(
        "demo",
        "codegraph",
        "c" * 64,
        timeout_sec=0.5,
    ) == "absent"


def test_file_dependency_view_lock_timeout_is_not_silently_absent(
    tmp_path,
    monkeypatch,
) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")

    @contextmanager
    def unavailable_lock(*_args, **_kwargs):
        yield False

    monkeypatch.setattr(queue._store, "key_lock", unavailable_lock)

    with pytest.raises(QueueOperationTimeout, match="File.*锁|锁.*预算"):
        queue.dependency_state(
            "demo",
            "codegraph",
            "a" * 40,
            timeout_sec=0.5,
        )


def test_file_retry_atomically_moves_item_to_tail_and_updates_reentry_time(
    tmp_path,
) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    assert_retry_moves_claim_to_tail(queue, prefix="file-tail")


def test_file_reject_retires_only_matching_claim_with_failure_audit(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    assert_reject_is_token_fenced(queue, prefix="file")

    result_path = queue.location / "results" / "file-reject__chroma.json"
    audit = json.loads(result_path.read_text(encoding="utf-8"))
    assert audit["result_status"] == "failed"
    assert audit["failure_reason"] == "非法目标版本"


def test_file_reject_preserves_newer_pending_replacement(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("file-dirty", "chroma")
    claim = queue.claim(
        owner_token="worker-1",
        projects=None,
        limit=1,
        timeout_sec=0.5,
    )[0]
    queue.enqueue("file-dirty", "chroma")

    assert queue.reject(
        claim,
        reason="旧 active 目标非法",
        timeout_sec=0.5,
    ) is True

    snapshot = queue.snapshot()
    assert [job.key for job in snapshot.pending] == [claim.job.key]
    assert snapshot.active == []
    assert [job.key for job in snapshot.results] == [claim.job.key]


def test_file_retry_refuses_non_finite_tail_without_retiring_active(
    tmp_path,
    monkeypatch,
) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    queue.enqueue("file-overflow", "chroma")
    claim = queue.claim(
        owner_token="worker-1",
        projects=None,
        limit=1,
        timeout_sec=0.5,
    )[0]
    monkeypatch.setattr(transitions.time, "time", lambda: sys.float_info.max)

    with pytest.raises(ValueError, match="队尾时间"):
        queue.retry(claim, reason="等待依赖", timeout_sec=0.5)

    recovered = queue.recover_owned(owner_token="worker-1", timeout_sec=0.5)
    assert recovered == [claim]
    assert queue.peek() == []
