"""跨域恢复顺序的公开 store 组合回归。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from codev_platform.runtime_fencing_store import (
    ControlLeaseStoreError,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryEnvelope,
    create_recovery_envelope,
    encode_recovery_envelope,
)
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_terminal_evidence_path,
    control_lease_history_path,
)
from codev_platform.runtime_store_protocols import RuntimeStorePolicy
from codev_platform.runtime_transaction_contract import (
    TransactionRecoveryDirective,
    create_transaction_journal,
    transaction_journal_sha256,
    transaction_recovery_directive,
)
from codev_platform.runtime_transaction_store import RuntimeTransactionBootstrapStore
from tests.runtime_transaction_controlled_store_support import (
    build_terminal_store_input,
)
from tests.runtime_transaction_terminal_support import _reservation
from tests.runtime_recovery_race_support import (
    CleanupPublishRaceInput,
    TakeoverRaceInput,
    TerminalCompletionRaceInput,
    TerminalEvidenceRaceInput,
    run_cleanup_publish_race,
    run_dual_takeover_race,
    run_terminal_completion_race,
    run_terminal_evidence_race,
)


_POSIX = os.name == "posix"


def _complete_and_retire(tmp_path: Path):
    """将真实 transaction 推进到 completed、retired 且保留旧 envelope。"""
    store, gate, root, active, completed, evidence, proof = build_terminal_store_input(
        tmp_path,
    )
    store.write_terminal_evidence_once(evidence, proof=proof)
    store.write_journal(
        completed,
        proof=proof,
        expected_sha256=transaction_journal_sha256(active),
    )
    retired = gate.retire_current(
        proof,
        completed,
        evidence,
        retired_at="2026-07-21T00:00:04Z",
    )
    return store, gate, root, active, completed, evidence, retired


def _prepare_next_attempt(root: Path) -> tuple[str, RecoveryEnvelope]:
    """为不同 attempt 预封存完整 bootstrap，但不抢占旧 active leaf。"""
    reservation = replace(
        _reservation(),
        attempt_id="b" * 32,
        plan_sha256="b" * 64,
        created_at="2026-07-21T00:00:05Z",
    )
    journal = create_transaction_journal(
        reservation,
        created_at=reservation.created_at,
    )
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-postlease",
        created_at=reservation.created_at,
    )
    bootstrap = RuntimeTransactionBootstrapStore(
        RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
    )
    bootstrap.reserve_attempt(reservation)
    bootstrap.write_journal_genesis_once(journal)
    bootstrap.write_envelope_once(envelope)
    return reservation.attempt_id, envelope


def _publish_next_attempt(root: Path) -> str:
    """为已清理旧 envelope 的不同 attempt 发布完整 bootstrap。"""
    attempt_id, envelope = _prepare_next_attempt(root)
    RuntimeTransactionBootstrapStore(
        RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
    ).publish_active_envelope_if_absent(envelope)
    return attempt_id


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活动租约显式接管后按持久journal指令继续恢复(tmp_path: Path) -> None:
    """root recovery 只显式授权接管，指令始终从严格 journal 读取。"""
    store, gate, _root, active, _completed, _evidence, _proof = build_terminal_store_input(
        tmp_path,
    )
    current = gate.load_current()

    assert current is not None
    successor, successor_proof = gate.take_over(
        current,
        owner="root-recovery",
        token=bytes(range(32, 64)),
        issued_at="2026-07-21T00:00:02Z",
    )

    assert successor.record.attempt_id == active.attempt_id
    assert (
        transaction_recovery_directive(
            store.load_journal(active.attempt_id),
        )
        is TransactionRecoveryDirective.ADVANCE
    )
    with gate.mutation(successor_proof) as scope:
        assert scope.snapshot == successor


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test完成终态由接管者复验后退休(tmp_path: Path) -> None:
    """completed journal/evidence 不能跳过新 lease 的谱系复验直接清理。"""
    store, gate, _root, active, completed, evidence, proof = build_terminal_store_input(
        tmp_path,
    )
    store.write_terminal_evidence_once(evidence, proof=proof)
    store.write_journal(
        completed,
        proof=proof,
        expected_sha256=transaction_journal_sha256(active),
    )
    current = gate.load_current()

    assert current is not None
    successor, successor_proof = gate.take_over(
        current,
        owner="terminal-recovery",
        token=bytes(range(32, 64)),
        issued_at="2026-07-21T00:00:02Z",
    )
    retired = gate.retire_current(
        successor_proof,
        completed,
        evidence,
        retired_at="2026-07-21T00:00:04Z",
    )

    assert retired.record.retired_from_sha256 == successor.sha256
    assert (
        transaction_recovery_directive(
            store.load_journal(active.attempt_id),
        )
        is TransactionRecoveryDirective.TERMINAL
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test退休清理旧envelope后才允许不同attempt签发initial(tmp_path: Path) -> None:
    """旧 tombstone 只清自己的 envelope，新的 initial 不得抢占旧身份。"""
    store, gate, root, _active, _completed, _evidence, retired = _complete_and_retire(
        tmp_path,
    )

    store.clear_terminal_envelope_if_current_tombstone(retired)
    assert not active_recovery_envelope_path(root).exists()
    next_attempt_id = _publish_next_attempt(root)
    successor, _proof = gate.acquire_initial(
        owner="controller-next",
        token=b"n" * 32,
        issued_at="2026-07-21T00:00:06Z",
    )

    assert successor.record.attempt_id == next_attempt_id
    assert successor.record.epoch == 1


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize("mutation", ("删除", "绑定漂移"))
def test活动租约缺失或漂移envelope时恢复闭锁(
    tmp_path: Path,
    mutation: str,
) -> None:
    """没有可信活动 bootstrap 时绝不自动开启或接管 attempt。"""
    store, gate, root, _active, _completed, _evidence, _proof = build_terminal_store_input(
        tmp_path,
    )
    current = gate.load_current()
    path = active_recovery_envelope_path(root)

    assert current is not None
    if mutation == "删除":
        path.unlink()
    else:
        active = RuntimeTransactionBootstrapStore(
            RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
        ).load_active_envelope()
        path.write_bytes(
            encode_recovery_envelope(
                replace(active.value, interpreter_sha256="e" * 64),
            )
        )

    with pytest.raises(ControlLeaseStoreError, match="bootstrap|活动|context"):
        gate.take_over(
            current,
            owner="unexpected-recovery",
            token=bytes(range(32, 64)),
            issued_at="2026-07-21T00:00:02Z",
        )
    with pytest.raises(ControlLeaseStoreError, match="bootstrap|活动|context"):
        gate.acquire_initial(
            owner="unexpected-initial",
            token=b"i" * 32,
            issued_at="2026-07-21T00:00:02Z",
        )

    assert gate.load_current() == current


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test两个接管者竞争时只冻结一个赢家history(tmp_path: Path) -> None:
    """两个 root recovery 授权不能让同一 current 派生两个 successor。"""
    _store, gate, root, _active, _completed, _evidence, _proof = build_terminal_store_input(
        tmp_path
    )
    current = gate.load_current()

    assert current is not None
    results = run_dual_takeover_race(
        TakeoverRaceInput(
            root=str(root),
            owner_uid=os.geteuid(),
            current=current,
        ),
    )

    assert [result.outcome for result in results].count("success") == 1
    assert [result.outcome for result in results].count("expected_conflict") == 1
    winner = next(result for result in results if result.outcome == "success")
    assert winner.record_sha256 is not None
    assert gate.load_current().sha256 == winner.record_sha256
    history = control_lease_history_path(
        root,
        current.record.attempt_id,
        current.sha256,
    ).parent
    assert {path.stem for path in history.glob("*.json")} == {
        current.sha256,
        winner.record_sha256,
    }


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test两个终态请求只发布同一份immutable_evidence(tmp_path: Path) -> None:
    """并行相同终态请求只能按相同规范字节幂等收敛。"""
    store, _gate, root, active, _completed, evidence, _proof = build_terminal_store_input(tmp_path)

    results = run_terminal_evidence_race(
        TerminalEvidenceRaceInput(
            root=str(root),
            owner_uid=os.geteuid(),
            evidence=evidence,
        ),
    )

    assert {result.outcome for result in results} == {"success"}
    assert store.load_terminal_evidence(active.attempt_id) == evidence
    assert attempt_terminal_evidence_path(root, active.attempt_id).is_file()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test两个完整终态请求只允许一个旧journal摘要推进(tmp_path: Path) -> None:
    """同一 active journal 的 CAS 前置只能被一个 completed 请求消费。"""
    store, _gate, root, active, completed, evidence, _proof = build_terminal_store_input(tmp_path)

    results = run_terminal_completion_race(
        TerminalCompletionRaceInput(
            root=str(root),
            owner_uid=os.geteuid(),
            evidence=evidence,
            completed=completed,
            expected_journal_sha256=transaction_journal_sha256(active),
        ),
    )

    assert [result.outcome for result in results].count("success") == 1
    assert [result.outcome for result in results].count("expected_conflict") == 1
    assert store.load_journal(active.attempt_id) == completed
    assert store.load_terminal_evidence(active.attempt_id) == evidence


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test旧tombstone清理与新active发布竞争不覆盖旧身份(tmp_path: Path) -> None:
    """同一部署锁只允许先清理再发布，或冲突后显式重试发布。"""
    store, _gate, root, _active, _completed, _evidence, retired = _complete_and_retire(
        tmp_path,
    )
    next_attempt_id, next_envelope = _prepare_next_attempt(root)
    old_payload = active_recovery_envelope_path(root).read_bytes()

    results = run_cleanup_publish_race(
        CleanupPublishRaceInput(
            root=str(root),
            owner_uid=os.geteuid(),
            tombstone=retired,
            next_envelope=next_envelope,
        ),
    )

    cleanup = next(result for result in results if result.operation == "cleanup")
    publish = next(result for result in results if result.operation == "publish")
    assert cleanup.outcome == "success"
    if publish.outcome == "expected_conflict":
        RuntimeTransactionBootstrapStore(
            RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
        ).publish_active_envelope_if_absent(next_envelope)
    else:
        assert publish.outcome == "success"
    active_payload = active_recovery_envelope_path(root).read_bytes()

    assert active_payload != old_payload
    assert active_payload == encode_recovery_envelope(next_envelope)
    assert store.load_journal(next_attempt_id).attempt_id == next_attempt_id
