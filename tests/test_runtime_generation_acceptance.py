"""按 attempt 独立的运行代际验收契约测试。"""

from __future__ import annotations

import dataclasses
import importlib
import json
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import canonical_json_bytes, canonical_sha256
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
    GenerationAcceptanceError,
    acceptance_record_sha256,
    decode_generation_acceptance,
    encode_generation_acceptance,
    serving_binding_sha256,
)
from codev_platform.runtime_fencing import ServingFenceRecord
from tests.runtime_contract_malicious_support import strict_json_mutations


def _acceptance(**changes: object) -> GenerationAcceptance:
    values: dict[str, object] = {
        "schema_version": 1,
        "attempt_id": "a" * 32,
        "generation_id": "b" * 64,
        "serving_fence_id": "serving-fence-a",
        "serving_fence_epoch": 4,
        "serving_fence_token_sha256": "c" * 64,
        "control_lease_epoch_audit": 8,
        "control_token_sha256_audit": "d" * 64,
        "control_lease_record_sha256_audit": "4" * 64,
        "entrypoint_proof_sha256": "e" * 64,
        "database_proof_sha256": "f" * 64,
        "systemd_proof_sha256": "1" * 64,
        "index_set_proof_sha256": "2" * 64,
        "health_proof_sha256": "3" * 64,
        "accepted_at": "2026-07-19T10:00:00Z",
    }
    values.update(changes)
    return GenerationAcceptance(**values)


def _encoded_acceptance_with_control_record(
    control_lease_record_sha256_audit: object = "4" * 64,
) -> bytes:
    values = json.loads(encode_generation_acceptance(_acceptance()))
    values["control_lease_record_sha256_audit"] = control_lease_record_sha256_audit
    return canonical_json_bytes(values)


def _serving_fence(acceptance: GenerationAcceptance) -> ServingFenceRecord:
    return ServingFenceRecord(
        schema_version=1,
        fence_id=acceptance.serving_fence_id,
        generation_id=acceptance.generation_id,
        accepted_attempt_id=acceptance.attempt_id,
        epoch=acceptance.serving_fence_epoch,
        token_sha256=acceptance.serving_fence_token_sha256,
        issued_at="2026-07-19T09:59:00Z",
    )


def test_acceptance字段精确冻结且无原始token() -> None:
    assert tuple(field.name for field in dataclasses.fields(GenerationAcceptance)) == (
        "schema_version",
        "attempt_id",
        "generation_id",
        "serving_fence_id",
        "serving_fence_epoch",
        "serving_fence_token_sha256",
        "control_lease_epoch_audit",
        "control_token_sha256_audit",
        "control_lease_record_sha256_audit",
        "entrypoint_proof_sha256",
        "database_proof_sha256",
        "systemd_proof_sha256",
        "index_set_proof_sha256",
        "health_proof_sha256",
        "accepted_at",
    )
    assert GenerationAcceptance.__dataclass_params__.frozen is True
    assert "__dict__" not in GenerationAcceptance.__slots__
    token_fields = tuple(
        field.name for field in dataclasses.fields(GenerationAcceptance) if "token" in field.name
    )
    assert token_fields == (
        "serving_fence_token_sha256",
        "control_token_sha256_audit",
    )


def test_serving_fence_acceptance绑定校验属于acceptance边界() -> None:
    """围栏模块不承载跨域 acceptance 语义。"""
    validation = importlib.import_module("codev_platform.runtime_generation_acceptance_validation")
    fencing = importlib.import_module("codev_platform.runtime_fencing")
    acceptance = _acceptance()
    fence = _serving_fence(acceptance)

    assert validation.verify_serving_fence_acceptance(fence, acceptance) is fence
    assert not hasattr(fencing, "GenerationAcceptance")
    assert not hasattr(fencing, "verify_serving_fence_acceptance")
    with pytest.raises(validation.GenerationAcceptanceBindingError, match="绑定"):
        validation.verify_serving_fence_acceptance(
            fence,
            dataclasses.replace(acceptance, serving_fence_epoch=fence.epoch + 1),
        )


def test_serving_binding绑定attempt_generation_fence与全部proof() -> None:
    accepted = _acceptance()

    for field, value in (
        ("attempt_id", "4" * 32),
        ("generation_id", "5" * 64),
        ("serving_fence_id", "serving-fence-b"),
        ("serving_fence_epoch", 5),
        ("serving_fence_token_sha256", "6" * 64),
        ("entrypoint_proof_sha256", "7" * 64),
        ("database_proof_sha256", "8" * 64),
        ("systemd_proof_sha256", "9" * 64),
        ("index_set_proof_sha256", "a" * 64),
        ("health_proof_sha256", "b" * 64),
    ):
        changed = dataclasses.replace(accepted, **{field: value})
        assert serving_binding_sha256(changed) != serving_binding_sha256(accepted)


def test_control审计与accepted_at不参与serving_binding但进入record() -> None:
    accepted = _acceptance()
    recovered = dataclasses.replace(
        accepted,
        control_lease_epoch_audit=99,
        control_token_sha256_audit="4" * 64,
        accepted_at="2026-07-19T12:00:00Z",
    )

    assert serving_binding_sha256(recovered) == serving_binding_sha256(accepted)
    assert acceptance_record_sha256(recovered) != acceptance_record_sha256(accepted)


def test_control_lease_record摘要只进入审计record不进入serving_binding() -> None:
    first = decode_generation_acceptance(_encoded_acceptance_with_control_record("4" * 64))
    second = decode_generation_acceptance(_encoded_acceptance_with_control_record("5" * 64))

    assert serving_binding_sha256(first) == serving_binding_sha256(second)
    assert acceptance_record_sha256(first) != acceptance_record_sha256(second)


def test同generation的不同attempt不能复用既有acceptance() -> None:
    first = _acceptance(attempt_id="a" * 32)
    second = _acceptance(attempt_id="b" * 32)
    assert first.generation_id == second.generation_id
    assert serving_binding_sha256(first) != serving_binding_sha256(second)


def test_record摘要等于全部持久字段的canonical摘要() -> None:
    acceptance = _acceptance()
    assert acceptance_record_sha256(acceptance) == canonical_sha256(acceptance)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"attempt_id": "a" * 31},
        {"attempt_id": "0" * 32},
        {"generation_id": "A" * 64},
        {"serving_fence_id": ""},
        {"serving_fence_id": "bad\x1ffence"},
        {"serving_fence_id": "a" * 129},
        {"serving_fence_epoch": True},
        {"serving_fence_epoch": 0},
        {"serving_fence_epoch": 2**63},
        {"control_lease_epoch_audit": 0},
        {"control_lease_epoch_audit": 2**63},
        {"health_proof_sha256": "0" * 64},
        {"accepted_at": "2026-07-19T10:00:00+00:00"},
        {"accepted_at": "2026-02-30T10:00:00Z"},
    ],
)
def test_acceptance非法字段fail_closed(changes: dict[str, object]) -> None:
    with pytest.raises(GenerationAcceptanceError):
        _acceptance(**changes)


def test_acceptance严格往返() -> None:
    acceptance = _acceptance()
    assert decode_generation_acceptance(encode_generation_acceptance(acceptance)) == acceptance


def test_acceptance_codec要求并往返control_lease_record摘要() -> None:
    payload = _encoded_acceptance_with_control_record()
    acceptance = decode_generation_acceptance(payload)

    assert acceptance.control_lease_record_sha256_audit == "4" * 64
    assert decode_generation_acceptance(encode_generation_acceptance(acceptance)) == acceptance


def test_acceptance_decoder拒绝缺失control_lease_record摘要() -> None:
    values = json.loads(encode_generation_acceptance(_acceptance()))
    del values["control_lease_record_sha256_audit"]

    with pytest.raises(GenerationAcceptanceError):
        decode_generation_acceptance(canonical_json_bytes(values))


@pytest.mark.parametrize("invalid_digest", ("0" * 64, "A" * 64, None))
def test_acceptance拒绝无效control_lease_record摘要(invalid_digest: object) -> None:
    values = dataclasses.asdict(_acceptance())
    values["control_lease_record_sha256_audit"] = invalid_digest

    with pytest.raises(GenerationAcceptanceError):
        GenerationAcceptance(**values)


@pytest.mark.parametrize(
    ("mutation", "payload"),
    [
        pytest.param(name, payload, id=name)
        for name, payload in strict_json_mutations(
            encode_generation_acceptance(_acceptance()),
            max_bytes=32_768,
            wrong_field="serving_fence_id",
        )
    ],
)
def test_acceptance_decoder逐项拒绝单变量恶意载荷(
    mutation: str,
    payload: object,
) -> None:
    del mutation
    with pytest.raises(GenerationAcceptanceError):
        decode_generation_acceptance(payload)  # type: ignore[arg-type]


def test_acceptance不再维护第二套JSON与基础校验真值() -> None:
    import codev_platform.runtime_generation_acceptance as acceptance

    source = Path(acceptance.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "def _unique_object",
        "def _reject_constant",
        "_ATTEMPT_ID =",
        "_UTC_RFC3339 =",
        "json.loads",
    ):
        assert forbidden not in source


def test_acceptance编码拒绝错误类型() -> None:
    with pytest.raises(GenerationAcceptanceError):
        encode_generation_acceptance(object())  # type: ignore[arg-type]
