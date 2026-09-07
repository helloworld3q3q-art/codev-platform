from __future__ import annotations


import pytest

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    RecoveryReport,
    RecoveryState,
    validate_death_proof_for_handle,
)
from codev_platform.reindex.attempts import ConfirmedProcessDeath


from tests import reindex_attempt_process_support as support


def test_death_proof_must_match_target_handle() -> None:
    assert validate_death_proof_for_handle(support._handle(), support._proof()) == support._proof()
    mismatched = ConfirmedProcessDeath(
        process_identity=support._identity(pid=support._PID + 1),
        containment_kind=support._CONTAINMENT_KIND,
        confirmed_at=12.0,
        evidence="另一个进程已死亡",
    )
    with pytest.raises(ValueError, match="handle|证明"):
        validate_death_proof_for_handle(support._handle(), mismatched)
    with pytest.raises(ValueError, match="handle|证明"):
        RecoveryReport(
            state=RecoveryState.CONFIRMED_DEAD,
            handle=support._handle(),
            death_proof=mismatched,
            note="错误目标证明",
        )


def test_start_error_preserves_ambiguous_handle_for_quarantine() -> None:
    error = AttemptProcessStartError(
        handle=support._handle(),
        death_proof=None,
        retryable=False,
        note="Job 分配失败且死亡未确认",
    )

    assert error.handle == support._handle()
    assert error.death_proof is None
    assert error.retryable is False
    assert str(error) == "Job 分配失败且死亡未确认"


def test_start_error_accepts_matching_death_proof() -> None:
    error = AttemptProcessStartError(
        handle=support._handle(),
        death_proof=support._proof(),
        retryable=True,
        note="启动失败但已确认进程死亡",
    )

    assert error.death_proof == support._proof()
    assert error.retryable is True


def test_start_error_rejects_proof_without_handle_or_mismatched_proof() -> None:
    with pytest.raises(ValueError):
        AttemptProcessStartError(
            handle=None,
            death_proof=support._proof(),
            retryable=False,
            note="证明没有对应句柄",
        )
    mismatched = ConfirmedProcessDeath(
        process_identity=support._identity(pid=support._PID + 1),
        containment_kind=support._CONTAINMENT_KIND,
        confirmed_at=12.0,
        evidence="另一个进程已死亡",
    )
    with pytest.raises(ValueError):
        AttemptProcessStartError(
            handle=support._handle(),
            death_proof=mismatched,
            retryable=False,
            note="证明与句柄不匹配",
        )


def test_start_error_fails_closed_when_live_handle_is_marked_retryable() -> None:
    with pytest.raises(ValueError):
        AttemptProcessStartError(
            handle=support._handle(),
            death_proof=None,
            retryable=True,
            note="未确认死亡时不得直接重试",
        )


@pytest.mark.parametrize("retryable", [0, 1, None, "false"])
def test_start_error_requires_strict_boolean(retryable: object) -> None:
    with pytest.raises(ValueError):
        AttemptProcessStartError(
            handle=None,
            death_proof=None,
            retryable=retryable,  # type: ignore[arg-type]
            note="启动前失败",
        )
