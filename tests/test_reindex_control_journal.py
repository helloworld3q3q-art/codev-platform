"""独立 reindex control journal 的严格 codec、CAS 与并存测试。"""
from __future__ import annotations

import dataclasses
import json

import pytest

from codev_platform.reindex.attempt_finalization import (
    AttemptFinalizationCheckpoint,
    FinalizationAction,
    FinalizationEvidence,
)
from codev_platform.reindex.attempt_process import ExecutionHandle, build_process_identity
from codev_platform.reindex.attempts import (
    AttemptJournalEntry,
    AttemptSpec,
    CanonicalJsonObject,
)
from codev_platform.reindex.control_journal import (
    ControlJournal,
    ControlJournalConflictError,
    ControlJournalCorruptionError,
)
from codev_platform.reindex.file_durability import durable_write_replace
from codev_platform.reindex.health_refresh import HealthOperationEntry
from codev_platform.reindex.orchestrator_models import (
    AttemptJournalPhase,
    AttemptJournalRecord,
)

_TARGET = "a" * 40
_RUNTIME = "b" * 64


def _handle(attempt_id: str, *, pid: int = 123) -> ExecutionHandle:
    native_ref = f"native:{attempt_id}:{pid}"
    return ExecutionHandle(
        attempt_id,
        pid,
        build_process_identity(
            pid=pid,
            native_ref=native_ref,
            birth_marker=f"birth:{attempt_id}:{pid}",
        ),
        "test-containment",
        native_ref,
        100.0,
    )


def _spec() -> AttemptSpec:
    return AttemptSpec(
        1,
        "attempt-a",
        "fence-a",
        "project-a",
        "codegraph",
        "configured",
        CanonicalJsonObject.from_value({"project_id": "project-a"}),
        _TARGET,
        30.0,
        _RUNTIME,
    )


def _record(
    phase: AttemptJournalPhase = AttemptJournalPhase.CLAIMED,
    *,
    handle: ExecutionHandle | None = None,
    checkpoint: AttemptFinalizationCheckpoint | None = None,
) -> AttemptJournalRecord:
    spec = _spec()
    entry = AttemptJournalEntry(
        1,
        "queue-owner-a",
        "claim-a",
        spec.attempt_id,
        spec.fence,
        spec.project_id,
        spec.kind,
        "C:/attempts/a/spec.json",
        "C:/attempts/a/result.json",
        handle.pid if handle else None,
        handle.process_identity if handle else None,
        handle.containment_kind if handle else None,
        handle.native_ref if handle else None,
        phase.value,
        handle.started_at if handle else 10.0,
        spec.timeout_sec,
    )
    return AttemptJournalRecord(entry, checkpoint, spec)


def _health() -> HealthOperationEntry:
    handle = _handle("health-a", pid=456)
    return HealthOperationEntry(1, "health-a", "project-a", handle, 100.0, 10.0)


def _journal(tmp_path) -> ControlJournal:
    return ControlJournal(
        owner_token="queue-owner-a",
        path=(tmp_path / "reindex-control-journal.json").resolve(),
    )


def test_attempt与health可耐久共存且可分别清理(tmp_path) -> None:
    journal = _journal(tmp_path)
    claimed = _record()
    health = _health()

    journal.create(claimed)
    journal.save_health(health)

    assert journal.load() == claimed
    assert journal.load_health() == health
    journal.clear_health(operation_id=health.operation_id)
    assert journal.load() == claimed
    assert journal.load_health() is None


def test_attempt_state跃迁与清理必须严格CAS(tmp_path) -> None:
    journal = _journal(tmp_path)
    claimed = _record()
    preparing = _record(AttemptJournalPhase.PREPARING)
    journal.create(claimed)

    journal.transition(preparing, expected=AttemptJournalPhase.CLAIMED)

    with pytest.raises(ControlJournalConflictError, match="phase|CAS"):
        journal.transition(preparing, expected=AttemptJournalPhase.CLAIMED)
    with pytest.raises(ControlJournalConflictError, match="attempt|fence|CAS"):
        journal.clear(
            attempt_id="attempt-a",
            fence="fence-other",
            expected=AttemptJournalPhase.PREPARING,
        )
    journal.clear(
        attempt_id="attempt-a",
        fence="fence-a",
        expected=AttemptJournalPhase.PREPARING,
    )
    assert journal.load() is None


def test_不同queue_owner不得跃迁或清理既有attempt(tmp_path) -> None:
    journal = _journal(tmp_path)
    foreign = ControlJournal(
        owner_token="queue-owner-b",
        path=(tmp_path / "reindex-control-journal.json").resolve(),
    )
    claimed = _record()
    preparing = _record(AttemptJournalPhase.PREPARING)
    journal.create(claimed)

    with pytest.raises(ControlJournalConflictError, match="owner|所有者"):
        foreign.transition(preparing, expected=AttemptJournalPhase.CLAIMED)
    with pytest.raises(ControlJournalConflictError, match="owner|所有者"):
        foreign.clear(
            attempt_id="attempt-a",
            fence="fence-a",
            expected=AttemptJournalPhase.CLAIMED,
        )

    assert journal.load() == claimed


def test_preparing首次绑定完整handle后不得替换(tmp_path) -> None:
    journal = _journal(tmp_path)
    claimed = _record()
    preparing = _record(AttemptJournalPhase.PREPARING)
    first = _record(AttemptJournalPhase.EXECUTING, handle=_handle("attempt-a", pid=123))
    different = _record(AttemptJournalPhase.TERMINATING, handle=_handle("attempt-a", pid=789))
    same = dataclasses.replace(
        first,
        entry=dataclasses.replace(first.entry, state=AttemptJournalPhase.TERMINATING.value),
    )
    journal.create(claimed)
    journal.transition(preparing, expected=AttemptJournalPhase.CLAIMED)
    journal.transition(first, expected=AttemptJournalPhase.PREPARING)

    with pytest.raises(ControlJournalConflictError, match="handle|CAS"):
        journal.transition(different, expected=AttemptJournalPhase.EXECUTING)
    journal.transition(same, expected=AttemptJournalPhase.EXECUTING)
    assert journal.load() == same


def test_finalizing同phase重放必须完全相同(tmp_path) -> None:
    journal = _journal(tmp_path)
    handle = _handle("attempt-a")
    claimed = _record()
    preparing = _record(AttemptJournalPhase.PREPARING)
    executing = _record(AttemptJournalPhase.EXECUTING, handle=handle)
    checkpoint = AttemptFinalizationCheckpoint(
        1,
        "attempt-a",
        "fence-a",
        _TARGET,
        FinalizationAction.GUARDED_ACK,
        FinalizationEvidence.COMPLETION_RECEIPT,
        "d" * 64,
    )
    finalizing = _record(
        AttemptJournalPhase.FINALIZING,
        handle=handle,
        checkpoint=checkpoint,
    )
    forged = dataclasses.replace(
        finalizing,
        finalization=dataclasses.replace(checkpoint, result_digest="e" * 64),
    )
    journal.create(claimed)
    journal.transition(preparing, expected=AttemptJournalPhase.CLAIMED)
    journal.transition(executing, expected=AttemptJournalPhase.PREPARING)
    journal.transition(finalizing, expected=AttemptJournalPhase.EXECUTING)

    journal.transition(finalizing, expected=AttemptJournalPhase.FINALIZING)
    with pytest.raises(ControlJournalConflictError, match="FINALIZING|CAS"):
        journal.transition(forged, expected=AttemptJournalPhase.FINALIZING)


def test_checkpoint目标提交与持久spec不一致时按损坏journal失败关闭(tmp_path) -> None:
    journal = _journal(tmp_path)
    handle = _handle("attempt-a")
    claimed = _record()
    preparing = _record(AttemptJournalPhase.PREPARING)
    executing = _record(AttemptJournalPhase.EXECUTING, handle=handle)
    checkpoint = AttemptFinalizationCheckpoint(
        1,
        "attempt-a",
        "fence-a",
        _TARGET,
        FinalizationAction.GUARDED_ACK,
        FinalizationEvidence.COMPLETION_RECEIPT,
        "d" * 64,
    )
    finalizing = _record(
        AttemptJournalPhase.FINALIZING,
        handle=handle,
        checkpoint=checkpoint,
    )
    journal.create(claimed)
    journal.transition(preparing, expected=AttemptJournalPhase.CLAIMED)
    journal.transition(executing, expected=AttemptJournalPhase.PREPARING)
    journal.transition(finalizing, expected=AttemptJournalPhase.EXECUTING)

    path = tmp_path / "reindex-control-journal.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["attempt"]["finalization"]["target_commit"] = "c" * 40
    durable_write_replace(
        path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )

    with pytest.raises(ControlJournalCorruptionError, match="target_commit|checkpoint|schema"):
        journal.load()


def test_health_operation只能精确清理(tmp_path) -> None:
    journal = _journal(tmp_path)
    health = _health()
    journal.save_health(health)

    with pytest.raises(ControlJournalConflictError, match="operation"):
        journal.clear_health(operation_id="health-other")
    journal.clear_health(operation_id=health.operation_id)
    assert journal.load_health() is None


def test损坏或未知字段必须失败关闭且不覆盖(tmp_path) -> None:
    journal = _journal(tmp_path)
    path = tmp_path / "reindex-control-journal.json"
    durable_write_replace(
        path,
        b'{"schema_version":1,"attempt":null,"health":null,"unknown":true}',
    )
    before = path.read_bytes()

    with pytest.raises(ControlJournalCorruptionError, match="字段|schema"):
        journal.load()
    with pytest.raises(ControlJournalCorruptionError, match="字段|schema"):
        journal.create(_record())

    assert path.read_bytes() == before


def test_control_journal与supervisor状态文件物理隔离(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    journal = ControlJournal(owner_token="queue-owner-a")

    assert journal._path.name == "reindex-control-journal.json"  # noqa: SLF001
    assert journal._path != (tmp_path / "run" / "reindex-worker-state.json")  # noqa: SLF001
