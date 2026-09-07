"""跨域恢复竞争测试的 spawn-safe worker 与结果收集。"""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass
from pathlib import Path
from queue import Empty
from time import monotonic

from codev_platform.runtime_fencing import ControlLeaseProof
from codev_platform.runtime_fencing_store import (
    ControlLeaseStore,
    ControlLeaseStoreError,
)
from codev_platform.runtime_recovery_contract import RecoveryEnvelope
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeStorePolicy,
)
from codev_platform.runtime_transaction_controlled_store import (
    RuntimeTransactionStore,
    TransactionRecordConflictError,
)
from codev_platform.runtime_transaction_store import (
    ActiveEnvelopeConflictError,
    RuntimeTransactionBootstrapStore,
)
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalEvidence,
)
from codev_platform.runtime_transaction_contract import TransactionJournal


_RACE_TIMEOUT_SECONDS = 15.0
_BARRIER_TIMEOUT_SECONDS = 5.0


class RuntimeRecoveryRaceError(RuntimeError):
    """跨域恢复竞争没有在确定时间预算内收敛。"""


@dataclass(frozen=True, slots=True)
class TakeoverRaceInput:
    """两名接管者在子进程内重建 store 所需的无能力输入。"""

    root: str
    owner_uid: int
    current: ControlLeaseSnapshot


@dataclass(frozen=True, slots=True)
class TerminalEvidenceRaceInput:
    """两名终态证据写入者的公开输入，不携带 token 或 scope。"""

    root: str
    owner_uid: int
    evidence: TransactionTerminalEvidence


@dataclass(frozen=True, slots=True)
class TerminalCompletionRaceInput:
    """两名完整终态请求者的公开 journal/evidence/CAS 前置输入。"""

    root: str
    owner_uid: int
    evidence: TransactionTerminalEvidence
    completed: TransactionJournal
    expected_journal_sha256: str


@dataclass(frozen=True, slots=True)
class CleanupPublishRaceInput:
    """旧 tombstone 清理与新 bootstrap 发布的公开竞争输入。"""

    root: str
    owner_uid: int
    tombstone: ControlLeaseSnapshot
    next_envelope: RecoveryEnvelope


@dataclass(frozen=True, slots=True)
class RecoveryRaceResult:
    """不携带异常对象、token、scope 或文件描述符的竞争结果。"""

    operation: str
    outcome: str
    record_sha256: str | None = None
    error_type: str | None = None


def run_dual_takeover_race(
    race_input: TakeoverRaceInput,
) -> tuple[RecoveryRaceResult, RecoveryRaceResult]:
    """让两个显式授权接管者竞争同一精确 current。"""
    if type(race_input) is not TakeoverRaceInput:
        raise TypeError("race_input 必须是 TakeoverRaceInput")
    return _run_pair("takeover", race_input)


def run_terminal_evidence_race(
    race_input: TerminalEvidenceRaceInput,
) -> tuple[RecoveryRaceResult, RecoveryRaceResult]:
    """让两名 worker 竞争同一 immutable terminal evidence 发布。"""
    if type(race_input) is not TerminalEvidenceRaceInput:
        raise TypeError("race_input 必须是 TerminalEvidenceRaceInput")
    return _run_pair("terminal-evidence", race_input)


def run_terminal_completion_race(
    race_input: TerminalCompletionRaceInput,
) -> tuple[RecoveryRaceResult, RecoveryRaceResult]:
    """让两个完整终态请求竞争同一严格 journal CAS 前置。"""
    if type(race_input) is not TerminalCompletionRaceInput:
        raise TypeError("race_input 必须是 TerminalCompletionRaceInput")
    return _run_pair("terminal-completion", race_input)


def run_cleanup_publish_race(
    race_input: CleanupPublishRaceInput,
) -> tuple[RecoveryRaceResult, RecoveryRaceResult]:
    """让旧 tombstone 清理与新 attempt 发布竞争同一 active leaf。"""
    if type(race_input) is not CleanupPublishRaceInput:
        raise TypeError("race_input 必须是 CleanupPublishRaceInput")
    return _run_pair("cleanup-publish", race_input)


def _run_pair(
    operation: str,
    race_input: TakeoverRaceInput
    | TerminalEvidenceRaceInput
    | TerminalCompletionRaceInput
    | CleanupPublishRaceInput,
) -> tuple[RecoveryRaceResult, RecoveryRaceResult]:
    """以单一截止时间运行两个 spawn worker 并强制回收。"""
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    processes = tuple(
        context.Process(
            target=_run_worker,
            args=(operation, index, race_input, barrier, result_queue),
        )
        for index in range(2)
    )
    deadline = monotonic() + _RACE_TIMEOUT_SECONDS
    try:
        for process in processes:
            process.start()
        results = _collect_results(result_queue, processes, deadline)
        _require_clean_worker_exits(processes, deadline)
        return tuple(results)  # type: ignore[return-value]
    finally:
        _abort_barrier(barrier)
        _reap_processes(processes)
        result_queue.close()
        result_queue.join_thread()


def _run_worker(
    operation: str,
    index: int,
    race_input: TakeoverRaceInput
    | TerminalEvidenceRaceInput
    | TerminalCompletionRaceInput
    | CleanupPublishRaceInput,
    barrier,
    result_queue,
) -> None:
    """只在子进程重建 policy/store，随后执行一个受控公开入口。"""
    label = _worker_label(operation, index)
    try:
        policy = RuntimeStorePolicy(
            root=Path(race_input.root),
            owner_uid=race_input.owner_uid,
        )
        barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        result = _perform_operation(label, index, policy, race_input)
    except BaseException as error:
        result = _classify_failure(label, operation, error)
    result_queue.put(result)


def _perform_operation(
    label: str,
    index: int,
    policy: RuntimeStorePolicy,
    race_input: TakeoverRaceInput
    | TerminalEvidenceRaceInput
    | TerminalCompletionRaceInput
    | CleanupPublishRaceInput,
) -> RecoveryRaceResult:
    """按固定 operation 调用真实 store，不传递父进程能力。"""
    if type(race_input) is TakeoverRaceInput:
        snapshot, _proof = ControlLeaseStore(policy).take_over(
            race_input.current,
            owner=f"recovery-{index}",
            token=bytes(range(32 + index, 64 + index)),
            issued_at=f"2026-07-21T00:00:0{index + 2}Z",
        )
        return RecoveryRaceResult(
            operation=label,
            outcome="success",
            record_sha256=snapshot.sha256,
        )
    if type(race_input) is TerminalEvidenceRaceInput:
        proof = _initial_fixture_proof(race_input.evidence.attempt_id)
        RuntimeTransactionStore(policy, ControlLeaseStore(policy)).write_terminal_evidence_once(
            race_input.evidence,
            proof=proof,
        )
        return RecoveryRaceResult(operation=label, outcome="success")
    if type(race_input) is TerminalCompletionRaceInput:
        proof = _initial_fixture_proof(race_input.evidence.attempt_id)
        store = RuntimeTransactionStore(policy, ControlLeaseStore(policy))
        store.write_terminal_evidence_once(race_input.evidence, proof=proof)
        store.write_journal(
            race_input.completed,
            proof=proof,
            expected_sha256=race_input.expected_journal_sha256,
        )
        return RecoveryRaceResult(operation=label, outcome="success")
    if type(race_input) is CleanupPublishRaceInput:
        if index == 0:
            RuntimeTransactionStore(
                policy,
                ControlLeaseStore(policy),
            ).clear_terminal_envelope_if_current_tombstone(race_input.tombstone)
        else:
            RuntimeTransactionBootstrapStore(policy).publish_active_envelope_if_absent(
                race_input.next_envelope,
            )
        return RecoveryRaceResult(operation=label, outcome="success")
    raise RuntimeRecoveryRaceError("竞争输入类型无效")


def _initial_fixture_proof(attempt_id: str) -> ControlLeaseProof:
    """只在 child 内重建固定夹具 token，父进程绝不传递原始 proof。"""
    return ControlLeaseProof(
        attempt_id=attempt_id,
        epoch=1,
        token=bytes(range(32)),
    )


def _worker_label(operation: str, index: int) -> str:
    """生成不会泄露输入内容的稳定 worker 标签。"""
    if operation == "takeover":
        return f"takeover-{index}"
    if operation == "terminal-evidence":
        return f"terminal-evidence-{index}"
    if operation == "terminal-completion":
        return f"terminal-completion-{index}"
    if operation == "cleanup-publish":
        return "cleanup" if index == 0 else "publish"
    raise RuntimeRecoveryRaceError("竞争 operation 无效")


def _classify_failure(
    label: str,
    operation: str,
    error: BaseException,
) -> RecoveryRaceResult:
    """只把已知 CAS 或旧 envelope 冲突标为预期，其他错误不得吞没。"""
    if operation == "takeover" and isinstance(error, ControlLeaseStoreError):
        if any(fragment in str(error) for fragment in ("CAS 已变化", "只能接管活动")):
            return RecoveryRaceResult(operation=label, outcome="expected_conflict")
    if operation == "cleanup-publish" and isinstance(error, ActiveEnvelopeConflictError):
        return RecoveryRaceResult(operation=label, outcome="expected_conflict")
    if operation == "terminal-completion" and isinstance(
        error,
        TransactionRecordConflictError,
    ):
        if "前置摘要" in str(error):
            return RecoveryRaceResult(operation=label, outcome="expected_conflict")
    return RecoveryRaceResult(
        operation=label,
        outcome="unexpected",
        error_type=type(error).__name__,
    )


def _collect_results(result_queue, processes, deadline: float) -> list[RecoveryRaceResult]:
    """在同一总截止时间内收集每名 worker 的单个结构化结果。"""
    results: list[RecoveryRaceResult] = []
    while len(results) < len(processes):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeRecoveryRaceError("竞争 worker 结果收集超时")
        try:
            result = result_queue.get(timeout=remaining)
        except Empty as error:
            raise RuntimeRecoveryRaceError("竞争 worker 未返回结果") from error
        if type(result) is not RecoveryRaceResult:
            raise RuntimeRecoveryRaceError("竞争 worker 返回类型无效")
        results.append(result)
    return results


def _require_clean_worker_exits(processes, deadline: float) -> None:
    """每个 worker 都必须在统一截止时间内正常退出。"""
    for process in processes:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeRecoveryRaceError("竞争 worker 退出超时")
        process.join(timeout=remaining)
        if process.is_alive():
            raise RuntimeRecoveryRaceError("竞争 worker 未退出")
        if process.exitcode != 0:
            raise RuntimeRecoveryRaceError("竞争 worker 非零退出")


def _abort_barrier(barrier) -> None:
    """异常路径解除仍在等待的 worker。"""
    try:
        barrier.abort()
    except BaseException:
        pass


def _reap_processes(processes) -> None:
    """finally 中强制收割所有子进程，避免测试污染后续批次。"""
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=_BARRIER_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            process.join(timeout=_BARRIER_TIMEOUT_SECONDS)


__all__ = [
    "CleanupPublishRaceInput",
    "RecoveryRaceResult",
    "RuntimeRecoveryRaceError",
    "TakeoverRaceInput",
    "TerminalCompletionRaceInput",
    "TerminalEvidenceRaceInput",
    "run_cleanup_publish_race",
    "run_dual_takeover_race",
    "run_terminal_evidence_race",
    "run_terminal_completion_race",
]
