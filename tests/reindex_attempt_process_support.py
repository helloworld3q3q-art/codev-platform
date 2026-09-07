from __future__ import annotations


from codev_platform.reindex.attempt_process import (
    ExecutionHandle,
    build_process_identity,
)
from codev_platform.reindex.attempts import AttemptJournalEntry, ConfirmedProcessDeath


_PID = 321
_NATIVE_REF = "posix-session:321"
_BIRTH_MARKER = "proc-start-ticks:987654"
_CONTAINMENT_KIND = "posix_session_v1"


def _identity(
    *,
    pid: int = _PID,
    native_ref: str = _NATIVE_REF,
    birth_marker: str = _BIRTH_MARKER,
) -> str:
    return build_process_identity(
        pid=pid,
        native_ref=native_ref,
        birth_marker=birth_marker,
    )


def _handle() -> ExecutionHandle:
    return ExecutionHandle(
        attempt_id="attempt-1",
        pid=_PID,
        process_identity=_identity(),
        containment_kind=_CONTAINMENT_KIND,
        native_ref=_NATIVE_REF,
        started_at=10.0,
    )


def _proof(*, confirmed_at: float = 12.0) -> ConfirmedProcessDeath:
    return ConfirmedProcessDeath(
        process_identity=_identity(),
        containment_kind=_CONTAINMENT_KIND,
        confirmed_at=confirmed_at,
        evidence="进程组成员已清空",
    )


def _journal(**overrides) -> AttemptJournalEntry:
    values = {
        "schema_version": 1,
        "owner_token": "owner",
        "claim_token": "claim",
        "attempt_id": "attempt-1",
        "fence": "fence",
        "project_id": "demo",
        "kind": "chroma",
        "spec_path": "spec.json",
        "result_path": "result.json",
        "pid": _PID,
        "process_identity": _identity(),
        "containment_kind": _CONTAINMENT_KIND,
        "native_ref": _NATIVE_REF,
        "state": "running",
        "started_at": 10.0,
        "timeout_sec": 30.0,
    }
    values.update(overrides)
    return AttemptJournalEntry(**values)
