"""control lease 跨进程竞争测试的可 pickle worker 与强制回收辅助。"""

from __future__ import annotations

import multiprocessing
import os
from dataclasses import dataclass
from pathlib import Path
from queue import Empty
from time import monotonic

from codev_platform.runtime_fencing import ControlLeaseProof
from codev_platform.runtime_control_lease_persistence import ControlLeasePersistence
from codev_platform.runtime_fencing_store import ControlLeaseStore, ControlLeaseStoreError
from codev_platform.runtime_root_binding import RuntimeRootBindingError
from codev_platform.runtime_store_protocols import ControlLeaseSnapshot, RuntimeStorePolicy
from codev_platform.runtime_transaction_contract import TransactionJournal
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalEvidence,
)


_RACE_TIMEOUT_SECONDS = 15.0
_BARRIER_TIMEOUT_SECONDS = 5.0
_INITIAL_TEST_TOKEN = bytes(range(32))


class RuntimeStoreRaceError(RuntimeError):
    """跨进程 control lease 竞争未在确定时间预算内得到可信结果。"""


@dataclass(frozen=True, slots=True)
class ControlLeaseRaceInput:
    """子进程自行重建 store 所需的不可变输入；不传递能力或文件描述符。"""

    root: str
    owner_uid: int
    current: ControlLeaseSnapshot
    completed: TransactionJournal
    evidence: TransactionTerminalEvidence


@dataclass(frozen=True, slots=True)
class InitialLeaseRootSwapRaceInput:
    """两名初始控制器在根替换前各自建立信任锚所需的无秘密输入。"""

    root: str
    replacement: str
    owner_uid: int


@dataclass(frozen=True, slots=True)
class ControlLeaseTransitionCrashInput:
    """真实子进程在指定持久化边界退出所需的无能力输入。"""

    root: str
    owner_uid: int
    current: ControlLeaseSnapshot | None
    crash_stage: str
    operation: str = "takeover"
    completed: TransactionJournal | None = None
    evidence: TransactionTerminalEvidence | None = None


@dataclass(frozen=True, slots=True)
class ControlLeaseTransitionRootSwapInput:
    """接管 pending 已创建后替换可见根所需的无能力输入。"""

    root: str
    replacement: str
    owner_uid: int
    current: ControlLeaseSnapshot


@dataclass(frozen=True, slots=True)
class ControlLeaseRaceResult:
    """不携带 token 或异常对象的结构化竞争结果。"""

    operation: str
    outcome: str
    record_sha256: str | None = None
    error_type: str | None = None


def run_takeover_retire_race(
    race_input: ControlLeaseRaceInput,
) -> tuple[ControlLeaseRaceResult, ControlLeaseRaceResult]:
    """让独立 spawn 进程竞争同一 initial 的接管与退休 CAS。"""
    if type(race_input) is not ControlLeaseRaceInput:
        raise TypeError("race_input 必须是 ControlLeaseRaceInput")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    processes = (
        context.Process(
            target=_run_worker,
            args=("takeover", race_input, barrier, result_queue),
        ),
        context.Process(
            target=_run_worker,
            args=("retire", race_input, barrier, result_queue),
        ),
    )
    results: list[ControlLeaseRaceResult] = []
    deadline = monotonic() + _RACE_TIMEOUT_SECONDS
    try:
        for process in processes:
            process.start()
        results.extend(_collect_results(result_queue, processes, deadline))
        _require_clean_worker_exits(processes, deadline)
        return tuple(results)  # type: ignore[return-value]
    finally:
        _abort_barrier(barrier)
        _reap_processes(processes)
        result_queue.close()
        result_queue.join_thread()


def run_control_lease_transition_crash(
    crash_input: ControlLeaseTransitionCrashInput,
) -> None:
    """让独立 spawn 子进程在指定 transition 持久化边界后退出。"""
    if type(crash_input) is not ControlLeaseTransitionCrashInput:
        raise TypeError("crash_input 必须是 ControlLeaseTransitionCrashInput")
    if crash_input.crash_stage not in {"after_prepare", "after_freeze", "after_cas"}:
        raise ValueError("crash_stage 必须是 after_prepare、after_freeze 或 after_cas")
    if crash_input.operation not in {"initial", "takeover", "retire"}:
        raise ValueError("operation 必须是 initial、takeover 或 retire")
    if crash_input.operation in {"takeover", "retire"} and crash_input.current is None:
        raise ValueError("takeover/retire 必须提供 current")
    if crash_input.operation == "retire" and (
        crash_input.completed is None or crash_input.evidence is None
    ):
        raise ValueError("retire 必须提供 completed 与 evidence")
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_run_control_lease_transition_crash_worker,
        args=(crash_input,),
    )
    deadline = monotonic() + _RACE_TIMEOUT_SECONDS
    try:
        process.start()
        _require_clean_worker_exits((process,), deadline)
    finally:
        _reap_processes((process,))


def run_takeover_transition_crash(
    crash_input: ControlLeaseTransitionCrashInput,
) -> None:
    """兼容既有接管崩溃测试；新用例应使用通用 transition runner。"""
    if crash_input.operation != "takeover":
        raise ValueError("兼容接管 runner 只接受 takeover 输入")
    run_control_lease_transition_crash(crash_input)


def run_takeover_transition_root_swap(
    race_input: ControlLeaseTransitionRootSwapInput,
) -> ControlLeaseRaceResult:
    """在接管 history 已冻结且 pending 存在时替换根，旧绑定必须闭锁。"""
    if type(race_input) is not ControlLeaseTransitionRootSwapInput:
        raise TypeError("race_input 必须是 ControlLeaseTransitionRootSwapInput")
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    result_queue = context.Queue()
    process = context.Process(
        target=_run_takeover_transition_root_swap_worker,
        args=(race_input, ready, release, result_queue),
    )
    deadline = monotonic() + _RACE_TIMEOUT_SECONDS
    try:
        process.start()
        _wait_for_event(ready, deadline)
        _replace_visible_root(Path(race_input.root), Path(race_input.replacement))
        release.set()
        result = _collect_results(result_queue, (process,), deadline)[0]
        _require_clean_worker_exits((process,), deadline)
        return result
    finally:
        release.set()
        _reap_processes((process,))
        result_queue.close()
        result_queue.join_thread()


def run_initial_root_swap_race(
    race_input: InitialLeaseRootSwapRaceInput,
) -> tuple[ControlLeaseRaceResult, ControlLeaseRaceResult]:
    """让两个已建立根信任锚的初始控制器在可见根替换后同时闭锁。"""
    if type(race_input) is not InitialLeaseRootSwapRaceInput:
        raise TypeError("race_input 必须是 InitialLeaseRootSwapRaceInput")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(3)
    release = context.Event()
    result_queue = context.Queue()
    processes = tuple(
        context.Process(
            target=_run_initial_root_swap_worker,
            args=(index, race_input, barrier, release, result_queue),
        )
        for index in range(2)
    )
    deadline = monotonic() + _RACE_TIMEOUT_SECONDS
    try:
        for process in processes:
            process.start()
        _wait_at_barrier(barrier, deadline)
        _replace_visible_root(Path(race_input.root), Path(race_input.replacement))
        release.set()
        results = _collect_results(result_queue, processes, deadline)
        _require_clean_worker_exits(processes, deadline)
        return tuple(results)  # type: ignore[return-value]
    finally:
        release.set()
        _abort_barrier(barrier)
        _reap_processes(processes)
        result_queue.close()
        result_queue.join_thread()


def _run_worker(
    operation: str,
    race_input: ControlLeaseRaceInput,
    barrier,
    result_queue,
) -> None:
    """在子进程内重建 policy/store，绝不接收父进程的根能力或 store。"""
    try:
        policy = RuntimeStorePolicy(
            root=Path(race_input.root),
            owner_uid=race_input.owner_uid,
        )
        store = ControlLeaseStore(policy)
        barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        snapshot = _perform_operation(operation, store, race_input)
        result = ControlLeaseRaceResult(
            operation=operation,
            outcome="success",
            record_sha256=snapshot.sha256,
        )
    except ControlLeaseStoreError as error:
        result = _conflict_or_unexpected(operation, error)
    except BaseException as error:
        result = ControlLeaseRaceResult(
            operation=operation,
            outcome="unexpected",
            error_type=type(error).__name__,
        )
    result_queue.put(result)


def _run_control_lease_transition_crash_worker(
    crash_input: ControlLeaseTransitionCrashInput,
) -> None:
    """在真实持久层边界后退出，不向父进程传递能力或 store。"""
    policy = RuntimeStorePolicy(
        root=Path(crash_input.root),
        owner_uid=crash_input.owner_uid,
    )
    store = ControlLeaseStore(policy)
    if crash_input.crash_stage == "after_prepare":
        original = ControlLeasePersistence.prepare_pending_transition

        def crash_after_prepare(self, root, intent):
            original(self, root, intent)
            os._exit(0)

        ControlLeasePersistence.prepare_pending_transition = crash_after_prepare
    elif crash_input.crash_stage == "after_freeze":
        original = ControlLeasePersistence.freeze_history

        def crash_after_freeze(self, root, record):
            original(self, root, record)
            os._exit(0)

        ControlLeasePersistence.freeze_history = crash_after_freeze
    else:
        original = ControlLeasePersistence.compare_and_swap_current

        def crash_after_cas(self, root, expected, next_record):
            original(self, root, expected, next_record)
            os._exit(0)

        ControlLeasePersistence.compare_and_swap_current = crash_after_cas
    _perform_transition_crash_operation(store, crash_input)
    raise RuntimeStoreRaceError("transition 崩溃 worker 未在指定边界退出")


def _run_takeover_transition_root_swap_worker(
    race_input: ControlLeaseTransitionRootSwapInput,
    ready,
    release,
    result_queue,
) -> None:
    """只在 child 内暂停真实 freeze 边界，根替换后继续并回传无秘密结果。"""
    try:
        policy = RuntimeStorePolicy(
            root=Path(race_input.root),
            owner_uid=race_input.owner_uid,
        )
        store = ControlLeaseStore(policy)
        original = ControlLeasePersistence.freeze_history

        def pause_after_freeze(self, root, record):
            snapshot = original(self, root, record)
            ready.set()
            if not release.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
                raise RuntimeStoreRaceError("根替换 worker 未收到释放信号")
            return snapshot

        ControlLeasePersistence.freeze_history = pause_after_freeze
        store.take_over(
            race_input.current,
            owner="root-swap-transition",
            token=bytes(range(32, 64)),
            issued_at="2026-07-21T00:00:02Z",
        )
        result = ControlLeaseRaceResult(
            operation="takeover-root-swap",
            outcome="unexpected_success",
        )
    except BaseException as error:
        result = _root_swap_failure_result("takeover-root-swap", error)
    result_queue.put(result)


def _perform_transition_crash_operation(
    store: ControlLeaseStore,
    crash_input: ControlLeaseTransitionCrashInput,
) -> None:
    """只在子进程内部重建固定测试 capability，父进程不传递 secret。"""
    if crash_input.operation == "initial":
        store.acquire_initial(
            owner="transition-initial",
            token=bytes(range(64, 96)),
            issued_at="2026-07-21T00:00:04Z",
        )
        return
    if crash_input.operation == "takeover":
        if crash_input.current is None:
            raise RuntimeStoreRaceError("takeover crash 输入缺少 current")
        store.take_over(
            crash_input.current,
            owner="transition-recovery",
            token=bytes(range(32, 64)),
            issued_at="2026-07-21T00:00:02Z",
        )
        return
    if crash_input.current is None or crash_input.completed is None or crash_input.evidence is None:
        raise RuntimeStoreRaceError("retire crash 输入缺少持久终态")
    proof = _rebuild_initial_fixture_proof(crash_input.current)
    store.retire_current(
        proof,
        crash_input.completed,
        crash_input.evidence,
        retired_at="2026-07-21T00:00:03Z",
    )


def _run_initial_root_swap_worker(
    index: int,
    race_input: InitialLeaseRootSwapRaceInput,
    barrier,
    release,
    result_queue,
) -> None:
    """先锚定旧根，再等待父进程替换可见根后尝试初始签发。"""
    operation = f"initial-{index}"
    try:
        policy = RuntimeStorePolicy(
            root=Path(race_input.root),
            owner_uid=race_input.owner_uid,
        )
        store = ControlLeaseStore(policy)
        with policy.root_binding.bind():
            barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
            if not release.wait(timeout=_BARRIER_TIMEOUT_SECONDS):
                raise RuntimeStoreRaceError("根替换竞争未收到释放信号")
            store.acquire_initial(
                owner=f"root-swap-{index}",
                token=bytes([index + 1]) * 32,
                issued_at="2026-07-21T00:00:01Z",
            )
        result = ControlLeaseRaceResult(operation=operation, outcome="success")
    except BaseException as error:
        result = _root_swap_failure_result(operation, error)
    result_queue.put(result)


def _perform_operation(
    operation: str,
    store: ControlLeaseStore,
    race_input: ControlLeaseRaceInput,
) -> ControlLeaseSnapshot:
    if operation == "takeover":
        snapshot, _proof = store.take_over(
            race_input.current,
            owner="race-recovery",
            token=bytes(range(32, 64)),
            issued_at="2026-07-21T00:00:02Z",
        )
        return snapshot
    if operation == "retire":
        return store.retire_current(
            _rebuild_initial_fixture_proof(race_input.current),
            race_input.completed,
            race_input.evidence,
            retired_at="2026-07-21T00:00:03Z",
        )
    raise RuntimeStoreRaceError("竞争操作类型无效")


def _conflict_or_unexpected(
    operation: str,
    error: ControlLeaseStoreError,
) -> ControlLeaseRaceResult:
    expected_fragments = (
        "CAS 已变化",
        "control lease proof 无效",
        "只能接管活动 control lease",
        "当前 control lease 不是 tombstone",
    )
    if any(fragment in str(error) for fragment in expected_fragments):
        return ControlLeaseRaceResult(operation=operation, outcome="expected_conflict")
    return ControlLeaseRaceResult(
        operation=operation,
        outcome="unexpected",
        error_type=type(error).__name__,
    )


def _rebuild_initial_fixture_proof(
    current: ControlLeaseSnapshot,
) -> ControlLeaseProof:
    """仅在子进程内由固定公开夹具重建 initial lease 的测试能力。"""
    return ControlLeaseProof(
        attempt_id=current.record.attempt_id,
        epoch=current.record.epoch,
        token=_INITIAL_TEST_TOKEN,
    )


def _root_swap_failure_result(
    operation: str,
    error: BaseException,
) -> ControlLeaseRaceResult:
    """仅将明确根身份漂移归类为预期闭锁，其他异常必须暴露。"""
    if _is_expected_root_drift(error):
        return ControlLeaseRaceResult(operation=operation, outcome="expected_root_drift")
    return ControlLeaseRaceResult(
        operation=operation,
        outcome="unexpected",
        error_type=type(error).__name__,
    )


def _is_expected_root_drift(error: BaseException) -> bool:
    """沿异常因果链查找根绑定错误或明确的根身份/可见路径漂移。"""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, RuntimeRootBindingError):
            return True
        message = str(current)
        if "已漂移" in message and ("运行时根目录身份" in message or "可见路径" in message):
            return True
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)
    return False


def _require_clean_worker_exits(processes, deadline: float) -> None:
    """在总截止时间内确认 worker 已退出且未被异常终止。"""
    for process in processes:
        remaining = max(0.0, deadline - monotonic())
        process.join(timeout=remaining)
        if process.is_alive():
            raise RuntimeStoreRaceError("跨进程竞争 worker 未在截止时间内退出")
        if process.exitcode != 0:
            raise RuntimeStoreRaceError("跨进程竞争 worker 异常退出")


def _collect_results(
    result_queue,
    processes,
    deadline: float,
) -> tuple[ControlLeaseRaceResult, ...]:
    results: list[ControlLeaseRaceResult] = []
    while len(results) < len(processes):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise RuntimeStoreRaceError("跨进程竞争等待结果超过时间预算")
        try:
            result = result_queue.get(timeout=remaining)
        except Empty as error:
            raise RuntimeStoreRaceError("跨进程竞争未返回完整结果") from error
        if type(result) is not ControlLeaseRaceResult:
            raise RuntimeStoreRaceError("跨进程竞争返回结果类型无效")
        results.append(result)
    return tuple(results)


def _wait_at_barrier(barrier, deadline: float) -> None:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise RuntimeStoreRaceError("根替换竞争未在时间预算内准备完成")
    try:
        barrier.wait(timeout=min(_BARRIER_TIMEOUT_SECONDS, remaining))
    except BaseException as error:
        raise RuntimeStoreRaceError("根替换竞争 worker 未完成信任锚建立") from error


def _wait_for_event(event, deadline: float) -> None:
    """以统一截止时间等待 worker 到达真实持久层暂停边界。"""
    remaining = deadline - monotonic()
    if remaining <= 0 or not event.wait(timeout=remaining):
        raise RuntimeStoreRaceError("根替换 worker 未到达指定持久化边界")


def _replace_visible_root(root: Path, replacement: Path) -> None:
    displaced = root.with_name(f"{root.name}-displaced")
    if displaced.exists():
        raise RuntimeStoreRaceError("根替换竞争存在未清理的旧根目录")
    try:
        root.rename(displaced)
        replacement.rename(root)
    except OSError as error:
        raise RuntimeStoreRaceError("根替换竞争无法原子替换可见根") from error


def _abort_barrier(barrier) -> None:
    try:
        barrier.abort()
    except BaseException:
        return None


def _reap_processes(processes) -> None:
    """无论断言、超时或 worker 异常均回收子进程，避免残留僵尸。"""
    for process in processes:
        process.join(timeout=0.2)
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=0.5)
    for process in processes:
        if process.is_alive():
            process.kill()
    for process in processes:
        process.join(timeout=0.5)


__all__ = [
    "ControlLeaseRaceInput",
    "ControlLeaseRaceResult",
    "ControlLeaseTransitionCrashInput",
    "ControlLeaseTransitionRootSwapInput",
    "InitialLeaseRootSwapRaceInput",
    "RuntimeStoreRaceError",
    "run_control_lease_transition_crash",
    "run_initial_root_swap_race",
    "run_takeover_transition_crash",
    "run_takeover_retire_race",
    "run_takeover_transition_root_swap",
]
