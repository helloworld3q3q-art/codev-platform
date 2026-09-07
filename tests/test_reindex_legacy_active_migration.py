"""旧 active 受控收口的队列契约测试。"""
from __future__ import annotations

import time

import pytest

from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex.queue_ports import ClaimedJob, Job, JobMeta, QueueSnapshot


class _Queue:
    def __init__(self, claims_by_owner: dict[str, list[ClaimedJob]]) -> None:
        self.claims_by_owner = claims_by_owner
        self.recovered_owners: list[str] = []
        self.rejected: list[ClaimedJob] = []
        self.retried: list[ClaimedJob] = []
        self.unowned_rejected: list[Job] = []
        self.settled: list[tuple[str, Job]] = []

    def recover_owned(self, *, owner_token: str, timeout_sec: float) -> list[ClaimedJob]:
        assert timeout_sec == 0.3
        self.recovered_owners.append(owner_token)
        return list(self.claims_by_owner.get(owner_token, ()))

    def reject(self, claim: ClaimedJob, *, reason: str, timeout_sec: float) -> bool:
        assert reason == "受控淘汰 legacy active"
        assert timeout_sec == 0.3
        self.rejected.append(claim)
        return True

    def retry(self, claim: ClaimedJob, *, reason: str, timeout_sec: float) -> bool:
        assert reason == "受控恢复 legacy active"
        assert timeout_sec == 0.3
        self.retried.append(claim)
        return True

    def settle_expired_legacy_active(
        self,
        expected: Job,
        *,
        action: str,
        reason: str,
        timeout_sec: float,
    ) -> bool:
        assert action in {"reject", "retry"}
        assert timeout_sec == 0.3
        assert reason.startswith("受控")
        self.settled.append((action, expected))
        return True

    def reject_unowned_legacy_active(
        self,
        expected: Job,
        *,
        reason: str,
        timeout_sec: float,
    ) -> bool:
        assert reason == "受控淘汰无 owner legacy active"
        assert timeout_sec == 0.3
        self.unowned_rejected.append(expected)
        return True


def _job(
    kind: str,
    *,
    target_commit: str | None,
    owner_token: str | None,
    token: str,
) -> Job:
    return Job(
        "demo",
        kind,
        1.0,
        token=token,
        meta=JobMeta(target_commit=target_commit),
        lease_expires_at=2.0,
        owner_token=owner_token,
    )


def _claim(job: Job) -> ClaimedJob:
    assert job.owner_token is not None and job.token is not None
    return ClaimedJob(job, job.token, job.owner_token, 2.0)


def test_已知旧owner仅在非法目标时按精确恢复后围栏拒绝() -> None:
    from codev_platform.reindex.queue_migration import (
        LegacyActiveAction,
        dispose_expired_legacy_active,
    )

    invalid = _job("chroma", target_commit="HEAD", owner_token="old-owner", token="claim-a")
    queue = _Queue({"old-owner": [_claim(invalid)]})

    resolutions = dispose_expired_legacy_active(
        queue,
        QueueSnapshot(expired_active=[invalid]),
        bootstrap=True,
        timeout_sec=0.3,
    )

    assert queue.recovered_owners == []
    assert queue.settled == [("reject", invalid)]
    assert queue.rejected == []
    assert queue.retried == []
    assert [(item.key, item.action) for item in resolutions] == [
        (invalid.key, LegacyActiveAction.REJECTED),
    ]


def test_合法且有owner的现代过期active不属于迁移范围也不触发恢复() -> None:
    from codev_platform.reindex.queue_migration import dispose_expired_legacy_active

    modern = _job(
        "chroma",
        target_commit="a" * 40,
        owner_token="stable-owner",
        token="claim-modern",
    )
    queue = _Queue({"stable-owner": [_claim(modern)]})

    resolutions = dispose_expired_legacy_active(
        queue,
        QueueSnapshot(expired_active=[modern]),
        stable_owner_token="stable-owner",
        timeout_sec=0.3,
    )

    assert resolutions == ()
    assert queue.recovered_owners == []
    assert queue.rejected == []
    assert queue.retried == []


def test_稳定owner识别foreign_owner并精确恢复合法过期任务为pending() -> None:
    from codev_platform.reindex.queue_migration import (
        LegacyActiveAction,
        LegacyQueueReason,
        detect_legacy_queue_entries,
        dispose_expired_legacy_active,
    )

    foreign = _job(
        "chroma",
        target_commit="a" * 40,
        owner_token="old-owner",
        token="claim-foreign",
    )
    queue = _Queue({"old-owner": [_claim(foreign)]})
    snapshot = QueueSnapshot(expired_active=[foreign])

    entries = detect_legacy_queue_entries(snapshot, stable_owner_token="stable-owner")
    resolutions = dispose_expired_legacy_active(
        queue,
        snapshot,
        stable_owner_token="stable-owner",
        timeout_sec=0.3,
    )

    assert [(item.phase, item.reason) for item in entries] == [
        ("expired_active", LegacyQueueReason.FOREIGN_OWNER_TOKEN),
    ]
    assert queue.recovered_owners == []
    assert queue.settled == [("retry", foreign)]
    assert queue.retried == []
    assert [item.action for item in resolutions] == [LegacyActiveAction.RETRIED]


def test_无稳定owner时foreign_active仅报告阻断而不恢复() -> None:
    from codev_platform.reindex.queue_migration import (
        LegacyActiveAction,
        dispose_expired_legacy_active,
    )

    foreign = _job(
        "chroma",
        target_commit="a" * 40,
        owner_token="old-owner",
        token="claim-foreign",
    )
    queue = _Queue({"old-owner": [_claim(foreign)]})

    resolutions = dispose_expired_legacy_active(
        queue,
        QueueSnapshot(expired_active=[foreign]),
        stable_owner_token=None,
        timeout_sec=0.3,
    )

    assert queue.recovered_owners == []
    assert [item.action for item in resolutions] == [
        LegacyActiveAction.MANUAL_RECOVERY_REQUIRED,
    ]


def test_bootstrap模式可按旧owner精确收口过期合法active() -> None:
    from codev_platform.reindex.queue_migration import (
        LegacyActiveAction,
        dispose_expired_legacy_active,
    )

    foreign = _job(
        "chroma",
        target_commit="a" * 40,
        owner_token="old-owner",
        token="claim-foreign",
    )
    queue = _Queue({"old-owner": [_claim(foreign)]})

    resolutions = dispose_expired_legacy_active(
        queue,
        QueueSnapshot(expired_active=[foreign]),
        stable_owner_token=None,
        bootstrap=True,
        timeout_sec=0.3,
    )

    assert queue.recovered_owners == []
    assert queue.settled == [("retry", foreign)]
    assert queue.retried == []
    assert [item.action for item in resolutions] == [LegacyActiveAction.RETRIED]


def test_无owner仅拒绝非法任务并将合法任务列为人工恢复() -> None:
    from codev_platform.reindex.queue_migration import (
        LegacyActiveAction,
        dispose_expired_legacy_active,
    )

    invalid = _job("chroma", target_commit=None, owner_token=None, token="claim-a")
    canonical = _job("codegraph", target_commit="a" * 40, owner_token=None, token="claim-b")
    queue = _Queue({})

    resolutions = dispose_expired_legacy_active(
        queue,
        QueueSnapshot(expired_active=[invalid, canonical]),
        bootstrap=True,
        timeout_sec=0.3,
    )

    assert queue.recovered_owners == []
    assert queue.unowned_rejected == [invalid]
    assert [(item.key, item.action) for item in resolutions] == [
        (invalid.key, LegacyActiveAction.REJECTED),
        (canonical.key, LegacyActiveAction.MANUAL_RECOVERY_REQUIRED),
    ]


def test_无稳定owner且非bootstrap时非法目标也只报告而不写入() -> None:
    from codev_platform.reindex.queue_migration import (
        LegacyActiveAction,
        dispose_expired_legacy_active,
    )

    invalid = _job("chroma", target_commit="HEAD", owner_token="old-owner", token="claim-a")
    queue = _Queue({"old-owner": [_claim(invalid)]})

    resolutions = dispose_expired_legacy_active(
        queue,
        QueueSnapshot(expired_active=[invalid]),
        stable_owner_token=None,
        bootstrap=False,
        timeout_sec=0.3,
    )

    assert queue.settled == []
    assert queue.recovered_owners == []
    assert [item.action for item in resolutions] == [
        LegacyActiveAction.MANUAL_RECOVERY_REQUIRED,
    ]


def test_file无owner旧active仅在claim_token与owner为空同时匹配时拒绝(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    path = queue.location / "active" / "demo__chroma.json"
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "claim_token": "legacy-claim",
        "lease_expires_at": time.time() - 1.0,
    })
    expected = queue.snapshot().expired_active[0]

    assert queue.reject_unowned_legacy_active(
        expected,
        reason="受控淘汰无 owner legacy active",
        timeout_sec=0.3,
    ) is True
    assert queue.snapshot().expired_active == []
    assert [job.key for job in queue.snapshot().results] == [expected.key]


def test_file无owner拒绝不会跨owner围栏退休新active(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    path = queue.location / "active" / "demo__chroma.json"
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "claim_token": "legacy-claim",
        "lease_expires_at": time.time() - 1.0,
    })
    expected = queue.snapshot().expired_active[0]
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "new", "pull_policy": None, "target_commit": "a" * 40},
        "claim_token": "legacy-claim",
        "owner_token": "new-owner",
        "lease_expires_at": time.time() + 60.0,
    })

    assert queue.reject_unowned_legacy_active(
        expected,
        reason="受控淘汰无 owner legacy active",
        timeout_sec=0.3,
    ) is False
    assert queue.snapshot().active[0].owner_token == "new-owner"
    assert queue.snapshot().results == []


def test_file已扫描的过期active若在写侧续租则原子收口失败(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    path = queue.location / "active" / "demo__chroma.json"
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "claim_token": "legacy-claim",
        "owner_token": "old-owner",
        "lease_expires_at": time.time() - 1.0,
    })
    expected = queue.snapshot().expired_active[0]
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "claim_token": "legacy-claim",
        "owner_token": "old-owner",
        "lease_expires_at": time.time() + 60.0,
    })

    assert queue.settle_expired_legacy_active(
        expected,
        action="reject",
        reason="受控淘汰 legacy active",
        timeout_sec=0.3,
    ) is False
    assert queue.snapshot().active[0].token == "legacy-claim"
    assert queue.snapshot().results == []


def test_file同claim同owner但预期租约值已变化时不得收口(tmp_path) -> None:
    queue = FileSpoolQueue(tmp_path / "queue")
    path = queue.location / "active" / "demo__chroma.json"
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "claim_token": "legacy-claim",
        "owner_token": "old-owner",
        "lease_expires_at": time.time() - 2.0,
    })
    expected = queue.snapshot().expired_active[0]
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "claim_token": "legacy-claim",
        "owner_token": "old-owner",
        "lease_expires_at": time.time() - 1.0,
    })

    assert queue.settle_expired_legacy_active(
        expected,
        action="reject",
        reason="受控淘汰 legacy active",
        timeout_sec=0.3,
    ) is False
    assert queue.snapshot().expired_active[0].lease_expires_at != expected.lease_expires_at
    assert queue.snapshot().results == []


def test_file收口写入结果后active删除失败不得报告成功(tmp_path, monkeypatch) -> None:
    from codev_platform.reindex.file_queue_transitions import FileQueueTransitionError

    queue = FileSpoolQueue(tmp_path / "queue")
    path = queue.location / "active" / "demo__chroma.json"
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "claim_token": "legacy-claim",
        "owner_token": "old-owner",
        "lease_expires_at": time.time() - 1.0,
    })
    expected = queue.snapshot().expired_active[0]
    monkeypatch.setattr(queue._store, "unlink", lambda _path: False)

    with pytest.raises(FileQueueTransitionError, match="active 删除失败"):
        queue.settle_expired_legacy_active(
            expected,
            action="reject",
            reason="受控淘汰 legacy active",
            timeout_sec=0.3,
        )
    assert queue.snapshot().expired_active[0].token == expected.token
    assert queue.snapshot().results[0].key == expected.key


def test_file无owner拒绝拒绝携带owner的伪造预期(tmp_path) -> None:
    from dataclasses import replace

    queue = FileSpoolQueue(tmp_path / "queue")
    path = queue.location / "active" / "demo__chroma.json"
    queue._store.write_json_atomic(path, {
        "project_id": "demo",
        "kind": "chroma",
        "enqueued_at": 1.0,
        "meta": {"source": "legacy", "pull_policy": None, "target_commit": "HEAD"},
        "claim_token": "legacy-claim",
        "lease_expires_at": time.time() - 1.0,
    })
    expected = replace(queue.snapshot().expired_active[0], owner_token="forged-owner")

    assert queue.reject_unowned_legacy_active(
        expected,
        reason="受控淘汰无 owner legacy active",
        timeout_sec=0.3,
    ) is False
    assert queue.snapshot().expired_active
