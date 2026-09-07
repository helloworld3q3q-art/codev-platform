"""回滚包 store 的真实 control scope 与持久基线夹具。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from codev_platform.runtime_attempt_contract import AttemptReservation
from codev_platform.runtime_fencing import ControlLeaseProof
from codev_platform.runtime_fencing_store import ControlLeaseStore
from codev_platform.runtime_generation_contract import encode_runtime_generation
from codev_platform.runtime_managed_file import create_managed_bytes_exclusive_at
from codev_platform.runtime_recovery_contract import create_recovery_envelope
from codev_platform.runtime_rollback_contract import ProtectedPayloadRef, RollbackBundle
from codev_platform.runtime_rollback_store import _InjectedRuntimeRollbackStore
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_storage import generation_record_path
from codev_platform.runtime_store_protocols import RuntimeStorePolicy, private_managed_file_policy
from codev_platform.runtime_transaction_store import (
    RuntimeTransactionBootstrapStore,
)
from codev_platform.runtime_transaction_controlled_store import RuntimeTransactionStore
from codev_platform.runtime_transaction_contract import create_transaction_journal
from tests.runtime_rollback_test_support import BoundRollbackFacts, bound_bundle, bound_facts


@dataclass(slots=True)
class RecordingProtectedPayloadVerifier:
    """记录注入端口调用，可按指定身份模拟载荷完整性拒绝。"""

    rejected_identity_sha256: str | None = None
    runtime_error_identity_sha256: str | None = None
    os_error_identity_sha256: str | None = None
    fail_at_call: int | None = None
    calls: list[tuple[ProtectedPayloadRef, BoundRuntimeRoot]] = field(
        default_factory=list,
    )

    def verify(self, payload: ProtectedPayloadRef, *, root: BoundRuntimeRoot) -> None:
        self.calls.append((payload, root))
        if self.fail_at_call == len(self.calls):
            raise ValueError("受保护载荷在指定校验轮次拒绝")
        if payload.identity_sha256 == self.runtime_error_identity_sha256:
            raise RuntimeError("受保护载荷 verifier 发生运行期错误")
        if payload.identity_sha256 == self.os_error_identity_sha256:
            raise OSError("/run/credentials/runtime-payload-verifier 不可读取")
        if payload.identity_sha256 == self.rejected_identity_sha256:
            raise ValueError("受保护载荷完整性校验失败")


@dataclass(frozen=True, slots=True)
class RollbackStoreInput:
    """回滚 store 真实持久链、活动 proof 与注入 verifier。"""

    store: _InjectedRuntimeRollbackStore
    policy: RuntimeStorePolicy
    root: Path
    facts: BoundRollbackFacts
    proof: ControlLeaseProof
    verifier: RecordingProtectedPayloadVerifier


@dataclass(frozen=True, slots=True)
class RollbackStoreContext:
    """真实 bootstrap、lease、冻结 attempt 与基线 generation 上下文。"""

    policy: RuntimeStorePolicy
    root: Path
    facts: BoundRollbackFacts
    proof: ControlLeaseProof
    gate: ControlLeaseStore


def build_rollback_store_context(
    tmp_path: Path,
    *,
    facts: BoundRollbackFacts | None = None,
    owner_uid: int | None = None,
) -> RollbackStoreContext:
    """建立可由 fake 或 concrete verifier 共同复用的真实持久控制链。"""
    if facts is None:
        facts = bound_facts()
    root = tmp_path / "runtime"
    root.mkdir()
    if owner_uid is None:
        owner_uid = os.geteuid()
    policy = RuntimeStorePolicy(root=root, owner_uid=owner_uid)
    reservation = AttemptReservation(
        schema_version=facts.attempt.schema_version,
        attempt_id=facts.attempt.attempt_id,
        operation=facts.attempt.operation,
        plan_sha256=facts.attempt.plan_sha256,
        controller_sha256=facts.attempt.controller_sha256,
        created_at=facts.attempt.created_at,
    )
    bootstrap = RuntimeTransactionBootstrapStore(policy)
    journal = create_transaction_journal(
        reservation,
        created_at="2026-07-21T11:00:00Z",
    )
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="f" * 64,
        transaction_store_id="runtime-rollback-store",
        created_at="2026-07-21T11:00:01Z",
    )
    bootstrap.reserve_attempt(reservation)
    bootstrap.write_journal_genesis_once(journal)
    bootstrap.write_envelope_once(envelope)
    bootstrap.publish_active_envelope_if_absent(envelope)
    gate = ControlLeaseStore(policy)
    _snapshot, proof = gate.acquire_initial(
        owner="rollback-controller",
        token=bytes(range(1, 33)),
        issued_at="2026-07-21T11:00:02Z",
    )
    RuntimeTransactionStore(policy, gate).write_attempt_once(facts.attempt, proof=proof)
    generation_policy = private_managed_file_policy(policy.owner_uid, 32_768)
    with policy.root_binding.bind() as bound_root:
        create_managed_bytes_exclusive_at(
            generation_record_path(root, facts.generation.generation_id),
            encode_runtime_generation(facts.generation),
            root=bound_root,
            policy=generation_policy,
        )
    return RollbackStoreContext(
        policy=policy,
        root=root,
        facts=facts,
        proof=proof,
        gate=gate,
    )


def build_rollback_store_input(tmp_path: Path) -> RollbackStoreInput:
    """建立默认 fake verifier 的回滚 store 定向测试夹具。"""
    context = build_rollback_store_context(tmp_path)
    verifier = RecordingProtectedPayloadVerifier()
    return RollbackStoreInput(
        store=_InjectedRuntimeRollbackStore(context.policy, context.gate, verifier),
        policy=context.policy,
        root=context.root,
        facts=context.facts,
        proof=context.proof,
        verifier=verifier,
    )


def valid_bundle() -> RollbackBundle:
    """返回与 `build_rollback_store_input()` 同一冻结事实一致的 bundle。"""
    return bound_bundle()


__all__ = [
    "RecordingProtectedPayloadVerifier",
    "RollbackStoreContext",
    "RollbackStoreInput",
    "build_rollback_store_context",
    "build_rollback_store_input",
    "valid_bundle",
]
