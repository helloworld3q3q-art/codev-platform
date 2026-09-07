"""generation-state 的严格读取与状态机 CAS 实现。"""

from __future__ import annotations

from collections.abc import Callable

from codev_platform._runtime_generation_store_validation import (
    GenerationStateConflictError,
    load_acceptance,
    load_frozen_attempt,
    load_generation,
    load_generation_state,
    load_serving_fence,
    load_serving_permit,
    load_staged_serving_permit,
    require_acceptance_bindings,
    require_attempt_target_generation,
    require_permit_target_state,
    require_same_bytes,
    require_staged_state_current_control,
    require_state_cas_precondition,
)
from codev_platform.runtime_attempt_contract import DeploymentAttempt
from codev_platform.core.runtime_models import canonical_sha256
from codev_platform.runtime_bootstrap_loader import PersistedActiveBootstrapLoader
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ServingFenceRecord,
    encode_serving_fence_record,
)
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
)
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    begin_switch,
    begin_validation,
    commit_serving,
    encode_generation_state,
    generation_state_sha256,
    mark_restricted,
    mark_safety_unproven,
    prepare_serving_publication,
)
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    write_managed_bytes_atomic_at,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_serving_permit import (
    ServingPermitRecord,
    encode_serving_permit,
    serving_permit_sha256,
)
from codev_platform.runtime_storage import (
    activation_lock_at,
    generation_state_path,
)
from codev_platform.runtime_store_protocols import (
    RuntimeMutationScope,
    StoredSnapshot,
)


class GenerationStateCasService:
    """只负责单一状态文件及其可证明的状态机边。"""

    def __init__(
        self,
        *,
        root_path: object,
        loader: PersistedActiveBootstrapLoader,
        attempt_policy: ManagedFilePolicy,
        generation_policy: ManagedFilePolicy,
        fence_policy: ManagedFilePolicy,
        acceptance_policy: ManagedFilePolicy,
        state_policy: ManagedFilePolicy,
        permit_policy: ManagedFilePolicy,
    ) -> None:
        self._root_path = root_path
        self._loader = loader
        self._attempt_policy = attempt_policy
        self._generation_policy = generation_policy
        self._fence_policy = fence_policy
        self._acceptance_policy = acceptance_policy
        self._state_policy = state_policy
        self._permit_policy = permit_policy

    def load_state(
        self,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[GenerationState]:
        """严格读取唯一 state；缺失时由受管文件层闭锁。"""
        return load_generation_state(
            self._root_path,
            root=root,
            policy=self._state_policy,
        )

    def compare_and_swap(
        self,
        scope: RuntimeMutationScope,
        root: BoundRuntimeRoot,
        expected_sha256: str,
        desired: GenerationState,
        *,
        fence: ServingFenceRecord | None,
        permit: ServingPermitRecord | None,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[GenerationState]:
        """在 activation lock 内仅写入既有纯转换函数导出的唯一后继。"""
        with activation_lock_at(root):
            current = self.load_state(root)
            require_state_cas_precondition(
                expected_sha256,
                current.value,
                current.sha256,
                desired,
            )
            self._require_exact_successor(
                scope,
                root,
                current.value,
                desired,
                fence=fence,
                permit=permit,
                proof=proof,
            )
            payload = encode_generation_state(desired)
            write_managed_bytes_atomic_at(
                generation_state_path(self._root_path),
                payload,
                root=root,
                policy=self._state_policy,
            )
            persisted = self.load_state(root)
            require_same_bytes(
                "generation state",
                payload,
                encode_generation_state(persisted.value),
            )
            return persisted

    def _require_exact_successor(
        self,
        scope: RuntimeMutationScope,
        root: BoundRuntimeRoot,
        current: GenerationState,
        desired: GenerationState,
        *,
        fence: ServingFenceRecord | None,
        permit: ServingPermitRecord | None,
        proof: ControlLeaseProof,
    ) -> None:
        if desired.mode is GenerationMode.RESTRICTED:
            self._require_no_evidence(fence, permit)
            self._match_transition(
                "mark_restricted",
                desired,
                lambda: mark_restricted(
                    current,
                    scope.snapshot.record,
                    proof,
                    control_lease_lineage=scope.control_lease_lineage,
                    updated_at=desired.updated_at,
                ),
            )
            return
        if desired.mode is GenerationMode.SAFETY_UNPROVEN:
            self._require_no_evidence(fence, permit)
            self._match_transition(
                "mark_safety_unproven",
                desired,
                lambda: mark_safety_unproven(
                    current,
                    scope.snapshot.record,
                    proof,
                    control_lease_lineage=scope.control_lease_lineage,
                    updated_at=desired.updated_at,
                ),
            )
            return
        if (
            current.mode is GenerationMode.STEADY
            and not current.maintenance_active
            and desired.mode is GenerationMode.SWITCHING
        ):
            self._require_no_evidence(fence, permit)
            attempt = self._load_scope_attempt(scope, root)
            generation = load_generation(
                self._root_path,
                attempt.value.target_generation_id,
                root=root,
                policy=self._generation_policy,
            )
            require_attempt_target_generation(attempt.value, generation.value)
            self._match_transition(
                "begin_switch",
                desired,
                lambda: begin_switch(
                    current,
                    attempt.value,
                    scope.snapshot.record,
                    proof,
                    control_lease_lineage=scope.control_lease_lineage,
                    updated_at=desired.updated_at,
                ),
            )
            return
        if current.mode is GenerationMode.SWITCHING and desired.mode is GenerationMode.VALIDATING:
            self._require_no_evidence(fence, permit)
            self._match_transition(
                "begin_validation",
                desired,
                lambda: begin_validation(
                    current,
                    scope.snapshot.record,
                    proof,
                    control_lease_lineage=scope.control_lease_lineage,
                    updated_at=desired.updated_at,
                ),
            )
            return
        if (
            current.mode is GenerationMode.VALIDATING
            and desired.mode is GenerationMode.STEADY
            and desired.maintenance_active
        ):
            if permit is not None:
                raise GenerationStateConflictError("commit_serving 不得携带 serving permit")
            acceptance, persisted_fence = self._load_commit_evidence(
                scope,
                root,
                fence,
            )
            self._match_transition(
                "commit_serving",
                desired,
                lambda: commit_serving(
                    current,
                    acceptance,
                    persisted_fence,
                    scope.snapshot.record,
                    proof,
                    control_lease_lineage=scope.control_lease_lineage,
                    updated_at=desired.updated_at,
                ),
            )
            return
        if (
            current.mode is GenerationMode.STEADY
            and current.maintenance_active
            and desired.mode is GenerationMode.STEADY
            and not desired.maintenance_active
        ):
            if fence is not None:
                raise GenerationStateConflictError("公开发布不得携带 serving fence")
            acceptance, persisted_fence, persisted_permit = self._load_publication_evidence(
                scope,
                root,
                current,
                permit,
            )
            require_staged_state_current_control(
                current,
                scope.snapshot.record,
                proof,
                scope.control_lease_lineage,
            )
            self._match_transition(
                "prepare_serving_publication",
                desired,
                lambda: prepare_serving_publication(
                    current,
                    current.acceptance_sha256,
                    updated_at=desired.updated_at,
                ),
            )
            require_permit_target_state(
                persisted_permit,
                current,
                desired,
                acceptance,
                persisted_fence,
            )
            return
        raise GenerationStateConflictError("generation state 不存在可证明的状态转换边")

    def _load_scope_attempt(
        self,
        scope: RuntimeMutationScope,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[DeploymentAttempt]:
        context = self._loader.load_active_context(bound_root=root)
        return load_frozen_attempt(
            self._root_path,
            scope.snapshot.record.attempt_id,
            context.reservation.value,
            root=root,
            policy=self._attempt_policy,
        )

    def _load_commit_evidence(
        self,
        scope: RuntimeMutationScope,
        root: BoundRuntimeRoot,
        fence: ServingFenceRecord | None,
    ) -> tuple[GenerationAcceptance, ServingFenceRecord]:
        if type(fence) is not ServingFenceRecord:
            raise GenerationStateConflictError("commit_serving 必须显式提供 ServingFenceRecord")
        if fence.accepted_attempt_id != scope.snapshot.record.attempt_id:
            raise GenerationStateConflictError("commit_serving fence 未绑定当前 attempt")
        return self._load_acceptance_evidence(
            scope,
            root,
            attempt_id=scope.snapshot.record.attempt_id,
            fence_sha256=canonical_sha256(fence),
            provided_fence=fence,
        )

    def _load_publication_evidence(
        self,
        scope: RuntimeMutationScope,
        root: BoundRuntimeRoot,
        current: GenerationState,
        permit: ServingPermitRecord | None,
    ) -> tuple[GenerationAcceptance, ServingFenceRecord, ServingPermitRecord]:
        if type(permit) is not ServingPermitRecord:
            raise GenerationStateConflictError("公开 state CAS 必须显式提供 ServingPermitRecord")
        if permit.attempt_id != scope.snapshot.record.attempt_id:
            raise GenerationStateConflictError("serving permit 未绑定当前 attempt")
        staged_permit = load_staged_serving_permit(
            self._root_path,
            permit.attempt_id,
            generation_state_sha256(current),
            root=root,
            policy=self._permit_policy,
        )
        require_same_bytes(
            "staged serving permit",
            encode_serving_permit(permit),
            encode_serving_permit(staged_permit.value),
        )
        persisted_permit = load_serving_permit(
            self._root_path,
            permit.attempt_id,
            serving_permit_sha256(permit),
            root=root,
            policy=self._permit_policy,
        )
        require_same_bytes(
            "serving permit",
            encode_serving_permit(permit),
            encode_serving_permit(persisted_permit.value),
        )
        acceptance, persisted_fence = self._load_acceptance_evidence(
            scope,
            root,
            attempt_id=permit.attempt_id,
            fence_sha256=permit.serving_fence_sha256,
            provided_fence=None,
        )
        return acceptance, persisted_fence, persisted_permit.value

    def _load_acceptance_evidence(
        self,
        scope: RuntimeMutationScope,
        root: BoundRuntimeRoot,
        *,
        attempt_id: str,
        fence_sha256: str,
        provided_fence: ServingFenceRecord | None,
    ) -> tuple[GenerationAcceptance, ServingFenceRecord]:
        if attempt_id != scope.snapshot.record.attempt_id:
            raise GenerationStateConflictError("证据链未绑定当前 attempt")
        attempt = self._load_scope_attempt(scope, root)
        persisted_fence = load_serving_fence(
            self._root_path,
            attempt_id,
            fence_sha256,
            root=root,
            policy=self._fence_policy,
        )
        if provided_fence is not None:
            require_same_bytes(
                "serving fence",
                encode_serving_fence_record(provided_fence),
                encode_serving_fence_record(persisted_fence.value),
            )
        acceptance = load_acceptance(
            self._root_path,
            attempt_id,
            root=root,
            policy=self._acceptance_policy,
        )
        generation = load_generation(
            self._root_path,
            acceptance.value.generation_id,
            root=root,
            policy=self._generation_policy,
        )
        require_acceptance_bindings(
            acceptance.value,
            persisted_fence.value,
            attempt.value,
            generation.value,
            scope.snapshot.record,
            scope.control_lease_lineage,
        )
        return acceptance.value, persisted_fence.value

    @staticmethod
    def _require_no_evidence(
        fence: ServingFenceRecord | None,
        permit: ServingPermitRecord | None,
    ) -> None:
        if fence is not None or permit is not None:
            raise GenerationStateConflictError("该状态边不得携带 fence 或 serving permit")

    @staticmethod
    def _match_transition(
        label: str,
        desired: GenerationState,
        transition: Callable[[], GenerationState],
    ) -> None:
        try:
            expected = transition()
        except ValueError as error:
            raise GenerationStateConflictError(
                f"{label} 不能从当前 state 导出唯一后继",
            ) from error
        if desired != expected:
            raise GenerationStateConflictError(
                f"desired state 不是 {label} 导出的唯一后继",
            )


__all__ = ["GenerationStateCasService"]
