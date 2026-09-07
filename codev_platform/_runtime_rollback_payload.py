"""回滚受保护载荷的分类、命名空间、权限与 manifest 真值。"""

from __future__ import annotations

import dataclasses
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    require_attempt_id,
    require_sha256,
)
from codev_platform.core.runtime_models import canonical_sha256

MAX_PAYLOADS_PER_CATEGORY = 256
MAX_TOTAL_PAYLOADS = 512
_MAX_RELATIVE_PATH_BYTES = 4095
_MAX_PATH_COMPONENT_BYTES = 255
_MAX_TARGET_KEY_BYTES = 255
_MAX_OWNER_ID = 2**32 - 2
_WINDOWS_DRIVE = re.compile(r"[A-Za-z]:")
_TARGET_KEY = re.compile(r"[a-z0-9._@-]+\Z", re.ASCII)


class RollbackContractError(ValueError):
    """回滚契约字段或序列化载荷不符合要求。"""


class PayloadIdentityKind(StrEnum):
    """载荷身份来源；秘密正文不得使用普通明文 SHA-256。"""

    PUBLIC_SHA256 = "sha256-v1"
    HMAC_SHA256 = "hmac-sha256-v1"
    CIPHERTEXT_SHA256 = "ciphertext-sha256-v1"


class PayloadCategory(StrEnum):
    """受保护载荷在回滚包内的稳定分类。"""

    SYSTEMD = "systemd"
    CONFIGURATION = "configuration"
    RECEIPT = "receipt"


class PayloadLogicalRole(StrEnum):
    """由后续可信 registry 映射目标的受限逻辑角色。"""

    SYSTEMD_UNIT = "systemd-unit"
    SYSTEMD_DROP_IN = "systemd-drop-in"
    SYSTEMD_SOCKET_UNIT = "systemd-socket-unit"
    RUNTIME_CONFIGURATION = "runtime-configuration"
    RUNTIME_ENVIRONMENT = "runtime-environment"
    DEPLOYMENT_RECEIPT = "deployment-receipt"
    GENERATION_RECEIPT = "generation-receipt"


_ROLES_BY_CATEGORY = {
    PayloadCategory.SYSTEMD: frozenset(
        {
            PayloadLogicalRole.SYSTEMD_UNIT,
            PayloadLogicalRole.SYSTEMD_DROP_IN,
            PayloadLogicalRole.SYSTEMD_SOCKET_UNIT,
        }
    ),
    PayloadCategory.CONFIGURATION: frozenset(
        {
            PayloadLogicalRole.RUNTIME_CONFIGURATION,
            PayloadLogicalRole.RUNTIME_ENVIRONMENT,
        }
    ),
    PayloadCategory.RECEIPT: frozenset(
        {
            PayloadLogicalRole.DEPLOYMENT_RECEIPT,
            PayloadLogicalRole.GENERATION_RECEIPT,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ProtectedPayloadRef:
    category: PayloadCategory
    logical_role: PayloadLogicalRole
    target_key: str
    relative_path: str
    identity_kind: PayloadIdentityKind
    identity_sha256: str
    protection_context_sha256: str | None
    mode: int
    uid: int
    gid: int

    def __post_init__(self) -> None:
        if type(self.category) is not PayloadCategory:
            raise RollbackContractError("category 必须是 PayloadCategory")
        if (
            type(self.logical_role) is not PayloadLogicalRole
            or self.logical_role not in _ROLES_BY_CATEGORY[self.category]
        ):
            raise RollbackContractError("logical_role 与载荷分类不一致")
        _require_target_key(self.target_key)
        _require_relative_path(self.relative_path)
        namespace_attempt_id(self.relative_path, self.category)
        if type(self.identity_kind) is not PayloadIdentityKind:
            raise RollbackContractError("identity_kind 必须是 PayloadIdentityKind")
        _require_payload_sha256(self.identity_sha256, "identity_sha256")
        if self.identity_kind is PayloadIdentityKind.PUBLIC_SHA256:
            if self.protection_context_sha256 is not None:
                raise RollbackContractError("公开 SHA 身份不得携带保护上下文")
        else:
            try:
                require_sha256(
                    self.protection_context_sha256,
                    field="protection_context_sha256",
                )
            except RuntimeContractSupportError:
                raise RollbackContractError("秘密身份必须携带规范保护上下文") from None
        if (
            self.category is PayloadCategory.CONFIGURATION
            and self.identity_kind is PayloadIdentityKind.PUBLIC_SHA256
        ):
            raise RollbackContractError("配置载荷身份必须使用 HMAC 或密文摘要")
        _require_category_mode(self.mode, self.category)
        _require_owner_id(self.uid, "uid")
        _require_owner_id(self.gid, "gid")
        if self.uid != 0 or self.gid != 0:
            raise RollbackContractError("受保护载荷 owner 必须是 root:root")


def systemd_receipt_manifest_sha256(
    systemd_payloads: tuple[ProtectedPayloadRef, ...],
    receipt_payloads: tuple[ProtectedPayloadRef, ...],
) -> str:
    """计算排除 attempt 命名空间的 systemd/receipt 权威 manifest。"""
    return canonical_sha256(
        {
            "receipt_payloads": _manifest_projection(
                receipt_payloads,
                "receipt_payloads",
                PayloadCategory.RECEIPT,
            ),
            "schema_version": 1,
            "systemd_payloads": _manifest_projection(
                systemd_payloads,
                "systemd_payloads",
                PayloadCategory.SYSTEMD,
            ),
        }
    )


def configuration_manifest_sha256(
    configuration_payloads: tuple[ProtectedPayloadRef, ...],
) -> str:
    """计算排除 attempt 命名空间的 configuration 权威 manifest。"""
    return canonical_sha256(
        {
            "configuration_payloads": _manifest_projection(
                configuration_payloads,
                "configuration_payloads",
                PayloadCategory.CONFIGURATION,
            ),
            "schema_version": 1,
        }
    )


def sort_payloads(
    value: tuple[ProtectedPayloadRef, ...],
    field: str,
) -> tuple[ProtectedPayloadRef, ...]:
    if type(value) is not tuple or any(type(item) is not ProtectedPayloadRef for item in value):
        raise RollbackContractError(f"{field} 必须是 ProtectedPayloadRef tuple")
    return tuple(sorted(value, key=lambda item: item.relative_path))


def require_payload_group(
    value: object,
    field: str,
    category: PayloadCategory,
    attempt_id: str,
) -> None:
    if type(value) is not tuple or not value:
        raise RollbackContractError(f"{field} 不能为空且必须是 tuple")
    if len(value) > MAX_PAYLOADS_PER_CATEGORY:
        raise RollbackContractError(f"{field} 数量超出上限")
    if any(type(item) is not ProtectedPayloadRef for item in value):
        raise RollbackContractError(f"{field} 载荷类型无效")
    if any(item.category is not category for item in value):
        raise RollbackContractError(f"{field} category 错误")
    if any(
        namespace_attempt_id(item.relative_path, category) != attempt_id
        for item in value
    ):
        raise RollbackContractError(f"{field} 使用了其他 attempt 命名空间")
    paths = tuple(item.relative_path for item in value)
    if paths != tuple(sorted(paths)):
        raise RollbackContractError(f"{field} 必须按 relative_path 稳定排序")
    target_keys = tuple(item.target_key for item in value)
    if len(set(target_keys)) != len(target_keys):
        raise RollbackContractError(f"{field} target_key 重复")


def require_non_conflicting_paths(payloads: tuple[ProtectedPayloadRef, ...]) -> None:
    paths = {item.relative_path for item in payloads}
    if len(paths) != len(payloads):
        raise RollbackContractError("受保护载荷路径重复或存在前缀冲突")
    for path in paths:
        parts = path.split("/")
        if any("/".join(parts[:end]) in paths for end in range(1, len(parts))):
            raise RollbackContractError("受保护载荷路径重复或存在前缀冲突")


def protected_payload_from_mapping(value: object) -> ProtectedPayloadRef:
    expected = {field.name for field in dataclasses.fields(ProtectedPayloadRef)}
    if type(value) is not dict or set(value) != expected:
        raise RollbackContractError("ProtectedPayloadRef 字段集合不匹配")
    values = dict(value)
    category = values.get("category")
    logical_role = values.get("logical_role")
    kind = values.get("identity_kind")
    if any(type(item) is not str for item in (category, logical_role, kind)):
        raise RollbackContractError("category/logical_role/identity_kind 类型无效")
    try:
        values["category"] = PayloadCategory(category)
        values["logical_role"] = PayloadLogicalRole(logical_role)
        values["identity_kind"] = PayloadIdentityKind(kind)
        return ProtectedPayloadRef(**values)
    except (TypeError, ValueError):
        raise RollbackContractError("ProtectedPayloadRef 数据无效") from None


def namespace_attempt_id(value: str, category: PayloadCategory) -> str:
    parts = value.split("/")
    if (
        len(parts) < 5
        or parts[0] != "rollback-bundles"
        or parts[2] != "protected"
        or parts[3] != category.value
    ):
        raise RollbackContractError("relative_path 不在 attempt/category 受保护命名空间")
    try:
        return require_attempt_id(parts[1])
    except RuntimeContractSupportError as exc:
        raise RollbackContractError(str(exc)) from None


def _require_relative_path(value: object) -> None:
    if (
        type(value) is not str
        or not value
        or value.startswith("/")
        or _WINDOWS_DRIVE.match(value) is not None
    ):
        raise RollbackContractError("relative_path 必须是 runtime root 内相对路径")
    if "\\" in value or any(unicodedata.category(character) == "Cc" for character in value):
        raise RollbackContractError("relative_path 包含禁止字符")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise RollbackContractError("relative_path 不是有效 UTF-8 路径") from None
    parts = value.split("/")
    if (
        len(encoded) > _MAX_RELATIVE_PATH_BYTES
        or any(part in {"", ".", ".."} for part in parts)
        or any(len(part.encode("utf-8")) > _MAX_PATH_COMPONENT_BYTES for part in parts)
    ):
        raise RollbackContractError("relative_path 不规范或超出长度上限")


def _require_target_key(value: object) -> None:
    if (
        type(value) is not str
        or value in {"", ".", ".."}
        or len(value) > _MAX_TARGET_KEY_BYTES
        or _TARGET_KEY.fullmatch(value) is None
    ):
        raise RollbackContractError("target_key 必须是受限 ASCII registry 身份")


def _require_payload_sha256(value: object, field: str) -> None:
    try:
        require_sha256(value, field=field)
    except RuntimeContractSupportError:
        raise RollbackContractError(f"{field} 必须是规范 SHA-256") from None


def _require_category_mode(value: object, category: PayloadCategory) -> None:
    allowed = {
        PayloadCategory.SYSTEMD: frozenset({0o644}),
        PayloadCategory.CONFIGURATION: frozenset({0o600}),
        PayloadCategory.RECEIPT: frozenset({0o600}),
    }[category]
    if type(value) is not int or value not in allowed:
        rendered = "/".join(f"{mode:04o}" for mode in sorted(allowed))
        raise RollbackContractError(f"{category.value} mode 只允许 {rendered}")


def _require_owner_id(value: object, field: str) -> None:
    if type(value) is not int or not 0 <= value <= _MAX_OWNER_ID:
        raise RollbackContractError(f"{field} 必须是平台范围内非负整数")


def _manifest_projection(
    value: tuple[ProtectedPayloadRef, ...],
    field: str,
    category: PayloadCategory,
) -> tuple[dict[str, object], ...]:
    if type(value) is not tuple or any(type(item) is not ProtectedPayloadRef for item in value):
        raise RollbackContractError(f"{field} 必须是 ProtectedPayloadRef tuple")
    if any(item.category is not category for item in value):
        raise RollbackContractError(f"{field} category 错误")
    projected = tuple(
        {
            "category": item.category.value,
            "gid": item.gid,
            "identity_kind": item.identity_kind.value,
            "identity_sha256": item.identity_sha256,
            "logical_role": item.logical_role.value,
            "mode": item.mode,
            "protected_name": "/".join(item.relative_path.split("/")[4:]),
            "protection_context_sha256": item.protection_context_sha256,
            "target_key": item.target_key,
            "uid": item.uid,
        }
        for item in value
    )
    return tuple(
        sorted(
            projected,
            key=lambda item: (str(item["target_key"]), str(item["protected_name"])),
        )
    )


__all__ = [
    "MAX_TOTAL_PAYLOADS",
    "PayloadCategory",
    "PayloadIdentityKind",
    "PayloadLogicalRole",
    "ProtectedPayloadRef",
    "RollbackContractError",
    "configuration_manifest_sha256",
    "protected_payload_from_mapping",
    "require_non_conflicting_paths",
    "require_payload_group",
    "sort_payloads",
    "systemd_receipt_manifest_sha256",
]
