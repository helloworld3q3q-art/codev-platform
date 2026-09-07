"""正式 WSL 部署计划、阶段与持久回执的纯领域契约。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, NoReturn

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_schema,
    require_sha256,
    require_utc_rfc3339_z,
)
from codev_platform.core.runtime_models import (
    canonical_json_bytes,
    canonical_sha256,
    require_runtime_revision,
)
from codev_platform.core import project_id as core_project_id


PRODUCTION_GIT_REMOTE = "fuwuqi"
PRODUCTION_GIT_BRANCH = "dev"
PRODUCTION_RUNTIME_ROOT = "/var/lib/codev-platform/runtime"
_POSIX_ABSOLUTE = re.compile(r"/(?:[A-Za-z0-9._+-]+/)*[A-Za-z0-9._+-]+\Z")
_SERVICE_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_LEGACY_PROJECT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")
_MAX_DEPLOYMENT_CONTRACT_BYTES = 32_768
_PLAN_FIELDS = frozenset(
    {
        "schema_version",
        "target_revision",
        "source_repo",
        "runtime_root",
        "requirements_lock",
        "approved_requirements",
        "wheelhouse",
        "candidate_root",
        "config_source",
        "environment_source",
        "service_user",
        "project_id",
        "require_cuda",
        "legacy_takeover_policy_sha256",
    }
)
_LEGACY_PLAN_FIELDS = (_PLAN_FIELDS - {"legacy_takeover_policy_sha256"}) | {
    "baseline_revision"
}
_LEGACY_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "plan_sha256",
        "target_revision",
        "baseline_revision",
        "status",
        "completed_phases",
        "base_id",
        "release_id",
        "baseline_release_id",
        "previous_release_id",
        "last_evidence_sha256",
        "failed_phase",
        "updated_at",
    }
)


class DeploymentPhase(StrEnum):
    """每个枚举值都代表一个已经原子提交、可以安全续跑的边界。"""

    TARGET_VERIFIED = "target_verified"
    ARTIFACTS_VERIFIED = "artifacts_verified"
    RELEASE_STAGED = "release_staged"
    CONFIG_STAGED = "config_staged"
    MAINTENANCE_ENTERED = "maintenance_entered"
    CONFIG_PUBLISHED = "config_published"
    DATABASE_MIGRATED = "database_migrated"
    SYSTEMD_STAGED = "systemd_staged"
    INDEXES_REBUILT = "indexes_rebuilt"
    ACCEPTED = "accepted"
    INGRESS_OPENED = "ingress_opened"


DEPLOYMENT_PHASES = tuple(DeploymentPhase)
DeploymentStatus = Literal["running", "failed_safe", "safety_unproven", "complete"]


class RuntimeDeploymentError(RuntimeError):
    """部署不能继续，且异常正文不得携带命令、路径内容或秘密。"""


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    """调用方明确批准的生产部署输入；不保存 DSN、令牌或配置正文。"""

    schema_version: int
    target_revision: str
    source_repo: str
    runtime_root: str
    requirements_lock: str
    approved_requirements: str
    wheelhouse: str
    candidate_root: str
    config_source: str
    environment_source: str
    service_user: str
    project_id: str
    require_cuda: bool
    legacy_takeover_policy_sha256: str | None = None

    def __post_init__(self) -> None:
        try:
            require_schema(self.schema_version, 2)
        except RuntimeContractSupportError:
            raise RuntimeDeploymentError("部署计划版本无效") from None
        try:
            require_runtime_revision(self.target_revision, git_only=True)
        except ValueError:
            raise RuntimeDeploymentError("部署提交必须是完整 40 位小写 OID") from None
        _require_plan_environment(self)
        _require_v2_project_id(self.project_id)
        if self.legacy_takeover_policy_sha256 is not None:
            try:
                require_sha256(
                    self.legacy_takeover_policy_sha256,
                    field="legacy_takeover_policy_sha256",
                )
            except RuntimeContractSupportError:
                raise RuntimeDeploymentError("遗留接管策略摘要无效") from None

    @property
    def digest(self) -> str:
        return canonical_sha256(self.to_mapping())

    @property
    def baseline_revision(self) -> str:
        """旧执行层若误收 schema 2，必须受控失败而不是属性错误。"""
        raise RuntimeDeploymentError("schema 2 部署计划不提供 baseline_revision")

    def to_mapping(self) -> dict[str, object]:
        return {
            "approved_requirements": self.approved_requirements,
            "candidate_root": self.candidate_root,
            "config_source": self.config_source,
            "environment_source": self.environment_source,
            "legacy_takeover_policy_sha256": self.legacy_takeover_policy_sha256,
            "project_id": self.project_id,
            "require_cuda": self.require_cuda,
            "requirements_lock": self.requirements_lock,
            "runtime_root": self.runtime_root,
            "schema_version": self.schema_version,
            "service_user": self.service_user,
            "source_repo": self.source_repo,
            "target_revision": self.target_revision,
            "wheelhouse": self.wheelhouse,
        }


@dataclass(frozen=True, slots=True)
class LegacyDeploymentPlanAudit:
    """旧 schema 1 计划的只读审计视图，不能进入生产执行入口。"""

    schema_version: int
    target_revision: str
    baseline_revision: str | None
    source_repo: str
    runtime_root: str
    requirements_lock: str
    approved_requirements: str
    wheelhouse: str
    candidate_root: str
    config_source: str
    environment_source: str
    service_user: str
    project_id: str
    require_cuda: bool

    def __post_init__(self) -> None:
        try:
            require_schema(self.schema_version, 1)
        except RuntimeContractSupportError:
            raise RuntimeDeploymentError("旧部署计划审计版本无效") from None
        try:
            require_runtime_revision(self.target_revision, git_only=True)
            if self.baseline_revision is not None:
                require_runtime_revision(self.baseline_revision, git_only=True)
        except ValueError:
            raise RuntimeDeploymentError("旧部署计划提交身份无效") from None
        if self.baseline_revision == self.target_revision:
            raise RuntimeDeploymentError("旧部署计划基线不能等于目标提交")
        _require_plan_environment(self)
        _require_v1_project_id(self.project_id)

    @property
    def digest(self) -> str:
        return canonical_sha256(self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "approved_requirements": self.approved_requirements,
            "baseline_revision": self.baseline_revision,
            "candidate_root": self.candidate_root,
            "config_source": self.config_source,
            "environment_source": self.environment_source,
            "project_id": self.project_id,
            "require_cuda": self.require_cuda,
            "requirements_lock": self.requirements_lock,
            "runtime_root": self.runtime_root,
            "schema_version": self.schema_version,
            "service_user": self.service_user,
            "source_repo": self.source_repo,
            "target_revision": self.target_revision,
            "wheelhouse": self.wheelhouse,
        }


@dataclass(frozen=True, slots=True)
class DeploymentUpdate:
    """单阶段只允许补充内容身份，禁止把任意日志或秘密塞入回执。"""

    base_id: str | None = None
    release_id: str | None = None
    baseline_release_id: str | None = None
    previous_release_id: str | None = None
    evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        for field in (
            "base_id",
            "release_id",
            "baseline_release_id",
            "previous_release_id",
            "evidence_sha256",
        ):
            value = getattr(self, field)
            if value is not None:
                try:
                    require_sha256(value, field=field)
                except RuntimeContractSupportError:
                    raise RuntimeDeploymentError("部署阶段身份无效") from None


@dataclass(frozen=True, slots=True)
class LegacyDeploymentReceiptAudit:
    """旧 schema 1 回执的只读审计视图。"""

    schema_version: int
    plan_sha256: str
    target_revision: str
    baseline_revision: str | None
    status: DeploymentStatus
    completed_phases: tuple[str, ...]
    base_id: str | None
    release_id: str | None
    baseline_release_id: str | None
    previous_release_id: str | None
    last_evidence_sha256: str | None
    failed_phase: str | None
    updated_at: str

    def __post_init__(self) -> None:
        try:
            require_schema(self.schema_version, 1)
        except RuntimeContractSupportError:
            raise RuntimeDeploymentError("部署回执版本无效") from None
        try:
            require_sha256(self.plan_sha256, field="plan_sha256")
            require_runtime_revision(self.target_revision, git_only=True)
            if self.baseline_revision is not None:
                require_runtime_revision(self.baseline_revision, git_only=True)
        except ValueError:
            raise RuntimeDeploymentError("部署回执身份无效") from None
        if type(self.status) is not str or self.status not in {
            "running",
            "failed_safe",
            "safety_unproven",
            "complete",
        }:
            raise RuntimeDeploymentError("部署回执状态无效")
        if type(self.completed_phases) is not tuple or any(
            type(phase) is not str for phase in self.completed_phases
        ):
            raise RuntimeDeploymentError("部署回执阶段无效")
        expected = tuple(phase.value for phase in DEPLOYMENT_PHASES[: len(self.completed_phases)])
        if self.completed_phases != expected:
            raise RuntimeDeploymentError("部署回执阶段不是规范前缀")
        if self.status == "complete" and len(self.completed_phases) != len(DEPLOYMENT_PHASES):
            raise RuntimeDeploymentError("完成回执缺少阶段")
        if self.failed_phase is not None and (
            type(self.failed_phase) is not str
            or self.failed_phase not in {phase.value for phase in DEPLOYMENT_PHASES}
        ):
            raise RuntimeDeploymentError("部署失败阶段无效")
        failed_status = self.status in {"failed_safe", "safety_unproven"}
        if failed_status:
            next_phase = (
                DEPLOYMENT_PHASES[len(self.completed_phases)].value
                if len(self.completed_phases) < len(DEPLOYMENT_PHASES)
                else None
            )
            if next_phase is None or self.failed_phase != next_phase:
                raise RuntimeDeploymentError("部署回执状态与失败阶段不一致")
        elif self.failed_phase is not None:
            raise RuntimeDeploymentError("部署回执状态不允许失败阶段")
        for field in (
            "base_id",
            "release_id",
            "baseline_release_id",
            "previous_release_id",
            "last_evidence_sha256",
        ):
            value = getattr(self, field)
            if value is not None:
                try:
                    require_sha256(value, field=field)
                except RuntimeContractSupportError:
                    raise RuntimeDeploymentError("部署回执摘要无效") from None
        _require_utc_timestamp(self.updated_at, "updated_at")

    @classmethod
    def start(cls, plan: DeploymentPlan, *, now: str) -> LegacyDeploymentReceiptAudit:
        del plan, now
        raise RuntimeDeploymentError("旧 schema 1 部署回执仅允许只读审计")

    def apply(
        self,
        phase: DeploymentPhase,
        update: DeploymentUpdate,
        *,
        now: str,
    ) -> LegacyDeploymentReceiptAudit:
        del phase, update, now
        raise RuntimeDeploymentError("旧 schema 1 部署回执仅允许只读审计")

    def to_mapping(self) -> dict[str, object]:
        return {
            "base_id": self.base_id,
            "baseline_release_id": self.baseline_release_id,
            "baseline_revision": self.baseline_revision,
            "completed_phases": list(self.completed_phases),
            "failed_phase": self.failed_phase,
            "last_evidence_sha256": self.last_evidence_sha256,
            "plan_sha256": self.plan_sha256,
            "previous_release_id": self.previous_release_id,
            "release_id": self.release_id,
            "schema_version": self.schema_version,
            "status": self.status,
            "target_revision": self.target_revision,
            "updated_at": self.updated_at,
        }


# 保留旧导入名仅用于读取历史记录；该类型没有任何写入转换能力。
DeploymentReceipt = LegacyDeploymentReceiptAudit


def encode_deployment_plan(value: DeploymentPlan) -> bytes:
    """编码 schema 2 生产计划。"""
    if type(value) is not DeploymentPlan:
        raise RuntimeDeploymentError("只接受 DeploymentPlan")
    return canonical_json_bytes(value.to_mapping())


def decode_deployment_plan(payload: bytes) -> DeploymentPlan:
    """严格解码 schema 2 生产计划，V1 只能走审计边界。"""
    values = _decode_deployment_mapping(
        payload,
        required_fields=_PLAN_FIELDS - {"legacy_takeover_policy_sha256"},
        optional_defaults={"legacy_takeover_policy_sha256": None},
        label="生产部署计划",
    )
    try:
        return DeploymentPlan(**values)
    except (TypeError, ValueError):
        raise RuntimeDeploymentError("生产部署计划载荷无效") from None


def decode_legacy_deployment_plan_audit(payload: bytes) -> LegacyDeploymentPlanAudit:
    """严格解码旧计划，只返回不能进入生产入口的审计类型。"""
    values = _decode_deployment_mapping(
        payload,
        required_fields=frozenset(_LEGACY_PLAN_FIELDS),
        optional_defaults={},
        label="旧部署计划审计",
    )
    try:
        return LegacyDeploymentPlanAudit(**values)
    except (TypeError, ValueError):
        raise RuntimeDeploymentError("旧部署计划审计载荷无效") from None


def decode_legacy_deployment_receipt_audit(payload: bytes) -> LegacyDeploymentReceiptAudit:
    """严格解码旧回执，只返回冻结的审计类型。"""
    values = _decode_deployment_mapping(
        payload,
        required_fields=_LEGACY_RECEIPT_FIELDS,
        optional_defaults={},
        label="旧部署回执审计",
    )
    try:
        phases = values.get("completed_phases")
        if type(phases) is not list or any(type(item) is not str for item in phases):
            raise RuntimeDeploymentError("旧部署回执阶段无效")
        values["completed_phases"] = tuple(phases)
        return LegacyDeploymentReceiptAudit(**values)
    except (TypeError, ValueError):
        raise RuntimeDeploymentError("旧部署回执审计载荷无效") from None


def reject_legacy_deployment_pipeline(plan: object) -> NoReturn:
    """旧 receipt/coordinator 不能消费 V2，也不能执行 V1 审计对象。"""
    if type(plan) is DeploymentPlan:
        raise RuntimeDeploymentError("schema 2 计划拒绝进入旧部署回执管线")
    raise RuntimeDeploymentError("旧 schema 1 计划只允许审计，不能进入生产部署")


def _decode_deployment_mapping(
    payload: bytes,
    *,
    required_fields: frozenset[str],
    optional_defaults: dict[str, object],
    label: str,
) -> dict[str, object]:
    try:
        return decode_exact_json_mapping(
            payload,
            required_fields=required_fields,
            optional_defaults=optional_defaults,
            max_bytes=_MAX_DEPLOYMENT_CONTRACT_BYTES,
        )
    except RuntimeContractSupportError:
        raise RuntimeDeploymentError(f"{label}载荷无效") from None


def _require_posix_path(value: object, field: str) -> str:
    if type(value) is not str or _POSIX_ABSOLUTE.fullmatch(value) is None:
        raise RuntimeDeploymentError(f"{field} 必须是规范 POSIX 绝对路径")
    if "//" in value or "/./" in value or "/../" in value or value.endswith(("/.", "/..")):
        raise RuntimeDeploymentError(f"{field} 必须是规范 POSIX 绝对路径")
    return value


def _require_plan_environment(plan: DeploymentPlan | LegacyDeploymentPlanAudit) -> None:
    for field in (
        "source_repo",
        "requirements_lock",
        "approved_requirements",
        "wheelhouse",
        "candidate_root",
        "config_source",
        "environment_source",
    ):
        _require_posix_path(getattr(plan, field), field)
    if plan.runtime_root != PRODUCTION_RUNTIME_ROOT:
        raise RuntimeDeploymentError("正式运行时根必须使用固定生产路径")
    if (
        type(plan.service_user) is not str
        or _SERVICE_USER.fullmatch(plan.service_user) is None
        or plan.service_user == "root"
    ):
        raise RuntimeDeploymentError("服务账号必须是非特权账号")
    if type(plan.require_cuda) is not bool:
        raise RuntimeDeploymentError("CUDA 门禁必须是布尔值")


def _require_v2_project_id(value: object) -> None:
    if type(value) is not str:
        raise RuntimeDeploymentError("项目标识无效")
    try:
        canonical_project_id = core_project_id.validate(value)
    except core_project_id.ProjectIdError:
        raise RuntimeDeploymentError("项目标识无效") from None
    if canonical_project_id != value:
        raise RuntimeDeploymentError("项目标识必须是规范精确值")


def _require_v1_project_id(value: object) -> None:
    if type(value) is not str or _LEGACY_PROJECT_ID.fullmatch(value) is None:
        raise RuntimeDeploymentError("旧项目标识无效")


def _require_utc_timestamp(value: object, field: str) -> None:
    try:
        require_utc_rfc3339_z(value, field=field)
    except RuntimeContractSupportError as exc:
        raise RuntimeDeploymentError(str(exc)) from None


__all__ = [
    "DEPLOYMENT_PHASES",
    "PRODUCTION_GIT_BRANCH",
    "PRODUCTION_GIT_REMOTE",
    "PRODUCTION_RUNTIME_ROOT",
    "DeploymentPhase",
    "DeploymentPlan",
    "DeploymentReceipt",
    "DeploymentUpdate",
    "LegacyDeploymentPlanAudit",
    "LegacyDeploymentReceiptAudit",
    "RuntimeDeploymentError",
    "decode_deployment_plan",
    "decode_legacy_deployment_plan_audit",
    "decode_legacy_deployment_receipt_audit",
    "encode_deployment_plan",
    "reject_legacy_deployment_pipeline",
]
