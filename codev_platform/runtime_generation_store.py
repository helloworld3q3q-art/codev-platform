"""运行代际不可变记录与状态 CAS 的受控存储入口。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from codev_platform._runtime_generation_store_state import GenerationStateCasService
from codev_platform._runtime_store_public_input import StorePublicInputValidator
from codev_platform._runtime_generation_store_validation import (
    RuntimeGenerationStoreError,
    load_acceptance,
    load_frozen_attempt,
    load_generation,
    load_generation_state,
    load_serving_fence,
    load_serving_permit,
    load_staged_serving_permit,
    require_attempt_target_generation,
    require_acceptance_bindings,
    require_fence_attempt_generation,
    require_permit_target_state,
    require_same_bytes,
    require_staged_state_current_control,
    snapshot,
)
from codev_platform.runtime_control_scope_verifier import (
    RuntimeMutationScopeVerifier,
)
from codev_platform.runtime_bootstrap_loader import (
    PersistedActiveBootstrapLoader,
    PersistedActiveBootstrapLoaderError,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ServingFenceRecord,
    encode_serving_fence_record,
)
from codev_platform.runtime_generation_acceptance import GenerationAcceptance
from codev_platform.runtime_generation_acceptance import (
    encode_generation_acceptance,
)
from codev_platform.runtime_generation_contract import RuntimeGeneration
from codev_platform.runtime_generation_state_model import (
    GenerationState,
    generation_state_sha256,
)
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    create_managed_bytes_exclusive_at,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError
from codev_platform.runtime_serving_permit import ServingPermitRecord
from codev_platform.runtime_serving_permit import (
    encode_serving_permit,
    serving_permit_sha256,
)
from codev_platform.runtime_storage import (
    RuntimeStorageError,
    acceptance_record_path,
    generation_record_path,
    serving_fence_record_path,
    serving_permit_record_path,
    serving_permit_stage_path,
)
from codev_platform.runtime_store_protocols import (
    RuntimeMutationScope,
    RuntimeControlGate,
    RuntimeStorePolicy,
    StoredSnapshot,
    private_managed_file_policy,
)


_GENERATION_MAX_BYTES = 32_768
_ATTEMPT_MAX_BYTES = 16_384
_FENCE_MAX_BYTES = 16_384
_ACCEPTANCE_MAX_BYTES = 32_768
_STATE_MAX_BYTES = 32_768
_PERMIT_MAX_BYTES = 16_384
_Value = TypeVar("_Value")
_STORE_ERRORS = (
    ManagedFileError,
    PersistedActiveBootstrapLoaderError,
    RuntimeRootBindingError,
    RuntimeStorageError,
    ValueError,
)
_INPUT = StorePublicInputValidator(RuntimeGenerationStoreError)


@dataclass(frozen=True, slots=True)
class GenerationStateSnapshot:
    """已严格读取且以完整规范摘要标识的代际状态。"""

    state: GenerationState
    sha256: str


class RuntimeGenerationStore:
    """封装 generation、fence、acceptance、permit 与状态发布入口。"""

    def __init__(self, policy: RuntimeStorePolicy, gate: RuntimeControlGate) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        if not callable(getattr(gate, "mutation", None)):
            raise TypeError("gate 必须实现 RuntimeControlGate")
        self._policy = policy
        self._gate = gate
        self._root = policy.root
        self._scope_verifier = RuntimeMutationScopeVerifier(policy)
        self._loader = PersistedActiveBootstrapLoader(policy)
        self._generation_policy = private_managed_file_policy(
            policy.owner_uid,
            _GENERATION_MAX_BYTES,
        )
        self._attempt_policy = private_managed_file_policy(
            policy.owner_uid,
            _ATTEMPT_MAX_BYTES,
        )
        self._fence_policy = private_managed_file_policy(
            policy.owner_uid,
            _FENCE_MAX_BYTES,
        )
        self._acceptance_policy = private_managed_file_policy(
            policy.owner_uid,
            _ACCEPTANCE_MAX_BYTES,
        )
        self._state_policy = private_managed_file_policy(
            policy.owner_uid,
            _STATE_MAX_BYTES,
        )
        self._permit_policy = private_managed_file_policy(
            policy.owner_uid,
            _PERMIT_MAX_BYTES,
        )
        self._state_store = GenerationStateCasService(
            root_path=self._root,
            loader=self._loader,
            attempt_policy=self._attempt_policy,
            generation_policy=self._generation_policy,
            fence_policy=self._fence_policy,
            acceptance_policy=self._acceptance_policy,
            state_policy=self._state_policy,
            permit_policy=self._permit_policy,
        )

    def write_generation_once(
        self,
        generation: RuntimeGeneration,
        *,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[RuntimeGeneration]:
        from codev_platform.runtime_generation_contract import encode_runtime_generation

        payload = _INPUT.encode(
            generation,
            encode_runtime_generation,
            label="generation",
        )

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[RuntimeGeneration]:
            context = self._loader.load_active_context(bound_root=root)
            attempt = load_frozen_attempt(
                self._root,
                scope.snapshot.record.attempt_id,
                context.reservation.value,
                root=root,
                policy=self._attempt_policy,
            )
            require_attempt_target_generation(attempt.value, generation)
            path = generation_record_path(self._root, generation.generation_id)
            try:
                create_managed_bytes_exclusive_at(
                    path,
                    payload,
                    root=root,
                    policy=self._generation_policy,
                )
            except FileExistsError:
                existing = load_generation(
                    self._root,
                    generation.generation_id,
                    root=root,
                    policy=self._generation_policy,
                )
                require_same_bytes(
                    "generation",
                    payload,
                    encode_runtime_generation(existing.value),
                )
                return existing
            return snapshot(generation, payload)

        return self._run_mutation(proof, operation)

    def load_generation(self, generation_id: str) -> RuntimeGeneration:
        return self._with_bound_root(
            lambda root: (
                load_generation(
                    self._root,
                    generation_id,
                    root=root,
                    policy=self._generation_policy,
                ).value
            ),
        )

    def write_serving_fence_once(
        self,
        fence: ServingFenceRecord,
        *,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[ServingFenceRecord]:
        payload = _INPUT.encode(
            fence,
            encode_serving_fence_record,
            label="serving fence",
        )

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[ServingFenceRecord]:
            context = self._loader.load_active_context(bound_root=root)
            attempt = load_frozen_attempt(
                self._root,
                scope.snapshot.record.attempt_id,
                context.reservation.value,
                root=root,
                policy=self._attempt_policy,
            )
            generation = load_generation(
                self._root,
                fence.generation_id,
                root=root,
                policy=self._generation_policy,
            )
            require_fence_attempt_generation(
                fence,
                attempt.value,
                generation.value,
            )
            record_sha256 = snapshot(fence, payload).sha256
            path = serving_fence_record_path(
                self._root,
                fence.accepted_attempt_id,
                record_sha256,
            )
            try:
                create_managed_bytes_exclusive_at(
                    path,
                    payload,
                    root=root,
                    policy=self._fence_policy,
                )
            except FileExistsError:
                existing = load_serving_fence(
                    self._root,
                    fence.accepted_attempt_id,
                    record_sha256,
                    root=root,
                    policy=self._fence_policy,
                )
                require_same_bytes(
                    "serving fence",
                    payload,
                    encode_serving_fence_record(existing.value),
                )
                return existing
            return snapshot(fence, payload)

        return self._run_mutation(proof, operation)

    def write_acceptance_once(
        self,
        acceptance: GenerationAcceptance,
        *,
        fence: ServingFenceRecord,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[GenerationAcceptance]:
        from codev_platform.core.runtime_models import canonical_sha256

        payload = _INPUT.encode(
            acceptance,
            encode_generation_acceptance,
            label="acceptance",
        )
        fence = _INPUT.require_type(fence, ServingFenceRecord, label="fence")

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[GenerationAcceptance]:
            context = self._loader.load_active_context(bound_root=root)
            attempt = load_frozen_attempt(
                self._root,
                scope.snapshot.record.attempt_id,
                context.reservation.value,
                root=root,
                policy=self._attempt_policy,
            )
            generation = load_generation(
                self._root,
                acceptance.generation_id,
                root=root,
                policy=self._generation_policy,
            )
            persisted_fence = load_serving_fence(
                self._root,
                fence.accepted_attempt_id,
                canonical_sha256(fence),
                root=root,
                policy=self._fence_policy,
            )
            require_same_bytes(
                "serving fence",
                encode_serving_fence_record(fence),
                encode_serving_fence_record(persisted_fence.value),
            )
            require_acceptance_bindings(
                acceptance,
                persisted_fence.value,
                attempt.value,
                generation.value,
                scope.snapshot.record,
                scope.control_lease_lineage,
            )
            path = acceptance_record_path(self._root, acceptance.attempt_id)
            try:
                create_managed_bytes_exclusive_at(
                    path,
                    payload,
                    root=root,
                    policy=self._acceptance_policy,
                )
            except FileExistsError:
                existing = load_acceptance(
                    self._root,
                    acceptance.attempt_id,
                    root=root,
                    policy=self._acceptance_policy,
                )
                require_same_bytes(
                    "acceptance",
                    payload,
                    encode_generation_acceptance(existing.value),
                )
                return existing
            return snapshot(acceptance, payload)

        return self._run_mutation(proof, operation)

    def write_serving_permit_once(
        self,
        permit: ServingPermitRecord,
        *,
        target_state: GenerationState,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[ServingPermitRecord]:
        target_state = _INPUT.require_type(
            target_state,
            GenerationState,
            label="target_state",
        )
        payload = _INPUT.encode(
            permit,
            encode_serving_permit,
            label="serving permit",
        )

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[ServingPermitRecord]:
            context = self._loader.load_active_context(bound_root=root)
            attempt = load_frozen_attempt(
                self._root,
                scope.snapshot.record.attempt_id,
                context.reservation.value,
                root=root,
                policy=self._attempt_policy,
            )
            current_state = load_generation_state(
                self._root,
                root=root,
                policy=self._state_policy,
            )
            acceptance = load_acceptance(
                self._root,
                permit.attempt_id,
                root=root,
                policy=self._acceptance_policy,
            )
            fence = load_serving_fence(
                self._root,
                permit.attempt_id,
                permit.serving_fence_sha256,
                root=root,
                policy=self._fence_policy,
            )
            generation = load_generation(
                self._root,
                target_state.serving_generation_id,
                root=root,
                policy=self._generation_policy,
            )
            require_acceptance_bindings(
                acceptance.value,
                fence.value,
                attempt.value,
                generation.value,
                scope.snapshot.record,
                scope.control_lease_lineage,
            )
            require_staged_state_current_control(
                current_state.value,
                scope.snapshot.record,
                proof,
                scope.control_lease_lineage,
            )
            require_permit_target_state(
                permit,
                current_state.value,
                target_state,
                acceptance.value,
                fence.value,
            )
            staged_state_sha256 = generation_state_sha256(current_state.value)
            stage_path = serving_permit_stage_path(
                self._root,
                permit.attempt_id,
                staged_state_sha256,
            )
            try:
                create_managed_bytes_exclusive_at(
                    stage_path,
                    payload,
                    root=root,
                    policy=self._permit_policy,
                )
            except FileExistsError:
                staged = load_staged_serving_permit(
                    self._root,
                    permit.attempt_id,
                    staged_state_sha256,
                    root=root,
                    policy=self._permit_policy,
                )
                require_same_bytes(
                    "staged serving permit",
                    payload,
                    encode_serving_permit(staged.value),
                )
            record_sha256 = serving_permit_sha256(permit)
            path = serving_permit_record_path(
                self._root,
                permit.attempt_id,
                record_sha256,
            )
            try:
                create_managed_bytes_exclusive_at(
                    path,
                    payload,
                    root=root,
                    policy=self._permit_policy,
                )
            except FileExistsError:
                existing = load_serving_permit(
                    self._root,
                    permit.attempt_id,
                    record_sha256,
                    root=root,
                    policy=self._permit_policy,
                )
                require_same_bytes(
                    "serving permit",
                    payload,
                    encode_serving_permit(existing.value),
                )
                return existing
            return snapshot(permit, payload)

        return self._run_mutation(proof, operation)

    def load_state(self) -> GenerationStateSnapshot:
        stored = self._with_bound_root(self._state_store.load_state)
        return GenerationStateSnapshot(state=stored.value, sha256=stored.sha256)

    def compare_and_swap_state(
        self,
        expected_sha256: str,
        desired: GenerationState,
        *,
        fence: ServingFenceRecord | None,
        permit: ServingPermitRecord | None,
        proof: ControlLeaseProof,
    ) -> GenerationStateSnapshot:
        desired = _INPUT.require_type(desired, GenerationState, label="desired")
        if fence is not None:
            fence = _INPUT.require_type(fence, ServingFenceRecord, label="fence")
        if permit is not None:
            permit = _INPUT.require_type(permit, ServingPermitRecord, label="permit")

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> GenerationStateSnapshot:
            persisted = self._state_store.compare_and_swap(
                scope,
                root,
                expected_sha256,
                desired,
                fence=fence,
                permit=permit,
                proof=proof,
            )
            return GenerationStateSnapshot(
                state=persisted.value,
                sha256=persisted.sha256,
            )

        return self._run_mutation(proof, operation)

    def _run_mutation(
        self,
        proof: ControlLeaseProof,
        operation: Callable[[RuntimeMutationScope, BoundRuntimeRoot], _Value],
    ) -> _Value:
        try:
            with self._gate.mutation(proof) as scope:
                root = self._require_mutation_scope(scope)
                with self._scope_verifier.hold_verified_mutation(
                    scope,
                    proof,
                    root=root,
                ) as verified_scope:
                    return operation(verified_scope, root)
        except RuntimeGenerationStoreError:
            raise
        except _STORE_ERRORS as error:
            raise RuntimeGenerationStoreError(
                "generation 受控写入无法安全完成",
            ) from error
        except RuntimeError as error:
            raise RuntimeGenerationStoreError(
                "generation 受控写入未获有效活动控制 scope",
            ) from error

    def _with_bound_root(
        self,
        operation: Callable[[BoundRuntimeRoot], _Value],
    ) -> _Value:
        try:
            with self._policy.root_binding.bind() as root:
                self._policy.root_binding.require_bound(root)
                return operation(root)
        except RuntimeGenerationStoreError:
            raise
        except _STORE_ERRORS as error:
            raise RuntimeGenerationStoreError(
                "generation 记录无法安全读取",
            ) from error

    def _require_mutation_scope(self, scope: RuntimeMutationScope) -> BoundRuntimeRoot:
        if type(scope) is not RuntimeMutationScope:
            raise RuntimeGenerationStoreError("gate 未授予有效活动控制 scope")
        try:
            self._policy.root_binding.require_bound(scope.bound_root)
        except RuntimeRootBindingError as error:
            raise RuntimeGenerationStoreError("活动控制 scope 根租约无效") from error
        return scope.bound_root


__all__ = [
    "GenerationStateSnapshot",
    "RuntimeGenerationStore",
    "RuntimeGenerationStoreError",
]
