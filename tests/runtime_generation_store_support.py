"""generation store 测试的真实 bootstrap、lease 与不可变对象夹具。"""

from __future__ import annotations

import os
import multiprocessing
from dataclasses import dataclass
from pathlib import Path
from queue import Empty
from time import monotonic

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
    freeze_deployment_attempt,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ServingFenceRecord,
    control_lease_record_sha256,
    issue_serving_fence,
)
from codev_platform.runtime_fencing_store import ControlLeaseStore
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
    serving_binding_sha256,
)
from codev_platform.runtime_generation_contract import (
    GenerationKind,
    RuntimeGeneration,
    create_runtime_generation,
)
from codev_platform.runtime_generation_store import (
    RuntimeGenerationStore,
    RuntimeGenerationStoreError,
)
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    begin_switch,
    begin_validation,
    commit_serving,
    encode_generation_state,
    prepare_serving_publication,
)
from codev_platform._runtime_generation_store_validation import (
    GenerationStateConflictError,
)
from codev_platform.runtime_managed_file import write_managed_bytes_atomic_at
from codev_platform.runtime_recovery_contract import create_recovery_envelope
from codev_platform.runtime_serving_permit import (
    ServingPermitRecord,
    create_serving_permit,
)
from codev_platform.runtime_storage import generation_state_path
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeStorePolicy,
    private_managed_file_policy,
)
from codev_platform.runtime_transaction_controlled_store import RuntimeTransactionStore
from codev_platform.runtime_transaction_contract import create_transaction_journal
from codev_platform.runtime_transaction_store import RuntimeTransactionBootstrapStore


_RACE_TIMEOUT_SECONDS = 15.0
_BARRIER_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class GenerationStoreInput:
    """一条真实活动 control 链及其目标 generation。"""

    store: RuntimeGenerationStore
    gate: ControlLeaseStore
    policy: RuntimeStorePolicy
    root: Path
    generation: RuntimeGeneration
    attempt: DeploymentAttempt
    fence: ServingFenceRecord
    acceptance: GenerationAcceptance
    control_snapshot: ControlLeaseSnapshot
    proof: ControlLeaseProof


@dataclass(frozen=True, slots=True)
class ServingPublicationInput:
    """已存在维护态 A，并预先计算公开 B 与其 staged permit。"""

    base: GenerationStoreInput
    staged_state: GenerationState
    public_state: GenerationState
    permit: ServingPermitRecord


@dataclass(frozen=True, slots=True)
class StateTransitionInput:
    """由既有纯状态机计算的完整合法转换链。"""

    base: GenerationStoreInput
    baseline_state: GenerationState
    switching_state: GenerationState
    validating_state: GenerationState
    staged_state: GenerationState
    public_state: GenerationState
    permit: ServingPermitRecord


@dataclass(frozen=True, slots=True)
class GenerationStateRaceInput:
    """spawn worker 重建 state CAS 所需的无文件描述符输入。"""

    root: str
    owner_uid: int
    expected_sha256: str
    desired: GenerationState
    proof: ControlLeaseProof


@dataclass(frozen=True, slots=True)
class GenerationStateRaceResult:
    """跨进程 CAS 的无异常对象结果。"""

    outcome: str
    state_sha256: str | None = None
    error_type: str | None = None


class GenerationStateRaceError(RuntimeError):
    """generation state spawn 竞争未在固定预算内收敛。"""


def build_generation_store_input(tmp_path: Path) -> GenerationStoreInput:
    """建立已冻结 attempt、已持久 bootstrap 与当前 control capability。"""
    root = tmp_path / "runtime"
    root.mkdir()
    generation = _generation()
    reservation = _reservation()
    attempt = freeze_deployment_attempt(
        reservation,
        target_generation_id=generation.generation_id,
        baseline_generation_id="d" * 64,
        baseline_observation_sha256="e" * 64,
    )
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    bootstrap = RuntimeTransactionBootstrapStore(policy)
    journal = create_transaction_journal(
        reservation,
        created_at="2026-07-21T10:00:01Z",
    )
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="f" * 64,
        transaction_store_id="runtime-generation-store",
        created_at="2026-07-21T10:00:02Z",
    )
    bootstrap.reserve_attempt(reservation)
    bootstrap.write_journal_genesis_once(journal)
    bootstrap.write_envelope_once(envelope)
    bootstrap.publish_active_envelope_if_absent(envelope)
    gate = ControlLeaseStore(policy)
    control_snapshot, proof = gate.acquire_initial(
        owner="generation-controller",
        token=bytes(range(1, 33)),
        issued_at="2026-07-21T10:00:03Z",
    )
    transaction_store = RuntimeTransactionStore(policy, gate)
    transaction_store.write_attempt_once(attempt, proof=proof)
    fence, _fence_proof = issue_serving_fence(
        attempt,
        fence_id="generation-store-fence",
        epoch=2,
        token=bytes(range(33, 65)),
        issued_at="2026-07-21T10:00:04Z",
    )
    acceptance = GenerationAcceptance(
        schema_version=1,
        attempt_id=attempt.attempt_id,
        generation_id=generation.generation_id,
        serving_fence_id=fence.fence_id,
        serving_fence_epoch=fence.epoch,
        serving_fence_token_sha256=fence.token_sha256,
        control_lease_epoch_audit=control_snapshot.record.epoch,
        control_token_sha256_audit=control_snapshot.record.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(
            control_snapshot.record,
        ),
        entrypoint_proof_sha256="5" * 64,
        database_proof_sha256="6" * 64,
        systemd_proof_sha256="7" * 64,
        index_set_proof_sha256="8" * 64,
        health_proof_sha256="9" * 64,
        accepted_at="2026-07-21T10:00:05Z",
    )
    return GenerationStoreInput(
        store=RuntimeGenerationStore(policy, gate),
        gate=gate,
        policy=policy,
        root=root,
        generation=generation,
        attempt=attempt,
        fence=fence,
        acceptance=acceptance,
        control_snapshot=control_snapshot,
        proof=proof,
    )


def build_serving_publication_input(tmp_path: Path) -> ServingPublicationInput:
    """构造已由上游状态边形成的维护态 A；只在测试内直接播种该既有事实。"""
    transitions = build_state_transition_input(tmp_path)
    seed_state_for_test(transitions.base, transitions.staged_state)
    return ServingPublicationInput(
        base=transitions.base,
        staged_state=transitions.staged_state,
        public_state=transitions.public_state,
        permit=transitions.permit,
    )


def build_state_transition_input(tmp_path: Path) -> StateTransitionInput:
    """构造可由受控 store 重放的四条合法状态边。"""
    base = build_generation_store_input(tmp_path)
    baseline = _baseline_state()
    switching = begin_switch(
        baseline,
        base.attempt,
        base.control_snapshot.record,
        base.proof,
        control_lease_lineage=(base.control_snapshot.record,),
        updated_at="2026-07-21T10:00:04Z",
    )
    validating = begin_validation(
        switching,
        base.control_snapshot.record,
        base.proof,
        control_lease_lineage=(base.control_snapshot.record,),
        updated_at="2026-07-21T10:00:05Z",
    )
    staged_state = commit_serving(
        validating,
        base.acceptance,
        base.fence,
        base.control_snapshot.record,
        base.proof,
        control_lease_lineage=(base.control_snapshot.record,),
        updated_at="2026-07-21T10:00:06Z",
    )
    public_state = prepare_serving_publication(
        staged_state,
        serving_binding_sha256(base.acceptance),
        updated_at="2026-07-21T10:00:07Z",
    )
    permit = create_serving_permit(
        public_state,
        base.acceptance,
        base.fence,
        issued_at="2026-07-21T10:00:08Z",
    )
    seed_state_for_test(base, baseline)
    return StateTransitionInput(
        base=base,
        baseline_state=baseline,
        switching_state=switching,
        validating_state=validating,
        staged_state=staged_state,
        public_state=public_state,
        permit=permit,
    )


def _baseline_state() -> GenerationState:
    """生成与当前冻结 attempt baseline 精确一致的公开稳态。"""
    return GenerationState(
        schema_version=1,
        state_version=1,
        mode=GenerationMode.STEADY,
        serving_generation_id="d" * 64,
        serving_fence_id="legacy-fence",
        serving_fence_epoch=1,
        serving_fence_token_sha256="e" * 64,
        desired_generation_id=None,
        rollback_generation_id="f" * 64,
        control_attempt_id=None,
        control_reservation_sha256=None,
        control_lease_record_sha256=None,
        control_lease_epoch=None,
        acceptance_sha256="1" * 64,
        maintenance_active=False,
        updated_at="2026-07-21T10:00:00Z",
    )


def run_generation_state_cas_race(
    race_input: GenerationStateRaceInput,
) -> tuple[GenerationStateRaceResult, GenerationStateRaceResult]:
    """让两个真实 spawn 控制器竞争同一 state 的同一旧摘要。"""
    if type(race_input) is not GenerationStateRaceInput:
        raise TypeError("race_input 必须是 GenerationStateRaceInput")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    result_queue = context.Queue()
    processes = tuple(
        context.Process(
            target=_run_generation_state_cas_worker,
            args=(race_input, barrier, result_queue),
        )
        for _ in range(2)
    )
    deadline = monotonic() + _RACE_TIMEOUT_SECONDS
    try:
        for process in processes:
            process.start()
        results = _collect_race_results(result_queue, processes, deadline)
        _require_clean_race_exits(processes, deadline)
        return tuple(results)  # type: ignore[return-value]
    finally:
        try:
            barrier.abort()
        except (BrokenPipeError, ValueError):
            pass
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=0.2)
        result_queue.close()
        result_queue.join_thread()


def _run_generation_state_cas_worker(
    race_input: GenerationStateRaceInput,
    barrier: object,
    result_queue: object,
) -> None:
    """子进程自行重建 policy、gate 与 store，绝不继承父进程根租约。"""
    try:
        policy = RuntimeStorePolicy(
            root=Path(race_input.root),
            owner_uid=race_input.owner_uid,
        )
        store = RuntimeGenerationStore(policy, ControlLeaseStore(policy))
        barrier.wait(timeout=_BARRIER_TIMEOUT_SECONDS)
        stored = store.compare_and_swap_state(
            race_input.expected_sha256,
            race_input.desired,
            fence=None,
            permit=None,
            proof=race_input.proof,
        )
        result = GenerationStateRaceResult(
            outcome="success",
            state_sha256=stored.sha256,
        )
    except GenerationStateConflictError:
        result = GenerationStateRaceResult(outcome="conflict")
    except RuntimeGenerationStoreError as error:
        result = GenerationStateRaceResult(
            outcome="unexpected",
            error_type=type(error).__name__,
        )
    except BaseException as error:
        result = GenerationStateRaceResult(
            outcome="unexpected",
            error_type=type(error).__name__,
        )
    result_queue.put(result)


def _collect_race_results(
    result_queue: object,
    processes: tuple[object, ...],
    deadline: float,
) -> list[GenerationStateRaceResult]:
    results: list[GenerationStateRaceResult] = []
    while len(results) < len(processes):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise GenerationStateRaceError("generation state 竞争结果超时")
        try:
            result = result_queue.get(timeout=remaining)
        except Empty as error:
            raise GenerationStateRaceError("generation state worker 未回传结果") from error
        if type(result) is not GenerationStateRaceResult:
            raise GenerationStateRaceError("generation state worker 回传类型无效")
        results.append(result)
    return results


def _require_clean_race_exits(
    processes: tuple[object, ...],
    deadline: float,
) -> None:
    for process in processes:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise GenerationStateRaceError("generation state worker 退出超时")
        process.join(timeout=remaining)
        if process.is_alive() or process.exitcode != 0:
            raise GenerationStateRaceError("generation state worker 异常退出")


def seed_state_for_test(inputs: GenerationStoreInput, state: GenerationState) -> None:
    """模拟独立上游状态边已持久化；不为生产 store 增加初始化旁路。"""
    policy = private_managed_file_policy(inputs.policy.owner_uid, 32_768)
    with inputs.policy.root_binding.bind() as root:
        write_managed_bytes_atomic_at(
            generation_state_path(inputs.root),
            encode_generation_state(state),
            root=root,
            policy=policy,
        )


def _generation() -> RuntimeGeneration:
    return create_runtime_generation(
        revision="a" * 40,
        release_id="b" * 64,
        base_id="c" * 64,
        entrypoint_contract_sha256="d" * 64,
        systemd_bundle_sha256="e" * 64,
        configuration_bundle_sha256="f" * 64,
        database_contract_sha256="1" * 64,
        index_set_sha256="2" * 64,
        kind=GenerationKind.MANAGED,
        created_at="2026-07-21T10:00:00Z",
    )


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="3" * 64,
        controller_sha256="4" * 64,
        created_at="2026-07-21T10:00:00Z",
    )


__all__ = [
    "GenerationStoreInput",
    "GenerationStateRaceError",
    "GenerationStateRaceInput",
    "GenerationStateRaceResult",
    "ServingPublicationInput",
    "StateTransitionInput",
    "build_generation_store_input",
    "build_serving_publication_input",
    "build_state_transition_input",
    "run_generation_state_cas_race",
    "seed_state_for_test",
]
