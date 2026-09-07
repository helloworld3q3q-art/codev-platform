"""回滚契约测试共享的已验证对象夹具。"""

from __future__ import annotations

from dataclasses import dataclass

from codev_platform.runtime_attempt_contract import AttemptOperation, DeploymentAttempt
from codev_platform.runtime_generation_contract import (
    GenerationKind,
    RuntimeGeneration,
    create_runtime_generation,
)
from codev_platform.runtime_rollback_contract import (
    BaselineObservation,
    PayloadCategory,
    PayloadIdentityKind,
    PayloadLogicalRole,
    ProtectedPayloadRef,
    RollbackBundle,
    baseline_observation_sha256,
    configuration_manifest_sha256,
    create_rollback_bundle,
    systemd_receipt_manifest_sha256,
)

ATTEMPT_ID = "1" * 32


def protected_ref(
    category: PayloadCategory,
    role: PayloadLogicalRole,
    name: str,
    *,
    target_key: str | None = None,
    attempt_id: str = ATTEMPT_ID,
    identity_sha256: str = "a" * 64,
    identity_kind: PayloadIdentityKind | None = None,
    protection_context_sha256: str | None = None,
    mode: int | None = None,
    uid: int = 0,
    gid: int = 0,
) -> ProtectedPayloadRef:
    if target_key is None:
        target_key = name.replace("/", "@")
    if identity_kind is None:
        identity_kind = PayloadIdentityKind.PUBLIC_SHA256
    if mode is None:
        mode = 0o644 if category is PayloadCategory.SYSTEMD else 0o600
    return ProtectedPayloadRef(
        category=category,
        logical_role=role,
        target_key=target_key,
        relative_path=(
            f"rollback-bundles/{attempt_id}/protected/{category.value}/{name}"
        ),
        identity_kind=identity_kind,
        identity_sha256=identity_sha256,
        protection_context_sha256=protection_context_sha256,
        mode=mode,
        uid=uid,
        gid=gid,
    )


def protected_groups(
    *,
    attempt_id: str = ATTEMPT_ID,
) -> tuple[
    tuple[ProtectedPayloadRef, ...],
    tuple[ProtectedPayloadRef, ...],
    tuple[ProtectedPayloadRef, ...],
]:
    return (
        (
            protected_ref(
                PayloadCategory.SYSTEMD,
                PayloadLogicalRole.SYSTEMD_UNIT,
                "codev-platform.service",
                attempt_id=attempt_id,
            ),
        ),
        (
            protected_ref(
                PayloadCategory.CONFIGURATION,
                PayloadLogicalRole.RUNTIME_CONFIGURATION,
                "runtime.env.enc",
                attempt_id=attempt_id,
                identity_kind=PayloadIdentityKind.CIPHERTEXT_SHA256,
                protection_context_sha256="b" * 64,
            ),
        ),
        (
            protected_ref(
                PayloadCategory.RECEIPT,
                PayloadLogicalRole.DEPLOYMENT_RECEIPT,
                "deployment.json",
                attempt_id=attempt_id,
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class BoundRollbackFacts:
    attempt: DeploymentAttempt
    generation: RuntimeGeneration
    observation: BaselineObservation
    systemd: tuple[ProtectedPayloadRef, ...]
    configuration: tuple[ProtectedPayloadRef, ...]
    receipts: tuple[ProtectedPayloadRef, ...]


PayloadGroups = tuple[
    tuple[ProtectedPayloadRef, ...],
    tuple[ProtectedPayloadRef, ...],
    tuple[ProtectedPayloadRef, ...],
]


def oversized_payload_groups() -> PayloadGroups:
    shared_path = "/".join(["p" * 255] * 15)

    def name(index: int) -> str:
        return f"{shared_path}/{index:03d}"

    systemd = tuple(
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_UNIT,
            name(index),
            target_key=f"unit-{index:03d}",
        )
        for index in range(256)
    )
    configuration = tuple(
        protected_ref(
            PayloadCategory.CONFIGURATION,
            PayloadLogicalRole.RUNTIME_CONFIGURATION,
            name(index),
            target_key=f"config-{index:03d}",
            identity_kind=PayloadIdentityKind.CIPHERTEXT_SHA256,
            protection_context_sha256="b" * 64,
        )
        for index in range(255)
    )
    receipts = (
        protected_ref(
            PayloadCategory.RECEIPT,
            PayloadLogicalRole.DEPLOYMENT_RECEIPT,
            name(0),
            target_key="receipt-000",
        ),
    )
    return systemd, configuration, receipts


def boundary_payload_groups(extra_path_bytes: int) -> PayloadGroups:
    if type(extra_path_bytes) is not int or not 0 <= extra_path_bytes <= 256 * 252:
        raise ValueError("extra_path_bytes 超出测试路径填充范围")
    shared_path = "/".join(["b" * 247] * 15)
    remaining = extra_path_bytes
    names: list[str] = []
    for index in range(256):
        padding = min(remaining, 252)
        names.append(f"{shared_path}/{index:03d}{'q' * padding}")
        remaining -= padding
    systemd = tuple(
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_UNIT,
            name,
            target_key=f"unit-{index:03d}",
        )
        for index, name in enumerate(names)
    )
    configuration = (
        protected_ref(
            PayloadCategory.CONFIGURATION,
            PayloadLogicalRole.RUNTIME_CONFIGURATION,
            f"{shared_path}/000",
            target_key="config-000",
            identity_kind=PayloadIdentityKind.CIPHERTEXT_SHA256,
            protection_context_sha256="b" * 64,
        ),
    )
    receipts = (
        protected_ref(
            PayloadCategory.RECEIPT,
            PayloadLogicalRole.DEPLOYMENT_RECEIPT,
            f"{shared_path}/000",
            target_key="receipt-000",
        ),
    )
    return systemd, configuration, receipts


def bound_facts() -> BoundRollbackFacts:
    return bound_facts_for_payloads(*protected_groups())


def bound_facts_for_payloads(
    systemd: tuple[ProtectedPayloadRef, ...],
    configuration: tuple[ProtectedPayloadRef, ...],
    receipts: tuple[ProtectedPayloadRef, ...],
) -> BoundRollbackFacts:
    generation = create_runtime_generation(
        revision="a" * 40,
        release_id="c" * 64,
        base_id="d" * 64,
        entrypoint_contract_sha256="e" * 64,
        systemd_bundle_sha256=systemd_receipt_manifest_sha256(systemd, receipts),
        configuration_bundle_sha256=configuration_manifest_sha256(configuration),
        database_contract_sha256="f" * 64,
        index_set_sha256="2" * 64,
        kind=GenerationKind.MANAGED,
        created_at="2026-07-19T09:00:00Z",
    )
    observation = BaselineObservation(
        schema_version=1,
        serving_generation_id=generation.generation_id,
        generation_state_sha256="3" * 64,
        generation_acceptance_sha256="4" * 64,
        serve_permit_sha256="5" * 64,
        main_pid=1234,
        interpreter_identity_sha256="6" * 64,
        systemd_state_sha256=generation.systemd_bundle_sha256,
        configuration_state_sha256=generation.configuration_bundle_sha256,
        database_state_sha256="7" * 64,
        index_set_sha256=generation.index_set_sha256,
        observed_at="2026-07-19T10:00:00Z",
    )
    attempt = DeploymentAttempt(
        schema_version=1,
        attempt_id=ATTEMPT_ID,
        operation=AttemptOperation.DEPLOY,
        target_generation_id="8" * 64,
        baseline_generation_id=generation.generation_id,
        baseline_observation_sha256=baseline_observation_sha256(observation),
        plan_sha256="9" * 64,
        controller_sha256="a" * 64,
        created_at="2026-07-19T10:01:00Z",
    )
    return BoundRollbackFacts(
        attempt,
        generation,
        observation,
        systemd,
        configuration,
        receipts,
    )


def rollback_bundle_candidate_mapping(
    facts: BoundRollbackFacts,
) -> dict[str, object]:
    return {
        "attempt_id": facts.attempt.attempt_id,
        "baseline_observation_sha256": facts.attempt.baseline_observation_sha256,
        "configuration_payloads": facts.configuration,
        "created_at": "2026-07-19T10:02:00Z",
        "database_compatibility_sha256": facts.generation.database_contract_sha256,
        "index_set_sha256": facts.generation.index_set_sha256,
        "original_serving_generation_id": facts.generation.generation_id,
        "plan_sha256": facts.attempt.plan_sha256,
        "receipt_payloads": facts.receipts,
        "schema_version": 1,
        "systemd_payloads": facts.systemd,
    }


def bound_bundle() -> RollbackBundle:
    facts = bound_facts()
    return create_rollback_bundle(
        attempt=facts.attempt,
        baseline_generation=facts.generation,
        baseline_observation=facts.observation,
        systemd_payloads=facts.systemd,
        configuration_payloads=facts.configuration,
        receipt_payloads=facts.receipts,
        created_at="2026-07-19T10:02:00Z",
    )
