"""control lease 授权、谱系、状态转换与短生命周期 scope。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from codev_platform.runtime_control_lease_persistence import (
    ControlLeasePersistence,
    ControlLeasePersistenceError,
)
from codev_platform.runtime_control_lease_transition import (
    ControlLeaseTransitionError,
    ControlLeaseTransitionIntent,
    ControlLeaseTransitionKind,
)
from codev_platform.runtime_control_lease_transition_recovery import (
    ControlLeaseTransitionRecovery,
    ControlLeaseTransitionRecoveryError,
)
from codev_platform.runtime_deployment_lock_capability import (
    BoundDeploymentLock,
    RuntimeDeploymentLockCapabilityError,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ControlLeaseStatus,
    FencingContractError,
    issue_control_lease,
    recover_control_lease,
    verify_control_lease,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError
from codev_platform.runtime_storage import (
    RuntimeStorageError,
    deployment_lock_at,
)
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeMutationScope,
    RuntimeStorePolicy,
    RuntimeTerminalCleanupScope,
)
from codev_platform.runtime_transaction_contract import (
    TransactionJournal,
    transaction_journal_sha256,
)
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalEvidence,
    transaction_terminal_evidence_sha256,
)


_Value = TypeVar("_Value")
_BoundOperation = Callable[[BoundRuntimeRoot], _Value]


class ControlLeaseStoreError(RuntimeError):
    """control lease 授权、谱系、状态转换或 scope 门禁失败。"""


class ControlLeaseStore:
    """只在同一根租约与部署锁内维护控制租约状态机。"""

    def __init__(self, policy: RuntimeStorePolicy) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        self._policy = policy
        self._persistence = ControlLeasePersistence(policy)
        self._transitions = ControlLeaseTransitionRecovery(
            policy,
            self._persistence,
        )

    def load_current(self) -> ControlLeaseSnapshot | None:
        """在 pending 前后双检下严格读取 current 与其 history。"""
        return self._with_pending_checked_read(self._persistence.load_current_or_none)

    def load_active_lineage(
        self,
        current: ControlLeaseRecord,
    ) -> tuple[ControlLeaseRecord, ...]:
        """在 pending 前后双检下重建完整活动谱系。"""
        return self._with_pending_checked_read(
            lambda root: self._transitions.active_lineage(root, current),
        )

    def acquire_initial(
        self,
        *,
        owner: str,
        token: bytes,
        issued_at: str,
    ) -> tuple[ControlLeaseSnapshot, ControlLeaseProof]:
        """从严格持久 bootstrap 签发当前为空时的初始 lease。"""

        def operation(
            root: BoundRuntimeRoot,
        ) -> tuple[ControlLeaseSnapshot, ControlLeaseProof]:
            context = self._transitions.load_active_context(root)
            previous = self._persistence.load_current_or_none(root)
            self._transitions.require_initial_predecessor(
                root,
                previous,
                context.reservation.value.attempt_id,
            )
            try:
                record, proof = issue_control_lease(
                    context.reservation.value,
                    epoch=1,
                    token=token,
                    owner=owner,
                    issued_at=issued_at,
                )
            except FencingContractError as error:
                raise ControlLeaseStoreError("初始 control lease 参数无效") from error
            intent = self._prepare_transition(
                root,
                kind=ControlLeaseTransitionKind.INITIAL,
                expected=previous,
                next_record=record,
            )
            return self._persistence.finalize_pending_transition(
                root,
                intent,
                previous,
            ), proof

        return self._with_reconciled_deployment_lock(operation)

    def take_over(
        self,
        current: ControlLeaseSnapshot,
        *,
        owner: str,
        token: bytes,
        issued_at: str,
    ) -> tuple[ControlLeaseSnapshot, ControlLeaseProof]:
        """对精确 current 执行一次由外部授权的 higher-epoch 接管。"""
        if type(current) is not ControlLeaseSnapshot:
            raise ControlLeaseStoreError("current 必须是 ControlLeaseSnapshot")

        def operation(
            root: BoundRuntimeRoot,
        ) -> tuple[ControlLeaseSnapshot, ControlLeaseProof]:
            context = self._transitions.load_active_context(root)
            actual = self._persistence.load_current_required(root)
            self._require_current_snapshot(actual, current)
            lineage = self._transitions.active_lineage(root, actual.record)
            self._transitions.require_active_bootstrap_binding(
                actual.record,
                context.reservation,
            )
            if actual.record.status is not ControlLeaseStatus.ACTIVE:
                raise ControlLeaseStoreError("只能接管活动 control lease")
            try:
                successor, proof = recover_control_lease(
                    actual.record,
                    context.reservation.value,
                    epoch=actual.record.epoch + 1,
                    token=token,
                    owner=owner,
                    issued_at=issued_at,
                )
            except FencingContractError as error:
                raise ControlLeaseStoreError("control lease 接管参数无效") from error
            if lineage[-1] != actual.record:
                raise ControlLeaseStoreError("control lease lineage 末端漂移")
            intent = self._prepare_transition(
                root,
                kind=ControlLeaseTransitionKind.TAKEOVER,
                expected=actual,
                next_record=successor,
            )
            return self._persistence.finalize_pending_transition(
                root,
                intent,
                actual,
            ), proof

        return self._with_reconciled_deployment_lock(operation)

    def recover_pending_transition(self) -> ControlLeaseSnapshot | None:
        """在唯一根租约与部署锁内收敛已固化的唯一提交决定。"""
        return self._with_reconciled_deployment_lock(
            self._persistence.load_current_or_none,
        )

    @contextmanager
    def mutation(self, proof: ControlLeaseProof) -> Iterator[RuntimeMutationScope]:
        """在唯一 bind 与部署锁作用域中校验 proof 后授予活动 scope。"""
        with self._reconciled_deployment_scope() as (root, deployment_lock):
            context = self._transitions.load_active_context(root)
            current = self._persistence.load_current_required(root)
            lineage = self._transitions.active_lineage(root, current.record)
            self._transitions.require_active_bootstrap_binding(
                current.record,
                context.reservation,
            )
            self._verify_proof(current.record, proof)
            self._policy.root_binding.require_bound(root)
            yield RuntimeMutationScope(
                snapshot=current,
                bound_root=root,
                control_lease_lineage=lineage,
                deployment_lock=deployment_lock,
            )

    def retire_current(
        self,
        proof: ControlLeaseProof,
        completed: TransactionJournal,
        evidence: TransactionTerminalEvidence,
        *,
        retired_at: str,
    ) -> ControlLeaseSnapshot:
        """仅凭同一临界区的持久终态与完整谱系发布 RETIRED tombstone。"""

        def operation(root: BoundRuntimeRoot) -> ControlLeaseSnapshot:
            context = self._transitions.load_active_context(root)
            current = self._persistence.load_current_required(root)
            lineage = self._transitions.active_lineage(root, current.record)
            self._transitions.require_active_bootstrap_binding(
                current.record,
                context.reservation,
            )
            self._verify_proof(current.record, proof)
            journal, terminal = self._transitions.load_persisted_terminal_inputs(
                root,
                current.record.attempt_id,
                (completed, evidence),
            )
            self._transitions.require_terminal_binding(
                current.record,
                journal,
                terminal,
                retired_at,
                lineage,
            )
            retired = ControlLeaseRecord(
                schema_version=current.record.schema_version,
                attempt_id=current.record.attempt_id,
                reservation_sha256=current.record.reservation_sha256,
                epoch=current.record.epoch,
                token_sha256=current.record.token_sha256,
                owner=current.record.owner,
                status=ControlLeaseStatus.RETIRED,
                issued_at=current.record.issued_at,
                predecessor_sha256=current.record.predecessor_sha256,
                terminal_journal_sha256=transaction_journal_sha256(journal),
                terminal_evidence_sha256=transaction_terminal_evidence_sha256(terminal),
                retired_at=retired_at,
                retired_from_sha256=current.sha256,
            )
            intent = self._prepare_transition(
                root,
                kind=ControlLeaseTransitionKind.RETIRE,
                expected=current,
                next_record=retired,
            )
            return self._persistence.finalize_pending_transition(root, intent, current)

        return self._with_reconciled_deployment_lock(operation)

    @contextmanager
    def terminal_cleanup(
        self,
        tombstone: ControlLeaseSnapshot,
    ) -> Iterator[RuntimeTerminalCleanupScope]:
        """在唯一 bind 与部署锁作用域中授予精确 RETIRED 清理 scope。"""
        if type(tombstone) is not ControlLeaseSnapshot:
            raise ControlLeaseStoreError("tombstone 必须是 ControlLeaseSnapshot")
        with self._reconciled_deployment_scope() as (root, deployment_lock):
            current = self._persistence.load_current_required(root)
            self._require_current_snapshot(current, tombstone)
            self._transitions.verify_retired_tombstone(root, current)
            self._policy.root_binding.require_bound(root)
            yield RuntimeTerminalCleanupScope(
                snapshot=current,
                bound_root=root,
                deployment_lock=deployment_lock,
            )

    def _with_bound_root(self, operation: _BoundOperation) -> _Value:
        try:
            with self._policy.root_binding.bind() as root:
                return operation(root)
        except ControlLeaseStoreError:
            raise
        except ControlLeaseTransitionRecoveryError as error:
            raise ControlLeaseStoreError(str(error)) from error
        except ControlLeasePersistenceError as error:
            raise ControlLeaseStoreError(str(error)) from None
        except _STORE_BOUND_ERRORS as error:
            raise ControlLeaseStoreError("运行时根不可安全访问") from error

    def _with_pending_checked_read(self, operation: _BoundOperation) -> _Value:
        def read(root: BoundRuntimeRoot) -> _Value:
            self._transitions.require_no_pending_transition(root)
            result = operation(root)
            self._transitions.require_no_pending_transition(root)
            return result

        return self._with_bound_root(read)

    def _with_reconciled_deployment_lock(self, operation: _BoundOperation) -> _Value:
        with self._reconciled_deployment_scope() as (root, _deployment_lock):
            return operation(root)

    @contextmanager
    def _reconciled_deployment_scope(
        self,
    ) -> Iterator[tuple[BoundRuntimeRoot, BoundDeploymentLock]]:
        try:
            with self._policy.root_binding.bind() as root:
                with deployment_lock_at(root) as deployment_lock:
                    self._transitions.reconcile(root)
                    yield root, deployment_lock
        except ControlLeaseStoreError:
            raise
        except ControlLeaseTransitionRecoveryError as error:
            raise ControlLeaseStoreError(str(error)) from error
        except ControlLeasePersistenceError as error:
            raise ControlLeaseStoreError(str(error)) from None
        except _STORE_BOUND_ERRORS as error:
            raise ControlLeaseStoreError("部署锁或运行时根不可安全访问") from error

    def _prepare_transition(
        self,
        root: BoundRuntimeRoot,
        *,
        kind: ControlLeaseTransitionKind,
        expected: ControlLeaseSnapshot | None,
        next_record: ControlLeaseRecord,
    ) -> ControlLeaseTransitionIntent:
        try:
            intent = ControlLeaseTransitionIntent(
                schema_version=1,
                kind=kind,
                expected_record=None if expected is None else expected.record,
                next_record=next_record,
            )
        except ControlLeaseTransitionError as error:
            raise ControlLeaseStoreError("control lease transition 参数无效") from error
        return self._persistence.prepare_pending_transition(root, intent)

    def _require_current_snapshot(
        self,
        actual: ControlLeaseSnapshot,
        expected: ControlLeaseSnapshot,
    ) -> None:
        if actual.sha256 != expected.sha256 or actual.record != expected.record:
            raise ControlLeaseStoreError("current control lease CAS 已变化")

    def _verify_proof(self, record: ControlLeaseRecord, proof: ControlLeaseProof) -> None:
        try:
            verify_control_lease(record, proof)
        except FencingContractError as error:
            raise ControlLeaseStoreError("control lease proof 无效") from error


_STORE_BOUND_ERRORS = (
    RuntimeDeploymentLockCapabilityError,
    RuntimeRootBindingError,
    RuntimeStorageError,
)


__all__ = ["ControlLeaseStore", "ControlLeaseStoreError"]
