from __future__ import annotations


import pytest

from codev_platform.reindex.attempt_process import (
    RecoveryReport,
    RecoveryState,
    TerminationReport,
)


from tests import reindex_attempt_process_support as support


def test_termination_report_accepts_graceful_and_forced_death() -> None:
    graceful = TerminationReport(
        requested_at=10.0,
        finished_at=12.0,
        graceful=True,
        forced=False,
        confirmed_dead=True,
        death_proof=support._proof(),
        note="TERM 后退出",
    )
    forced = TerminationReport(
        requested_at=10.0,
        finished_at=12.0,
        graceful=False,
        forced=True,
        confirmed_dead=True,
        death_proof=support._proof(),
        note="KILL 后确认退出",
    )
    unconfirmed = TerminationReport(
        requested_at=10.0,
        finished_at=12.0,
        graceful=False,
        forced=True,
        confirmed_dead=False,
        death_proof=None,
        note="已强杀但无法确认死亡",
    )

    assert graceful.graceful is True
    assert forced.forced is True
    assert unconfirmed.confirmed_dead is False
    assert not hasattr(graceful, "__dict__")


@pytest.mark.parametrize(
    ("graceful", "forced", "confirmed_dead", "proof"),
    [
        (True, True, True, "proof"),
        (True, False, False, None),
        (False, False, True, "proof"),
        (False, True, True, None),
        (False, True, False, "proof"),
    ],
)
def test_termination_report_rejects_inconsistent_state(
    graceful: bool,
    forced: bool,
    confirmed_dead: bool,
    proof: str | None,
) -> None:
    with pytest.raises(ValueError):
        TerminationReport(
            requested_at=10.0,
            finished_at=12.0,
            graceful=graceful,
            forced=forced,
            confirmed_dead=confirmed_dead,
            death_proof=support._proof() if proof else None,
            note="终止报告",
        )


def test_termination_report_rejects_reversed_or_out_of_window_time() -> None:
    with pytest.raises(ValueError):
        TerminationReport(
            requested_at=12.0,
            finished_at=10.0,
            graceful=False,
            forced=False,
            confirmed_dead=False,
            death_proof=None,
            note="时间逆序",
        )
    with pytest.raises(ValueError):
        TerminationReport(
            requested_at=10.0,
            finished_at=11.0,
            graceful=True,
            forced=False,
            confirmed_dead=True,
            death_proof=support._proof(confirmed_at=12.0),
            note="证明晚于报告",
        )


def test_termination_report_requires_bounded_nonempty_note() -> None:
    for note in ("", "  ", "x" * 4097):
        with pytest.raises(ValueError):
            TerminationReport(
                requested_at=10.0,
                finished_at=11.0,
                graceful=False,
                forced=False,
                confirmed_dead=False,
                death_proof=None,
                note=note,
            )


def test_recovery_report_accepts_only_each_states_canonical_fields() -> None:
    active = RecoveryReport(
        state=RecoveryState.ACTIVE,
        handle=support._handle(),
        death_proof=None,
        note="恢复到活动进程",
    )
    dead = RecoveryReport(
        state=RecoveryState.CONFIRMED_DEAD,
        handle=support._handle(),
        death_proof=support._proof(),
        note="已确认历史进程死亡",
    )
    never_started = RecoveryReport(
        state=RecoveryState.NEVER_STARTED,
        handle=None,
        death_proof=None,
        note="journal 尚未启动进程",
    )
    unconfirmed = RecoveryReport(
        state=RecoveryState.UNCONFIRMED,
        handle=None,
        death_proof=None,
        note="进程状态无法确认",
    )

    assert active.handle is not None
    assert dead.death_proof is not None
    assert never_started.state is RecoveryState.NEVER_STARTED
    assert unconfirmed.state is RecoveryState.UNCONFIRMED
    assert not hasattr(active, "__dict__")


@pytest.mark.parametrize(
    ("state", "has_handle", "has_proof"),
    [
        (RecoveryState.ACTIVE, False, False),
        (RecoveryState.ACTIVE, True, True),
        (RecoveryState.CONFIRMED_DEAD, False, False),
        (RecoveryState.CONFIRMED_DEAD, False, True),
        (RecoveryState.CONFIRMED_DEAD, True, False),
        (RecoveryState.NEVER_STARTED, True, False),
        (RecoveryState.NEVER_STARTED, False, True),
        (RecoveryState.UNCONFIRMED, True, False),
        (RecoveryState.UNCONFIRMED, False, True),
    ],
)
def test_recovery_report_fails_closed_on_noncanonical_field_combinations(
    state: RecoveryState,
    has_handle: bool,
    has_proof: bool,
) -> None:
    with pytest.raises(ValueError):
        RecoveryReport(
            state=state,
            handle=support._handle() if has_handle else None,
            death_proof=support._proof() if has_proof else None,
            note="非法恢复组合",
        )


def test_recovery_report_rejects_dynamic_state_or_unbounded_note() -> None:
    with pytest.raises(ValueError):
        RecoveryReport(
            state="active",  # type: ignore[arg-type]
            handle=support._handle(),
            death_proof=None,
            note="恢复到活动进程",
        )
    for note in ("", "  ", "x" * 4097):
        with pytest.raises(ValueError):
            RecoveryReport(
                state=RecoveryState.UNCONFIRMED,
                handle=None,
                death_proof=None,
                note=note,
            )
