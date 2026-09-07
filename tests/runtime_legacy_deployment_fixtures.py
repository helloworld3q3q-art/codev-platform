"""只由 schema 1 审计对象构造且身份一致的历史部署夹具。"""

from __future__ import annotations

from codev_platform.runtime_deployment_contract import (
    DEPLOYMENT_PHASES,
    LegacyDeploymentPlanAudit,
    LegacyDeploymentReceiptAudit,
    decode_legacy_deployment_plan_audit,
)


LEGACY_PLAN_GOLDEN_SHA256 = "a367d6301b8ed2fbf14f2c75ebc347735f9f17e4ce4a869978e289cb9b6dec08"
LEGACY_PLAN_GOLDEN_MAPPING: dict[str, object] = {
    "approved_requirements": "/srv/codev-artifacts/wsl-runtime.freeze",
    "baseline_revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "candidate_root": "/var/lib/codev-platform/runtime/candidates",
    "config_source": "/home/helloworld/.codev-platform/config.json",
    "environment_source": "/etc/codev-platform/platform.source.env",
    "project_id": "codev-platform",
    "require_cuda": True,
    "requirements_lock": "/srv/codev-artifacts/wsl-runtime.lock",
    "runtime_root": "/var/lib/codev-platform/runtime",
    "schema_version": 1,
    "service_user": "helloworld",
    "source_repo": "/home/helloworld/work/codev-platform",
    "target_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "wheelhouse": "/srv/codev-artifacts/wheelhouse",
}
LEGACY_PLAN_GOLDEN_JSON = (
    b'{"approved_requirements":"/srv/codev-artifacts/wsl-runtime.freeze",'
    b'"baseline_revision":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
    b'"candidate_root":"/var/lib/codev-platform/runtime/candidates",'
    b'"config_source":"/home/helloworld/.codev-platform/config.json",'
    b'"environment_source":"/etc/codev-platform/platform.source.env",'
    b'"project_id":"codev-platform","require_cuda":true,'
    b'"requirements_lock":"/srv/codev-artifacts/wsl-runtime.lock",'
    b'"runtime_root":"/var/lib/codev-platform/runtime","schema_version":1,'
    b'"service_user":"helloworld",'
    b'"source_repo":"/home/helloworld/work/codev-platform",'
    b'"target_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    b'"wheelhouse":"/srv/codev-artifacts/wheelhouse"}'
)
LEGACY_RECEIPT_GOLDEN_MAPPING: dict[str, object] = {
    "base_id": None,
    "baseline_release_id": None,
    "baseline_revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "completed_phases": [],
    "failed_phase": None,
    "last_evidence_sha256": None,
    "plan_sha256": LEGACY_PLAN_GOLDEN_SHA256,
    "previous_release_id": None,
    "release_id": None,
    "schema_version": 1,
    "status": "running",
    "target_revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "updated_at": "2026-07-19T10:00:00Z",
}
LEGACY_RECEIPT_GOLDEN_JSON = (
    b'{"base_id":null,"baseline_release_id":null,'
    b'"baseline_revision":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
    b'"completed_phases":[],"failed_phase":null,"last_evidence_sha256":null,'
    b'"plan_sha256":"a367d6301b8ed2fbf14f2c75ebc347735f9f17e4ce4a869978e289cb9b6dec08",'
    b'"previous_release_id":null,"release_id":null,"schema_version":1,'
    b'"status":"running",'
    b'"target_revision":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
    b'"updated_at":"2026-07-19T10:00:00Z"}'
)


def legacy_plan_audit() -> LegacyDeploymentPlanAudit:
    return decode_legacy_deployment_plan_audit(LEGACY_PLAN_GOLDEN_JSON)


def legacy_receipt_audit(
    plan: LegacyDeploymentPlanAudit,
    *,
    completed: int = 0,
    status: str = "running",
    failed_phase: str | None = None,
    base_id: str | None = None,
    release_id: str | None = None,
    baseline_release_id: str | None = None,
    previous_release_id: str | None = None,
    last_evidence_sha256: str | None = None,
) -> LegacyDeploymentReceiptAudit:
    if status in {"failed_safe", "safety_unproven"} and failed_phase is None:
        failed_phase = DEPLOYMENT_PHASES[completed].value
    return LegacyDeploymentReceiptAudit(
        schema_version=1,
        plan_sha256=plan.digest,
        target_revision=plan.target_revision,
        baseline_revision=plan.baseline_revision,
        status=status,  # type: ignore[arg-type]
        completed_phases=tuple(phase.value for phase in DEPLOYMENT_PHASES[:completed]),
        base_id=base_id,
        release_id=release_id,
        baseline_release_id=baseline_release_id,
        previous_release_id=previous_release_id,
        last_evidence_sha256=last_evidence_sha256,
        failed_phase=failed_phase,
        updated_at="2026-07-19T10:00:00Z",
    )


__all__ = [
    "LEGACY_PLAN_GOLDEN_JSON",
    "LEGACY_PLAN_GOLDEN_MAPPING",
    "LEGACY_PLAN_GOLDEN_SHA256",
    "LEGACY_RECEIPT_GOLDEN_JSON",
    "LEGACY_RECEIPT_GOLDEN_MAPPING",
    "legacy_plan_audit",
    "legacy_receipt_audit",
]
