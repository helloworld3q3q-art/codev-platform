"""运行代际的不可变身份契约。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_schema,
    require_sha256,
    require_utc_rfc3339_z,
)
from codev_platform.core.runtime_models import (
    ReleaseMetadata,
    canonical_json_bytes,
    canonical_sha256,
    compute_release_id,
    require_runtime_revision,
)


_MAX_GENERATION_BYTES = 32_768
_GENERATION_FIELDS = frozenset(
    {
        "schema_version",
        "generation_id",
        "revision",
        "release_id",
        "base_id",
        "entrypoint_contract_sha256",
        "systemd_bundle_sha256",
        "configuration_bundle_sha256",
        "database_contract_sha256",
        "index_set_sha256",
        "kind",
        "created_at",
    }
)
_RELEASE_IDENTITY_FIELDS = frozenset(
    {"revision", "release_id", "base_id", "wheel_sha256"}
)
_Model = TypeVar("_Model")


class RuntimeGenerationContractError(ValueError):
    """运行代际字段或序列化载荷不符合契约。"""


class GenerationKind(StrEnum):
    LEGACY = "legacy"
    MANAGED = "managed"


@dataclass(frozen=True, slots=True)
class RuntimeGeneration:
    schema_version: int
    generation_id: str
    revision: str
    release_id: str
    base_id: str
    entrypoint_contract_sha256: str
    systemd_bundle_sha256: str
    configuration_bundle_sha256: str
    database_contract_sha256: str
    index_set_sha256: str
    kind: GenerationKind
    created_at: str

    def __post_init__(self) -> None:
        _require_schema(self.schema_version)
        _require_generation_fields(self)
        if self.generation_id != canonical_sha256(_generation_identity(self)):
            raise RuntimeGenerationContractError("generation_id 与身份投影不一致")


@dataclass(frozen=True, slots=True)
class GenerationReleaseIdentity:
    revision: str
    release_id: str
    base_id: str
    wheel_sha256: str

    def __post_init__(self) -> None:
        _require_revision(self.revision)
        _require_digests(
            release_id=self.release_id,
            base_id=self.base_id,
            wheel_sha256=self.wheel_sha256,
        )


def create_runtime_generation(
    *,
    revision: str,
    release_id: str,
    base_id: str,
    entrypoint_contract_sha256: str,
    systemd_bundle_sha256: str,
    configuration_bundle_sha256: str,
    database_contract_sha256: str,
    index_set_sha256: str,
    kind: GenerationKind,
    created_at: str,
) -> RuntimeGeneration:
    """对不含审计时间的显式身份投影取规范 SHA-256。"""
    identity = {
        "base_id": base_id,
        "configuration_bundle_sha256": configuration_bundle_sha256,
        "database_contract_sha256": database_contract_sha256,
        "entrypoint_contract_sha256": entrypoint_contract_sha256,
        "index_set_sha256": index_set_sha256,
        "kind": kind,
        "release_id": release_id,
        "revision": revision,
        "schema_version": 1,
        "systemd_bundle_sha256": systemd_bundle_sha256,
    }
    return RuntimeGeneration(
        schema_version=1,
        generation_id=canonical_sha256(identity),
        revision=revision,
        release_id=release_id,
        base_id=base_id,
        entrypoint_contract_sha256=entrypoint_contract_sha256,
        systemd_bundle_sha256=systemd_bundle_sha256,
        configuration_bundle_sha256=configuration_bundle_sha256,
        database_contract_sha256=database_contract_sha256,
        index_set_sha256=index_set_sha256,
        kind=kind,
        created_at=created_at,
    )


def encode_runtime_generation(value: RuntimeGeneration) -> bytes:
    """编码完整代际记录，拒绝相似类型和任意映射。"""
    return _encode_exact(value, RuntimeGeneration)


def decode_runtime_generation(payload: bytes) -> RuntimeGeneration:
    """严格解码代际记录，未知、缺失或重复字段一律拒绝。"""
    values = _decode_generation_mapping(payload, _GENERATION_FIELDS, "记录")
    try:
        kind = values.get("kind")
        if type(kind) is not str:
            raise RuntimeGenerationContractError("kind 类型无效")
        values["kind"] = GenerationKind(kind)
        return RuntimeGeneration(**values)
    except (TypeError, ValueError):
        raise RuntimeGenerationContractError("运行代际记录无效") from None


def encode_generation_release_identity(value: GenerationReleaseIdentity) -> bytes:
    """编码 release 与 generation 的稳定连接身份。"""
    return _encode_exact(value, GenerationReleaseIdentity)


def decode_generation_release_identity(payload: bytes) -> GenerationReleaseIdentity:
    """严格解码 release 与 generation 的连接身份。"""
    values = _decode_generation_mapping(payload, _RELEASE_IDENTITY_FIELDS, "release 身份")
    try:
        return GenerationReleaseIdentity(**values)
    except (TypeError, ValueError):
        raise RuntimeGenerationContractError("运行代际 release 身份无效") from None


def verify_generation_release_identity(
    identity: GenerationReleaseIdentity,
    metadata: ReleaseMetadata,
) -> GenerationReleaseIdentity:
    """以已验证 release metadata 及其计算身份为 DTO 作证。"""
    if type(identity) is not GenerationReleaseIdentity:
        raise RuntimeGenerationContractError("只接受 GenerationReleaseIdentity")
    if type(metadata) is not ReleaseMetadata:
        raise RuntimeGenerationContractError("只接受已验证的 ReleaseMetadata")
    try:
        computed_release_id = compute_release_id(
            metadata.runtime_revision,
            metadata.wheel_sha256,
            metadata.base_id,
            metadata.base_metadata_sha256,
        )
    except ValueError:
        raise RuntimeGenerationContractError("release metadata 身份无效") from None
    if metadata.release_id != computed_release_id:
        raise RuntimeGenerationContractError("release metadata 的 release_id 与计算身份不一致")
    if (
        identity.revision != metadata.runtime_revision
        or identity.wheel_sha256 != metadata.wheel_sha256
        or identity.base_id != metadata.base_id
        or identity.release_id != metadata.release_id
    ):
        raise RuntimeGenerationContractError("generation release 身份与 metadata 不一致")
    return identity


def _require_generation_fields(value: RuntimeGeneration) -> None:
    _require_revision(value.revision)
    _require_digests(
        generation_id=value.generation_id,
        release_id=value.release_id,
        base_id=value.base_id,
        entrypoint_contract_sha256=value.entrypoint_contract_sha256,
        systemd_bundle_sha256=value.systemd_bundle_sha256,
        configuration_bundle_sha256=value.configuration_bundle_sha256,
        database_contract_sha256=value.database_contract_sha256,
        index_set_sha256=value.index_set_sha256,
    )
    if type(value.kind) is not GenerationKind:
        raise RuntimeGenerationContractError("kind 必须是 GenerationKind")
    _require_utc_timestamp(value.created_at, "created_at")


def _generation_identity(value: RuntimeGeneration) -> dict[str, object]:
    return {
        "base_id": value.base_id,
        "configuration_bundle_sha256": value.configuration_bundle_sha256,
        "database_contract_sha256": value.database_contract_sha256,
        "entrypoint_contract_sha256": value.entrypoint_contract_sha256,
        "index_set_sha256": value.index_set_sha256,
        "kind": value.kind.value,
        "release_id": value.release_id,
        "revision": value.revision,
        "schema_version": value.schema_version,
        "systemd_bundle_sha256": value.systemd_bundle_sha256,
    }


def _require_schema(value: object) -> None:
    try:
        require_schema(value, 1)
    except RuntimeContractSupportError as exc:
        raise RuntimeGenerationContractError(str(exc)) from None


def _require_revision(value: object) -> None:
    try:
        require_runtime_revision(value)
    except ValueError:
        raise RuntimeGenerationContractError("revision 必须是完整小写运行时 OID") from None


def _require_digests(**values: object) -> None:
    try:
        for field, value in values.items():
            require_sha256(value, field=field)
    except RuntimeContractSupportError:
        raise RuntimeGenerationContractError("运行代际摘要无效") from None


def _require_utc_timestamp(value: object, field: str) -> None:
    try:
        require_utc_rfc3339_z(value, field=field)
    except RuntimeContractSupportError as exc:
        raise RuntimeGenerationContractError(str(exc)) from None


def _encode_exact(value: _Model, expected: type[_Model]) -> bytes:
    if type(value) is not expected:
        raise RuntimeGenerationContractError(f"只接受 {expected.__name__}")
    return canonical_json_bytes(value)


def _decode_generation_mapping(
    payload: bytes,
    required_fields: frozenset[str],
    label: str,
) -> dict[str, object]:
    try:
        return decode_exact_json_mapping(
            payload,
            required_fields=required_fields,
            optional_defaults={},
            max_bytes=_MAX_GENERATION_BYTES,
        )
    except RuntimeContractSupportError:
        raise RuntimeGenerationContractError(f"运行代际{label}序列化载荷无效") from None


__all__ = [
    "GenerationKind",
    "GenerationReleaseIdentity",
    "RuntimeGeneration",
    "RuntimeGenerationContractError",
    "create_runtime_generation",
    "decode_generation_release_identity",
    "decode_runtime_generation",
    "encode_generation_release_identity",
    "encode_runtime_generation",
    "verify_generation_release_identity",
]
