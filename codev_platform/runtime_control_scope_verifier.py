"""分域 store 使用的持久 control scope 复验器。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from codev_platform.runtime_control_lease_persistence import (
    ControlLeasePersistence,
    ControlLeasePersistenceError,
)
from codev_platform.runtime_control_lease_transition_recovery import (
    ControlLeaseTransitionRecovery,
    ControlLeaseTransitionRecoveryError,
)
from codev_platform.runtime_deployment_lock_capability import (
    RuntimeDeploymentLockCapabilityError,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    FencingContractError,
    verify_control_lease,
)
from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBindingError,
)
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeMutationScope,
    RuntimeStorePolicy,
    RuntimeTerminalCleanupScope,
)


class RuntimeControlScopeVerificationError(RuntimeError):
    """活动 scope 不能证明来自当前持久 control lease。"""


class RuntimeMutationScopeVerifier:
    """在 gate 已持锁的同一根租约内复验 scope 的持久真实性。"""

    def __init__(self, policy: RuntimeStorePolicy) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        persistence = ControlLeasePersistence(policy)
        self._policy = policy
        self._persistence = persistence
        self._transitions = ControlLeaseTransitionRecovery(policy, persistence)

    def verify(
        self,
        scope: RuntimeMutationScope,
        proof: ControlLeaseProof,
        *,
        root: BoundRuntimeRoot,
    ) -> RuntimeMutationScope:
        """拒绝伪造、过期、未收敛或脱离当前 history 的活动 scope。"""
        if type(scope) is not RuntimeMutationScope:
            raise RuntimeControlScopeVerificationError("gate 未授予 RuntimeMutationScope")
        if root is not scope.bound_root:
            raise RuntimeControlScopeVerificationError("scope 根租约与当前操作根不一致")
        try:
            scope.deployment_lock.require_active(root)
            self._policy.root_binding.require_bound(root)
            self._transitions.require_no_pending_transition(root)
            current = self._persistence.load_current_required(root)
            if current != scope.snapshot:
                raise RuntimeControlScopeVerificationError(
                    "scope 快照不是当前持久 control lease",
                )
            context = self._transitions.load_active_context(root)
            self._transitions.require_active_bootstrap_binding(
                current.record,
                context.reservation,
            )
            lineage = self._transitions.active_lineage(root, current.record)
            if lineage != scope.control_lease_lineage:
                raise RuntimeControlScopeVerificationError(
                    "scope control lease lineage 与持久 history 不一致",
                )
            verify_control_lease(current.record, proof)
        except RuntimeControlScopeVerificationError:
            raise
        except (
            ControlLeasePersistenceError,
            ControlLeaseTransitionRecoveryError,
            FencingContractError,
            RuntimeDeploymentLockCapabilityError,
            RuntimeRootBindingError,
        ) as error:
            raise RuntimeControlScopeVerificationError(
                "活动 control scope 无法通过持久复验",
            ) from error
        return scope

    @contextmanager
    def hold_verified_mutation(
        self,
        scope: RuntimeMutationScope,
        proof: ControlLeaseProof,
        *,
        root: BoundRuntimeRoot,
    ) -> Iterator[RuntimeMutationScope]:
        """在未释放 deployment flock capability 的整个操作期复验活动 scope。"""
        if type(scope) is not RuntimeMutationScope:
            raise RuntimeControlScopeVerificationError("gate 未授予 RuntimeMutationScope")
        try:
            with scope.deployment_lock.hold_active(root):
                yield self.verify(scope, proof, root=root)
        except RuntimeControlScopeVerificationError:
            raise
        except RuntimeDeploymentLockCapabilityError as error:
            raise RuntimeControlScopeVerificationError(
                "活动 control scope 未持有有效 deployment-lock capability",
            ) from error

    def verify_terminal_cleanup(
        self,
        scope: RuntimeTerminalCleanupScope,
        tombstone: ControlLeaseSnapshot,
        *,
        root: BoundRuntimeRoot,
    ) -> RuntimeTerminalCleanupScope:
        """拒绝伪造、漂移或未持久化的终态清理 tombstone scope。"""
        if type(scope) is not RuntimeTerminalCleanupScope:
            raise RuntimeControlScopeVerificationError(
                "gate 未授予 RuntimeTerminalCleanupScope",
            )
        if type(tombstone) is not ControlLeaseSnapshot:
            raise RuntimeControlScopeVerificationError("tombstone 类型无效")
        if root is not scope.bound_root:
            raise RuntimeControlScopeVerificationError("scope 根租约与当前操作根不一致")
        if scope.snapshot != tombstone:
            raise RuntimeControlScopeVerificationError(
                "scope tombstone 与调用方请求不一致",
            )
        try:
            scope.deployment_lock.require_active(root)
            self._policy.root_binding.require_bound(root)
            self._transitions.require_no_pending_transition(root)
            current = self._persistence.load_current_required(root)
            if current != tombstone:
                raise RuntimeControlScopeVerificationError(
                    "tombstone 不是当前持久 control lease",
                )
            self._transitions.verify_retired_tombstone(root, current)
        except RuntimeControlScopeVerificationError:
            raise
        except (
            ControlLeasePersistenceError,
            ControlLeaseTransitionRecoveryError,
            RuntimeDeploymentLockCapabilityError,
            RuntimeRootBindingError,
        ) as error:
            raise RuntimeControlScopeVerificationError(
                "终态清理 scope 无法通过持久复验",
            ) from error
        return scope

    @contextmanager
    def hold_verified_terminal_cleanup(
        self,
        scope: RuntimeTerminalCleanupScope,
        tombstone: ControlLeaseSnapshot,
        *,
        root: BoundRuntimeRoot,
    ) -> Iterator[RuntimeTerminalCleanupScope]:
        """在未释放 deployment flock capability 的整个清理期复验 tombstone。"""
        if type(scope) is not RuntimeTerminalCleanupScope:
            raise RuntimeControlScopeVerificationError(
                "gate 未授予 RuntimeTerminalCleanupScope",
            )
        try:
            with scope.deployment_lock.hold_active(root):
                yield self.verify_terminal_cleanup(scope, tombstone, root=root)
        except RuntimeControlScopeVerificationError:
            raise
        except RuntimeDeploymentLockCapabilityError as error:
            raise RuntimeControlScopeVerificationError(
                "终态清理 scope 未持有有效 deployment-lock capability",
            ) from error


__all__ = [
    "RuntimeControlScopeVerificationError",
    "RuntimeMutationScopeVerifier",
]
