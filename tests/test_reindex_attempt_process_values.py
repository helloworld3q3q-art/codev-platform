from __future__ import annotations

import dataclasses
import math

import pytest

from codev_platform.reindex.attempt_process import (
    Deadline,
    ExecutionHandle,
    ProcessReference,
    build_process_identity,
    handle_epoch_time,
    validate_process_identity,
)


from tests import reindex_attempt_process_support as support


def test_handle_epoch_time_clamps_wall_clock_rollback_to_process_start() -> None:
    handle = support._handle()

    assert handle_epoch_time(handle, 9.0) == 10.0
    assert handle_epoch_time(handle, 11.0) == 11.0


@pytest.mark.parametrize("observed_at", [None, True, -1, float("nan"), float("inf")])
def test_handle_epoch_time_rejects_invalid_epoch(observed_at: object) -> None:
    with pytest.raises(ValueError, match="时间|handle"):
        handle_epoch_time(support._handle(), observed_at)


def test_process_identity_is_versioned_and_binds_native_reference() -> None:
    identity = support._identity()

    assert identity.startswith("reindex-process:v1:")
    validate_process_identity(
        identity,
        pid=support._PID,
        native_ref=support._NATIVE_REF,
        birth_marker=support._BIRTH_MARKER,
    )

    with pytest.raises(ValueError):
        validate_process_identity(identity, pid=support._PID, native_ref="posix-session:999")
    with pytest.raises(ValueError):
        validate_process_identity(identity, pid=support._PID + 1, native_ref=support._NATIVE_REF)
    with pytest.raises(ValueError):
        validate_process_identity(
            identity,
            pid=support._PID,
            native_ref=support._NATIVE_REF,
            birth_marker="proc-start-ticks:reused",
        )


@pytest.mark.parametrize("pid", [0, -1, True, 1.5])
def test_process_identity_rejects_invalid_pid(pid: object) -> None:
    with pytest.raises(ValueError):
        build_process_identity(
            pid=pid,  # type: ignore[arg-type]
            native_ref=support._NATIVE_REF,
            birth_marker=support._BIRTH_MARKER,
        )


@pytest.mark.parametrize("field", ["native_ref", "birth_marker"])
@pytest.mark.parametrize("value", ["", "  ", "x" * 4097])
def test_process_identity_rejects_empty_or_unbounded_input(field: str, value: str) -> None:
    inputs = {
        "pid": support._PID,
        "native_ref": support._NATIVE_REF,
        "birth_marker": support._BIRTH_MARKER,
    }
    inputs[field] = value

    with pytest.raises(ValueError):
        build_process_identity(**inputs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "identity",
    [
        "pid:321:start:987654",
        "reindex-process:v2:321:" + "a" * 64 + ":" + "b" * 64,
        "reindex-process:v1:321:not-a-digest:" + "b" * 64,
    ],
)
def test_process_reference_rejects_unversioned_or_malformed_identity(identity: str) -> None:
    with pytest.raises(ValueError):
        ProcessReference(
            process_identity=identity,
            containment_kind=support._CONTAINMENT_KIND,
            native_ref=support._NATIVE_REF,
        )


def test_execution_values_are_frozen_slotted_and_cross_validate_identity() -> None:
    handle = support._handle()
    reference = ProcessReference(
        process_identity=handle.process_identity,
        containment_kind=handle.containment_kind,
        native_ref=handle.native_ref,
    )

    assert not hasattr(handle, "__dict__")
    assert not hasattr(reference, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        handle.pid = 99  # type: ignore[misc]
    with pytest.raises(ValueError):
        ExecutionHandle(
            attempt_id="attempt-1",
            pid=support._PID + 1,
            process_identity=support._identity(),
            containment_kind=support._CONTAINMENT_KIND,
            native_ref=support._NATIVE_REF,
            started_at=10.0,
        )


@pytest.mark.parametrize("field", ["attempt_id", "containment_kind", "native_ref"])
@pytest.mark.parametrize("value", ["", "  ", "x" * 4097])
def test_execution_handle_rejects_empty_or_unbounded_text(field: str, value: str) -> None:
    values: dict[str, object] = {
        "attempt_id": "attempt-1",
        "pid": support._PID,
        "process_identity": support._identity(),
        "containment_kind": support._CONTAINMENT_KIND,
        "native_ref": support._NATIVE_REF,
        "started_at": 10.0,
    }
    values[field] = value
    if field == "native_ref" and value.strip() and len(value.encode("utf-8")) <= 4096:
        values["process_identity"] = support._identity(native_ref=value)

    with pytest.raises(ValueError):
        ExecutionHandle(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("started_at", [-1.0, math.inf, math.nan, True])
def test_execution_handle_rejects_invalid_start_time(started_at: object) -> None:
    with pytest.raises(ValueError):
        ExecutionHandle(
            attempt_id="attempt-1",
            pid=support._PID,
            process_identity=support._identity(),
            containment_kind=support._CONTAINMENT_KIND,
            native_ref=support._NATIVE_REF,
            started_at=started_at,  # type: ignore[arg-type]
        )


def test_deadline_uses_one_monotonic_absolute_budget() -> None:
    deadline = Deadline.start(2.5, now=100.0)

    assert deadline.expires_at == 102.5
    assert deadline.remaining(now=101.0) == 1.5
    assert deadline.remaining(now=103.0) == 0.0
    assert deadline.expired(now=102.5) is True
    assert deadline.expired(now=102.499) is False
    assert not hasattr(deadline, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        deadline.expires_at = 999.0  # type: ignore[misc]


@pytest.mark.parametrize("timeout", [-0.01, math.inf, math.nan, True, "1"])
def test_deadline_rejects_invalid_timeout(timeout: object) -> None:
    with pytest.raises(ValueError):
        Deadline.start(timeout, now=100.0)  # type: ignore[arg-type]


@pytest.mark.parametrize("now", [-1.0, math.inf, math.nan, True])
def test_deadline_rejects_invalid_monotonic_time(now: object) -> None:
    with pytest.raises(ValueError):
        Deadline.start(1.0, now=now)  # type: ignore[arg-type]

    deadline = Deadline.start(1.0, now=100.0)
    with pytest.raises(ValueError):
        deadline.remaining(now=now)  # type: ignore[arg-type]


def test_deadline_allows_immediately_expired_budget() -> None:
    deadline = Deadline.start(0.0, now=100.0)

    assert deadline.remaining(now=100.0) == 0.0
    assert deadline.expired(now=100.0) is True
