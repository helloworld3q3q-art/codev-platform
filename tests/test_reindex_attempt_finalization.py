"""FINALIZING checkpoint 的动作、证据与严格 codec 测试。"""
from __future__ import annotations

import dataclasses

import pytest

from codev_platform.reindex.attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationAction,
    FinalizationEvidence,
    decode_finalization_checkpoint,
    encode_finalization_checkpoint,
    validate_finalization_checkpoint,
)
from codev_platform.reindex.attempt_completion import attempt_result_digest
from codev_platform.reindex.attempts import (
    AttemptJournalEntry,
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
)

_OID = "a" * 40


def _spec(**changes: object) -> AttemptSpec:
    value = AttemptSpec(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-secret",
        project_id="demo",
        kind="ingest",
        input_kind="configured",
        input_payload=CanonicalJsonObject.from_value({"project_id": "demo"}),
        target_commit=_OID,
        timeout_sec=30.0,
        runtime_revision="c" * 64,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _result(**changes: object) -> AttemptResult:
    value = AttemptResult(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-secret",
        project_id="demo",
        kind="ingest",
        input_root="/work/input",
        target_commit=_OID,
        input_commits=(("primary", _OID),),
        input_trees=(("primary", "d" * 40),),
        runtime_revision="c" * 64,
        outcome=AttemptOutcome.SUCCEEDED,
        rc=0,
        retryable=False,
        note="",
        timing=(("started_at", 10.0), ("finished_at", 12.0)),
        log_ref=None,
        proof=CanonicalJsonObject.from_value({"success": True}),
    )
    return dataclasses.replace(value, **changes) if changes else value


_DIGEST = attempt_result_digest(_result())
_RETRY_RESULT = _result(
    outcome=AttemptOutcome.RETRYABLE,
    rc=2,
    retryable=True,
    input_root="",
    proof=CanonicalJsonObject.from_value({"success": False}),
)
_RETRY_DIGEST = attempt_result_digest(_RETRY_RESULT)
_DEPENDENCY_RESULT = _result(
    outcome=AttemptOutcome.FAILED,
    rc=None,
    input_root="",
    input_commits=(),
    input_trees=(),
    timing=(),
    log_ref=None,
    proof=CanonicalJsonObject.from_value({
        "dependency": {
            "dependency_kind": "codegraph",
            "dependent_kind": "ingest",
            "disposition": "block",
            "manifest": {
                "attempt_id": "dependency-attempt",
                "result_digest": "e" * 64,
                "status": "failed",
                "target_commit": _OID,
            },
            "project_id": "demo",
            "queue_state": "absent",
            "target_commit": _OID,
        },
        "evidence": "dependency_block",
        "success": False,
    }),
)
_DEPENDENCY_DIGEST = attempt_result_digest(_DEPENDENCY_RESULT)


def _checkpoint(**changes: object) -> AttemptFinalizationCheckpoint:
    value = AttemptFinalizationCheckpoint(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-secret",
        target_commit=_OID,
        action=FinalizationAction.GUARDED_ACK,
        evidence=FinalizationEvidence.COMPLETION_RECEIPT,
        result_digest=_DIGEST,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _journal(*, with_process: bool = True, **changes: object) -> AttemptJournalEntry:
    value = AttemptJournalEntry(
        schema_version=1,
        owner_token="owner-1",
        claim_token="claim-secret",
        attempt_id="attempt-1",
        fence="fence-secret",
        project_id="demo",
        kind="all",
        spec_path="C:/artifacts/spec.json",
        result_path="C:/artifacts/result.json",
        pid=123 if with_process else None,
        process_identity="identity-1" if with_process else None,
        containment_kind="windows-job" if with_process else None,
        native_ref="job-1" if with_process else None,
        state="finalizing",
        started_at=10.0,
        timeout_sec=30.0,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _validate(
    checkpoint: AttemptFinalizationCheckpoint,
    journal: AttemptJournalEntry,
    spec: AttemptSpec | None = None,
    result: AttemptResult | None = None,
) -> None:
    resolved_result = result
    no_result_evidence = {
        FinalizationEvidence.INCOMPLETE_ARTIFACT,
        FinalizationEvidence.NO_PROCESS_RETRY,
    }
    if result is None and checkpoint.evidence not in no_result_evidence:
        if checkpoint.evidence is FinalizationEvidence.DEPENDENCY_BLOCK:
            resolved_result = _DEPENDENCY_RESULT
        elif checkpoint.action is FinalizationAction.RETRY:
            resolved_result = _RETRY_RESULT
        else:
            resolved_result = _result()
    validate_finalization_checkpoint(
        checkpoint,
        journal,
        spec=spec or _spec(),
        result=resolved_result,
    )


@pytest.mark.parametrize(
    ("action", "evidence", "digest", "with_process"),
    [
        (FinalizationAction.GUARDED_ACK, FinalizationEvidence.COMPLETION_RECEIPT, _DIGEST, True),
        (FinalizationAction.RETRY, FinalizationEvidence.COMPLETION_RECEIPT, _RETRY_DIGEST, True),
        (FinalizationAction.RETRY, FinalizationEvidence.INCOMPLETE_ARTIFACT, None, True),
        (FinalizationAction.RETRY, FinalizationEvidence.NO_PROCESS_RETRY, None, False),
        (
            FinalizationAction.GUARDED_ACK,
            FinalizationEvidence.DEPENDENCY_BLOCK,
            _DEPENDENCY_DIGEST,
            False,
        ),
    ],
)
def test_checkpoint_接受唯一合法动作证据组合(action, evidence, digest, with_process) -> None:
    checkpoint = _checkpoint(action=action, evidence=evidence, result_digest=digest)

    _validate(checkpoint, _journal(with_process=with_process))

    assert decode_finalization_checkpoint(encode_finalization_checkpoint(checkpoint)) == checkpoint


@pytest.mark.parametrize(
    ("action", "evidence", "digest"),
    [
        (FinalizationAction.GUARDED_ACK, FinalizationEvidence.COMPLETION_RECEIPT, None),
        (FinalizationAction.GUARDED_ACK, FinalizationEvidence.INCOMPLETE_ARTIFACT, None),
        (FinalizationAction.RETRY, FinalizationEvidence.INCOMPLETE_ARTIFACT, _DIGEST),
        (FinalizationAction.GUARDED_ACK, FinalizationEvidence.NO_PROCESS_RETRY, None),
        (FinalizationAction.RETRY, FinalizationEvidence.NO_PROCESS_RETRY, _DIGEST),
        (FinalizationAction.RETRY, FinalizationEvidence.DEPENDENCY_BLOCK, _DIGEST),
    ],
)
def test_checkpoint_拒绝动作证据摘要冲突(action, evidence, digest) -> None:
    with pytest.raises(ValueError, match="动作|证据|摘要|digest"):
        _checkpoint(action=action, evidence=evidence, result_digest=digest)


def test_checkpoint_拒绝身份进程形状和状态冲突() -> None:
    checkpoint = _checkpoint()
    invalid = (
        _journal(fence="other"),
        _journal(state="executing"),
        _journal(pid=None),
        _journal(with_process=False),
    )
    for journal in invalid:
        with pytest.raises(ValueError):
            _validate(checkpoint, journal)

    with pytest.raises(ValueError, match="spec|身份"):
        _validate(checkpoint, _journal(), _spec(target_commit="d" * 40))
    with pytest.raises(ValueError, match="摘要|digest"):
        _validate(dataclasses.replace(checkpoint, result_digest="d" * 64), _journal())
    with pytest.raises(ValueError, match="result|身份"):
        _validate(checkpoint, _journal(), result=_result(fence="other"))
    with pytest.raises(ValueError, match="Result|结果"):
        validate_finalization_checkpoint(
            checkpoint,
            _journal(),
            spec=_spec(),
            result=None,
        )

    incomplete = _checkpoint(
        action=FinalizationAction.RETRY,
        evidence=FinalizationEvidence.INCOMPLETE_ARTIFACT,
        result_digest=None,
    )
    with pytest.raises(ValueError, match="不能绑定 result"):
        validate_finalization_checkpoint(
            incomplete,
            _journal(),
            spec=_spec(),
            result=_result(),
        )

    dependency = _checkpoint(
        evidence=FinalizationEvidence.DEPENDENCY_BLOCK,
        result_digest=_DEPENDENCY_DIGEST,
    )
    with pytest.raises(ValueError, match="进程|prepare"):
        _validate(dependency, _journal(with_process=True))


def test_checkpoint_codec_字段严格且不携带_claim_token() -> None:
    checkpoint = _checkpoint()
    payload = encode_finalization_checkpoint(checkpoint)
    raw = payload.text

    assert "claim_token" not in raw
    assert "claim-secret" not in raw
    assert decode_finalization_checkpoint(payload) == checkpoint

    value = payload.to_value()
    value["unknown"] = True
    with pytest.raises(ValueError, match="字段"):
        decode_finalization_checkpoint(CanonicalJsonObject.from_value(value))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"attempt_id": " "},
        {"target_commit": "A" * 40},
        {"result_digest": "B" * 64},
        {"action": "guarded_ack"},
        {"evidence": "completion_receipt"},
    ],
)
def test_checkpoint_拒绝宽松标量和动态枚举(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _checkpoint(**changes)


def test_dependency_block_checkpoint_只允许空进程形状() -> None:
    checkpoint = _checkpoint(
        evidence=FinalizationEvidence.DEPENDENCY_BLOCK,
        result_digest=_DEPENDENCY_DIGEST,
    )

    _validate(checkpoint, _journal(with_process=False))

    encoded = encode_finalization_checkpoint(checkpoint).text
    assert "claim-secret" not in encoded


def test_no_process_retry_checkpoint_只允许空进程且不绑定_result() -> None:
    checkpoint = _checkpoint(
        action=FinalizationAction.RETRY,
        evidence=FinalizationEvidence.NO_PROCESS_RETRY,
        result_digest=None,
    )

    _validate(checkpoint, _journal(with_process=False))

    with pytest.raises(ValueError, match="进程|prepare"):
        _validate(checkpoint, _journal(with_process=True))
    with pytest.raises(ValueError, match="不能绑定 result"):
        _validate(
            checkpoint,
            _journal(with_process=False),
            result=_RETRY_RESULT,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"rc": 1},
        {"retryable": True},
        {"input_root": "/forged"},
        {"input_commits": (("primary", _OID),)},
        {"input_trees": (("primary", "d" * 40),)},
        {"log_ref": "logs/forged.log"},
        {"proof": CanonicalJsonObject.from_value({"success": False})},
    ],
)
def test_dependency_block_checkpoint_拒绝伪造进程输入或证据(
    changes: dict[str, object],
) -> None:
    result = dataclasses.replace(_DEPENDENCY_RESULT, **changes)
    checkpoint = _checkpoint(
        evidence=FinalizationEvidence.DEPENDENCY_BLOCK,
        result_digest=attempt_result_digest(result),
    )

    with pytest.raises(ValueError, match="依赖阻断"):
        _validate(checkpoint, _journal(with_process=False), result=result)


def test_checkpoint_不允许提前固化_publish_或_supersede_结果() -> None:
    values = {action.value for action in FinalizationAction}

    assert values == {"guarded_ack", "retry"}
    assert "publish_ack" not in values
    assert "supersede_ack" not in values


def test_checkpoint_动作必须与_result_outcome_一致() -> None:
    guarded_retry = _checkpoint(result_digest=_RETRY_DIGEST)
    retry_success = _checkpoint(action=FinalizationAction.RETRY)

    with pytest.raises(ValueError, match="动作|outcome"):
        _validate(guarded_retry, _journal(), result=_RETRY_RESULT)
    with pytest.raises(ValueError, match="动作|outcome"):
        _validate(retry_success, _journal(), result=_result())
