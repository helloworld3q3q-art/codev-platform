"""运行代际不可变身份契约测试。"""

from __future__ import annotations

import dataclasses

import pytest

from codev_platform.core.runtime_models import ReleaseMetadata, canonical_sha256, compute_release_id
from codev_platform.runtime_generation_contract import (
    GenerationKind,
    GenerationReleaseIdentity,
    RuntimeGeneration,
    RuntimeGenerationContractError,
    create_runtime_generation,
    decode_generation_release_identity,
    decode_runtime_generation,
    encode_generation_release_identity,
    encode_runtime_generation,
    verify_generation_release_identity,
)
from tests.runtime_contract_malicious_support import strict_json_mutations


def _generation(*, created_at: str = "2026-07-19T10:00:00Z") -> RuntimeGeneration:
    return create_runtime_generation(
        revision="a" * 40,
        release_id="b" * 64,
        base_id="c" * 64,
        entrypoint_contract_sha256="d" * 64,
        systemd_bundle_sha256="e" * 64,
        configuration_bundle_sha256="f" * 64,
        database_contract_sha256="1" * 64,
        index_set_sha256="2" * 64,
        kind=GenerationKind.MANAGED,
        created_at=created_at,
    )


def test_generation模型字段冻结且legacy持久值精确() -> None:
    assert GenerationKind.LEGACY.value == "legacy"
    assert GenerationKind.MANAGED.value == "managed"
    assert tuple(field.name for field in dataclasses.fields(RuntimeGeneration)) == (
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
    )
    assert tuple(field.name for field in dataclasses.fields(GenerationReleaseIdentity)) == (
        "revision",
        "release_id",
        "base_id",
        "wheel_sha256",
    )
    assert RuntimeGeneration.__dataclass_params__.frozen is True
    assert "__dict__" not in RuntimeGeneration.__slots__
    with pytest.raises(dataclasses.FrozenInstanceError):
        _generation().created_at = "2026-07-19T11:00:00Z"  # type: ignore[misc]


def test_generation_id只使用显式身份投影() -> None:
    generation = _generation()
    expected_identity = {
        "base_id": "c" * 64,
        "configuration_bundle_sha256": "f" * 64,
        "database_contract_sha256": "1" * 64,
        "entrypoint_contract_sha256": "d" * 64,
        "index_set_sha256": "2" * 64,
        "kind": "managed",
        "release_id": "b" * 64,
        "revision": "a" * 40,
        "schema_version": 1,
        "systemd_bundle_sha256": "e" * 64,
    }

    assert generation.generation_id == canonical_sha256(expected_identity)
    assert _generation(created_at="2027-01-01T00:00:00Z").generation_id == (
        generation.generation_id
    )
    changed = create_runtime_generation(
        revision=generation.revision,
        release_id=generation.release_id,
        base_id=generation.base_id,
        entrypoint_contract_sha256="3" * 64,
        systemd_bundle_sha256=generation.systemd_bundle_sha256,
        configuration_bundle_sha256=generation.configuration_bundle_sha256,
        database_contract_sha256=generation.database_contract_sha256,
        index_set_sha256=generation.index_set_sha256,
        kind=generation.kind,
        created_at=generation.created_at,
    )
    assert changed.generation_id != generation.generation_id


def test_generation与release_identity严格往返() -> None:
    generation = _generation()
    identity = GenerationReleaseIdentity(
        revision=generation.revision,
        release_id=generation.release_id,
        base_id=generation.base_id,
        wheel_sha256="4" * 64,
    )

    assert decode_runtime_generation(encode_runtime_generation(generation)) == generation
    assert decode_generation_release_identity(
        encode_generation_release_identity(identity)
    ) == identity


def _release_metadata() -> ReleaseMetadata:
    release_id = compute_release_id("a" * 40, "4" * 64, "c" * 64, "5" * 64)
    return ReleaseMetadata(
        schema_version=1,
        release_id=release_id,
        runtime_revision="a" * 40,
        wheel_sha256="4" * 64,
        base_id="c" * 64,
        base_requirements_sha256="6" * 64,
        base_metadata_sha256="5" * 64,
        app_freeze_sha256="7" * 64,
        created_at="2026-07-19T09:00:00Z",
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        base_link_relative="venv/base",
        base_pth_relative="venv/base.pth",
    )


def test_release_identity必须由已验证metadata及计算身份作证() -> None:
    metadata = _release_metadata()
    identity = GenerationReleaseIdentity(
        revision=metadata.runtime_revision,
        release_id=metadata.release_id,
        base_id=metadata.base_id,
        wheel_sha256=metadata.wheel_sha256,
    )

    assert verify_generation_release_identity(identity, metadata) is identity


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("identity", "revision", "b" * 40),
        ("identity", "wheel_sha256", "8" * 64),
        ("identity", "base_id", "9" * 64),
        ("identity", "release_id", "a" * 64),
        ("metadata", "release_id", "b" * 64),
    ),
)
def test_release_identity复验拒绝dto漂移或伪造metadata摘要(
    target: str,
    field: str,
    value: str,
) -> None:
    metadata = _release_metadata()
    identity = GenerationReleaseIdentity(
        revision=metadata.runtime_revision,
        release_id=metadata.release_id,
        base_id=metadata.base_id,
        wheel_sha256=metadata.wheel_sha256,
    )
    if target == "identity":
        identity = dataclasses.replace(identity, **{field: value})
    else:
        metadata = dataclasses.replace(metadata, **{field: value})

    with pytest.raises(RuntimeGenerationContractError, match="release"):
        verify_generation_release_identity(identity, metadata)


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"generation_id": "A" * 64},
        {"generation_id": "0" * 64},
        {"revision": "a" * 7},
        {"release_id": "0" * 64},
        {"kind": "managed"},
        {"kind": "legacy_external"},
        {"created_at": ""},
        {"created_at": "2026-07-19T10:00:00+00:00"},
        {"created_at": "2026-02-30T10:00:00Z"},
    ],
)
def test_generation拒绝非法字段(mutation: dict[str, object]) -> None:
    values = {
        field.name: getattr(_generation(), field.name)
        for field in dataclasses.fields(RuntimeGeneration)
    }
    values.update(mutation)
    with pytest.raises(RuntimeGenerationContractError):
        RuntimeGeneration(**values)


def test_generation拒绝摘要与身份投影漂移() -> None:
    with pytest.raises(RuntimeGenerationContractError, match="generation_id"):
        dataclasses.replace(_generation(), entrypoint_contract_sha256="3" * 64)


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": "a" * 7},
        {"release_id": "A" * 64},
        {"base_id": "0" * 64},
        {"wheel_sha256": "a" * 63},
    ],
)
def test_release_identity拒绝非法字段(changes: dict[str, object]) -> None:
    values = {
        "revision": "a" * 40,
        "release_id": "b" * 64,
        "base_id": "c" * 64,
        "wheel_sha256": "d" * 64,
    }
    values.update(changes)
    with pytest.raises(RuntimeGenerationContractError):
        GenerationReleaseIdentity(**values)


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        pytest.param(decoder, payload, id=f"{name}-{mutation}")
        for name, decoder, encoded, wrong_field in (
            (
                "generation",
                decode_runtime_generation,
                encode_runtime_generation(_generation()),
                "kind",
            ),
            (
                "release-identity",
                decode_generation_release_identity,
                encode_generation_release_identity(
                    GenerationReleaseIdentity(
                        revision="a" * 40,
                        release_id="b" * 64,
                        base_id="c" * 64,
                        wheel_sha256="d" * 64,
                    )
                ),
                "revision",
            ),
        )
        for mutation, payload in strict_json_mutations(
            encoded,
            max_bytes=32_768,
            wrong_field=wrong_field,
        )
    ],
)
def test_generation每个decoder逐项拒绝单变量恶意载荷(
    decoder,
    payload: object,
) -> None:
    with pytest.raises(RuntimeGenerationContractError):
        decoder(payload)  # type: ignore[arg-type]


def test_generation编码拒绝错误类型() -> None:
    with pytest.raises(RuntimeGenerationContractError):
        encode_runtime_generation(object())  # type: ignore[arg-type]
