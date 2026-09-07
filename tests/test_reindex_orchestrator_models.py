"""reindex 编排 journal 状态与 CAS 端口契约测试。"""
from __future__ import annotations

import dataclasses
import inspect

import pytest

from codev_platform.reindex.attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationAction,
    FinalizationEvidence,
)
from codev_platform.reindex.attempts import AttemptJournalEntry, AttemptSpec, CanonicalJsonObject
from codev_platform.reindex.orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalPort,
    AttemptJournalRecord,
    validate_journal_transition,
)

_TARGET = "a" * 40


def _entry(
    phase: AttemptJournalPhase,
    *,
    with_process: bool,
    **changes: object,
) -> AttemptJournalEntry:
    value = AttemptJournalEntry(
        1,
        "owner-1",
        "claim-secret",
        "attempt-1",
        "fence-secret",
        "demo",
        "chroma",
        "C:/artifacts/spec.json",
        "C:/artifacts/result.json",
        123 if with_process else None,
        "process-identity" if with_process else None,
        "windows-job" if with_process else None,
        "job-ref" if with_process else None,
        phase.value,
        10.0,
        30.0,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _checkpoint(
    *,
    evidence: FinalizationEvidence = FinalizationEvidence.COMPLETION_RECEIPT,
    **changes: object,
) -> AttemptFinalizationCheckpoint:
    no_result = evidence in {
        FinalizationEvidence.INCOMPLETE_ARTIFACT,
        FinalizationEvidence.NO_PROCESS_RETRY,
    }
    value = AttemptFinalizationCheckpoint(
        1,
        "attempt-1",
        "fence-secret",
        _TARGET,
        FinalizationAction.RETRY if no_result else FinalizationAction.GUARDED_ACK,
        evidence,
        None if no_result else "d" * 64,
    )
    return dataclasses.replace(value, **changes) if changes else value


def test_journal_port_使用显式_cas_而不是宽松覆盖() -> None:
    assert tuple(inspect.signature(AttemptJournalPort.load).parameters) == ("self",)
    assert tuple(inspect.signature(AttemptJournalPort.create).parameters) == (
        "self",
        "record",
    )
    assert tuple(inspect.signature(AttemptJournalPort.transition).parameters) == (
        "self",
        "record",
        "expected",
    )
    assert tuple(inspect.signature(AttemptJournalPort.clear).parameters) == (
        "self",
        "attempt_id",
        "fence",
        "expected",
    )


def test_preparing_与_no_process_retry_只接受空进程形状() -> None:
    preparing = AttemptJournalRecord(
        _entry(AttemptJournalPhase.PREPARING, with_process=False)
    )
    finalizing = AttemptJournalRecord(
        _entry(AttemptJournalPhase.FINALIZING, with_process=False),
        _checkpoint(evidence=FinalizationEvidence.NO_PROCESS_RETRY),
    )

    assert preparing.phase is AttemptJournalPhase.PREPARING
    assert finalizing.phase is AttemptJournalPhase.FINALIZING

    with pytest.raises(ValueError, match="phase|进程|形状"):
        AttemptJournalRecord(_entry(AttemptJournalPhase.PREPARING, with_process=True))


def test_finalizing_checkpoint_必须绑定同一_attempt_和_fence() -> None:
    entry = _entry(AttemptJournalPhase.FINALIZING, with_process=True)

    for checkpoint in (
        _checkpoint(attempt_id="attempt-other"),
        _checkpoint(fence="fence-other"),
    ):
        with pytest.raises(ValueError, match="身份|attempt|fence"):
            AttemptJournalRecord(entry, checkpoint)


def test_journal_记录的_spec也必须绑定同一_attempt_fence和任务身份() -> None:
    spec = AttemptSpec(
        1,
        "attempt-1",
        "fence-secret",
        "demo",
        "chroma",
        "configured",
        CanonicalJsonObject.from_value({"project_id": "demo"}),
        _TARGET,
        30.0,
        "c" * 64,
    )
    entry = _entry(AttemptJournalPhase.CLAIMED, with_process=False)

    assert AttemptJournalRecord(entry, spec=spec).spec == spec
    with pytest.raises(ValueError, match="spec|身份"):
        AttemptJournalRecord(
            entry,
            spec=dataclasses.replace(spec, attempt_id="attempt-other"),
        )


def test_finalizing_checkpoint必须绑定持久spec的目标提交() -> None:
    spec = AttemptSpec(
        1,
        "attempt-1",
        "fence-secret",
        "demo",
        "chroma",
        "configured",
        CanonicalJsonObject.from_value({"project_id": "demo"}),
        _TARGET,
        30.0,
        "c" * 64,
    )
    entry = _entry(AttemptJournalPhase.FINALIZING, with_process=True)

    with pytest.raises(ValueError, match="target_commit|目标提交|checkpoint"):
        AttemptJournalRecord(
            entry,
            _checkpoint(target_commit="b" * 40),
            spec,
        )


def test_quarantined_必须有完整进程引用且没有_checkpoint() -> None:
    full = _entry(AttemptJournalPhase.QUARANTINED, with_process=True)

    assert AttemptJournalRecord(full).phase is AttemptJournalPhase.QUARANTINED
    with pytest.raises(ValueError, match="phase|进程|形状"):
        AttemptJournalRecord(_entry(AttemptJournalPhase.QUARANTINED, with_process=False))
    with pytest.raises(ValueError, match="phase|finalization|形状"):
        AttemptJournalRecord(full, _checkpoint())


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (AttemptJournalPhase.CLAIMED, AttemptJournalPhase.PREPARING),
        (AttemptJournalPhase.CLAIMED, AttemptJournalPhase.FINALIZING),
        (AttemptJournalPhase.PREPARING, AttemptJournalPhase.EXECUTING),
        (AttemptJournalPhase.PREPARING, AttemptJournalPhase.FINALIZING),
        (AttemptJournalPhase.PREPARING, AttemptJournalPhase.QUARANTINED),
        (AttemptJournalPhase.EXECUTING, AttemptJournalPhase.TERMINATING),
        (AttemptJournalPhase.EXECUTING, AttemptJournalPhase.FINALIZING),
        (AttemptJournalPhase.EXECUTING, AttemptJournalPhase.QUARANTINED),
        (AttemptJournalPhase.TERMINATING, AttemptJournalPhase.FINALIZING),
        (AttemptJournalPhase.TERMINATING, AttemptJournalPhase.QUARANTINED),
        (AttemptJournalPhase.FINALIZING, AttemptJournalPhase.FINALIZING),
    ],
)
def test_journal_只接受显式合法跃迁(before, after) -> None:
    validate_journal_transition(before, after)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (AttemptJournalPhase.CLAIMED, AttemptJournalPhase.EXECUTING),
        (AttemptJournalPhase.PREPARING, AttemptJournalPhase.CLAIMED),
        (AttemptJournalPhase.FINALIZING, AttemptJournalPhase.EXECUTING),
        (AttemptJournalPhase.QUARANTINED, AttemptJournalPhase.FINALIZING),
    ],
)
def test_journal_拒绝回退和跳跃跃迁(before, after) -> None:
    with pytest.raises(ValueError, match="跃迁"):
        validate_journal_transition(before, after)
