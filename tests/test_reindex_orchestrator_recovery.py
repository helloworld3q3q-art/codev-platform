"""reindex 编排崩溃恢复、失权收口与领取门禁测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.reindex.attempt_completion import attempt_result_digest
from codev_platform.reindex.attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationAction,
    FinalizationEvidence,
)
from codev_platform.reindex.attempt_process import (
    ExecutionHandle,
    RecoveryReport,
    RecoveryState,
    build_process_identity,
)
from codev_platform.reindex.attempts import AttemptJournalEntry, ConfirmedProcessDeath
from codev_platform.reindex.orchestrator import OrchestratorFatalError
from codev_platform.reindex.orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalRecord,
)
from tests.test_reindex_orchestrator import _claim, _orchestrator, _spec


def _record(
    phase: AttemptJournalPhase,
    *,
    claim,
    spec,
    artifacts,
    process,
    checkpoint: AttemptFinalizationCheckpoint | None = None,
) -> AttemptJournalRecord:
    handle = process.handle
    with_process = phase not in {AttemptJournalPhase.CLAIMED, AttemptJournalPhase.PREPARING}
    entry = AttemptJournalEntry(
        1,
        claim.owner_token,
        claim.claim_token,
        spec.attempt_id,
        spec.fence,
        spec.project_id,
        spec.kind,
        str(artifacts.paths.spec),
        str(artifacts.paths.result),
        handle.pid if with_process else None,
        handle.process_identity if with_process else None,
        handle.containment_kind if with_process else None,
        handle.native_ref if with_process else None,
        phase.value,
        handle.started_at if with_process else 10.0,
        spec.timeout_sec,
    )
    return AttemptJournalRecord(entry, checkpoint, spec)


def _incomplete_checkpoint(spec) -> AttemptFinalizationCheckpoint:
    return AttemptFinalizationCheckpoint(
        1,
        spec.attempt_id,
        spec.fence,
        spec.target_commit,
        FinalizationAction.RETRY,
        FinalizationEvidence.INCOMPLETE_ARTIFACT,
        None,
    )


def _completion_checkpoint(spec, result) -> AttemptFinalizationCheckpoint:
    return AttemptFinalizationCheckpoint(
        1,
        spec.attempt_id,
        spec.fence,
        spec.target_commit,
        FinalizationAction.GUARDED_ACK,
        FinalizationEvidence.COMPLETION_RECEIPT,
        attempt_result_digest(result),
    )


def test_领取前必须完成_readiness_health与崩溃恢复(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, *_rest = _orchestrator(tmp_path, events, claims=[_claim()])

    with pytest.raises(OrchestratorFatalError, match="恢复"):
        orchestrator.drain_once()

    orchestrator.recover_incomplete()

    assert events == [
        "process:assert_ready",
        "health:recover",
        "queue:recover_owned",
    ]


def test_无_journal的旧_owned_claim只_retry且不启动进程(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, _artifacts, queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    queue.recovered_claims = [claim]

    orchestrator.recover_incomplete()

    assert journal.record is None
    assert "queue:retry" in events
    assert "process:prepare" not in events
    assert "process:activate" not in events


def test_executing恢复会先杀树再用不完整凭据_retry(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, _artifacts, queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    spec = _spec()
    journal.record = _record(
        AttemptJournalPhase.EXECUTING,
        claim=claim,
        spec=spec,
        artifacts=_artifacts,
        process=process,
    )
    queue.recovered_claims = [claim]

    orchestrator.recover_incomplete()

    assert "process:recover_handle" in events
    assert "process:terminate" in events
    assert events.index("process:terminate") < events.index("queue:retry")
    assert journal.record is None
    assert "process:activate" not in events


def test_preparing明确从未启动会走无进程_finalizing_retry(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, artifacts, queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    spec = _spec()
    journal.record = _record(
        AttemptJournalPhase.PREPARING,
        claim=claim,
        spec=spec,
        artifacts=artifacts,
        process=process,
    )
    queue.recovered_claims = [claim]
    process.recovery_report = RecoveryReport(
        RecoveryState.NEVER_STARTED,
        None,
        None,
        "确定性 containment 不存在",
    )

    orchestrator.recover_incomplete()

    assert "process:recover" in events
    assert "input:cleanup_unstarted" in events
    assert "queue:retry" in events
    assert journal.record is None


def test_finalizing已失权时只清理私有资源不重放_queue动作(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, artifacts, queue, publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    spec = _spec()
    result = artifacts.validated.result
    journal.record = _record(
        AttemptJournalPhase.FINALIZING,
        claim=claim,
        spec=spec,
        artifacts=artifacts,
        process=process,
        checkpoint=_completion_checkpoint(spec, result),
    )
    process.recovery_report = RecoveryReport(
        RecoveryState.CONFIRMED_DEAD,
        process.handle,
        process._proof(),
        "历史 containment 已确认死亡",
    )

    orchestrator.recover_incomplete()

    assert "queue:retry" not in events
    assert "queue:begin_publish" not in events
    assert "publisher:publish" not in events
    assert publisher.published is True
    assert "artifacts:cleanup" in events
    assert journal.record is None


def test_finalizing仍有精确claim时重放幂等发布与_ack(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, artifacts, queue, _publisher, _health = _orchestrator(
        tmp_path,
        events,
    )
    spec = _spec()
    result = artifacts.validated.result
    journal.record = _record(
        AttemptJournalPhase.FINALIZING,
        claim=claim,
        spec=spec,
        artifacts=artifacts,
        process=process,
        checkpoint=_completion_checkpoint(spec, result),
    )
    queue.recovered_claims = [claim]
    process.recovery_report = RecoveryReport(
        RecoveryState.CONFIRMED_DEAD,
        process.handle,
        process._proof(),
        "历史 containment 已确认死亡",
    )

    orchestrator.recover_incomplete()

    assert "artifacts:load_validated" in events
    assert "publisher:publish" in events
    assert "queue:ack" in events
    assert journal.record is None


def test_恢复进程状态有歧义时必须queue_quarantine并停止(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, artifacts, queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    spec = _spec()
    journal.record = _record(
        AttemptJournalPhase.EXECUTING,
        claim=claim,
        spec=spec,
        artifacts=artifacts,
        process=process,
    )
    queue.recovered_claims = [claim]
    process.recovery_report = RecoveryReport(
        RecoveryState.UNCONFIRMED,
        None,
        None,
        "containment 状态不明",
    )

    with pytest.raises(OrchestratorFatalError, match="quarantine|恢复|状态"):
        orchestrator.recover_incomplete()

    assert "queue:quarantine" in events
    assert journal.record is not None
    assert journal.record.phase is AttemptJournalPhase.QUARANTINED


def test_恢复报告不能用同_attempt的外来_handle替代原_handle(tmp_path: Path) -> None:
    events: list[str] = []
    orchestrator, claim, process, journal, artifacts, queue, *_rest = _orchestrator(
        tmp_path,
        events,
    )
    spec = _spec()
    journal.record = _record(
        AttemptJournalPhase.FINALIZING,
        claim=claim,
        spec=spec,
        artifacts=artifacts,
        process=process,
        checkpoint=_completion_checkpoint(spec, artifacts.validated.result),
    )
    queue.recovered_claims = [claim]
    foreign_ref = "foreign-native-ref"
    foreign = ExecutionHandle(
        spec.attempt_id,
        789,
        build_process_identity(
            pid=789,
            native_ref=foreign_ref,
            birth_marker="foreign-birth",
        ),
        "foreign-containment",
        foreign_ref,
        process.handle.started_at,
    )
    foreign_proof = ConfirmedProcessDeath(
        foreign.process_identity,
        foreign.containment_kind,
        foreign.started_at + 1.0,
        "外来 containment 已死亡",
    )
    process.recovery_report = RecoveryReport(
        RecoveryState.CONFIRMED_DEAD,
        foreign,
        foreign_proof,
        "同 attempt 的外来 handle",
    )

    with pytest.raises(OrchestratorFatalError, match="handle|恢复"):
        orchestrator.recover_incomplete()

    assert "queue:ack" not in events
    assert "publisher:publish" not in events
    assert journal.record is not None
    assert journal.record.phase is AttemptJournalPhase.QUARANTINED


def test_recovery后才能领取并且不会重新运行旧_attempt(tmp_path: Path) -> None:
    events: list[str] = []
    claim = _claim()
    orchestrator, _claim_value, process, journal, artifacts, queue, *_rest = _orchestrator(
        tmp_path,
        events,
        claims=[claim],
    )
    spec = _spec()
    journal.record = _record(
        AttemptJournalPhase.PREPARING,
        claim=claim,
        spec=spec,
        artifacts=artifacts,
        process=process,
    )
    queue.recovered_claims = [claim]
    process.recovery_report = RecoveryReport(
        RecoveryState.NEVER_STARTED,
        None,
        None,
        "containment 从未创建",
    )

    orchestrator.recover_incomplete()
    events.clear()
    assert orchestrator.drain_once() == 1

    assert "process:prepare" in events
    assert journal.record is None
    assert process.handle.attempt_id == spec.attempt_id
    assert queue.claims == []
