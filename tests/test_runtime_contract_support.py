"""运行时领域契约共享严格解码与基础校验真值测试。"""

from __future__ import annotations

import pytest

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
    require_attempt_id,
    require_schema,
    require_sha256,
    require_utc_rfc3339_z,
)

_VALID_OVERSIZE_MAPPING = b'{"schema_version":2}' + b" " * 45


def test_exact_mapping允许唯一可选字段缺失并补稳定默认值() -> None:
    decoded = decode_exact_json_mapping(
        b'{"schema_version":2}',
        required_fields=frozenset({"schema_version"}),
        optional_defaults={"policy_sha256": None},
        max_bytes=64,
    )

    assert decoded == {"schema_version": 2, "policy_sha256": None}


@pytest.mark.parametrize(
    "payload",
    (
        b'{"schema_version":2,"unknown":1}',
        b"{}",
        b'{"schema_version":2,"schema_version":2}',
        b'\xff',
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
        b"[]",
        _VALID_OVERSIZE_MAPPING,
    ),
)
def test_exact_mapping拒绝未知缺失重复非法常量容器与超限(payload: bytes) -> None:
    with pytest.raises(RuntimeContractSupportError):
        decode_exact_json_mapping(
            payload,
            required_fields=frozenset({"schema_version"}),
            optional_defaults={"policy_sha256": None},
            max_bytes=64,
        )


def test_exact_mapping合法超限载荷只被长度门禁拒绝() -> None:
    assert len(_VALID_OVERSIZE_MAPPING) == 65

    with pytest.raises(RuntimeContractSupportError, match="超过固定上限"):
        decode_exact_json_mapping(
            _VALID_OVERSIZE_MAPPING,
            required_fields=frozenset({"schema_version"}),
            optional_defaults={"policy_sha256": None},
            max_bytes=64,
        )

    assert decode_exact_json_mapping(
        _VALID_OVERSIZE_MAPPING,
        required_fields=frozenset({"schema_version"}),
        optional_defaults={"policy_sha256": None},
        max_bytes=65,
    ) == {"schema_version": 2, "policy_sha256": None}


def test_exact_mapping拒绝非bytes与非法策略参数() -> None:
    with pytest.raises(RuntimeContractSupportError):
        decode_exact_json_mapping(  # type: ignore[arg-type]
            "{}",
            required_fields=frozenset(),
            optional_defaults={},
            max_bytes=64,
        )
    with pytest.raises(RuntimeContractSupportError):
        decode_exact_json_mapping(
            b"{}",
            required_fields=frozenset({"schema_version"}),
            optional_defaults={},
            max_bytes=True,
        )


def test_exact_mapping独立拒绝空required_fields() -> None:
    with pytest.raises(RuntimeContractSupportError, match="required_fields"):
        decode_exact_json_mapping(
            b"{}",
            required_fields=frozenset(),
            optional_defaults={},
            max_bytes=64,
        )


@pytest.mark.parametrize(
    "payload",
    (
        '{"schema_version":2}'.encode("utf-16-le"),
        '{"schema_version":2}'.encode("utf-16-be"),
        '{"schema_version":2}'.encode("utf-32-le"),
        '{"schema_version":2}'.encode("utf-32-be"),
        '{"schema_version":2}'.encode("utf-16"),
        b"\xfe\xff" + '{"schema_version":2}'.encode("utf-16-be"),
        '{"schema_version":2}'.encode("utf-32"),
        b"\x00\x00\xfe\xff" + '{"schema_version":2}'.encode("utf-32-be"),
    ),
)
def test_exact_mapping拒绝完整合法UTF16与UTF32_JSON(payload: bytes) -> None:
    with pytest.raises(RuntimeContractSupportError):
        decode_exact_json_mapping(
            payload,
            required_fields=frozenset({"schema_version"}),
            optional_defaults={},
            max_bytes=256,
        )


def test_exact_mapping把低于上限的超长整数ValueError统一为support错误() -> None:
    payload = b'{"schema_version":' + b"9" * 5_000 + b"}"
    assert len(payload) < 8_192

    with pytest.raises(RuntimeContractSupportError):
        decode_exact_json_mapping(
            payload,
            required_fields=frozenset({"schema_version"}),
            optional_defaults={},
            max_bytes=8_192,
        )


@pytest.mark.parametrize(
    ("validator", "value"),
    (
        (lambda value: require_schema(value, 1), True),
        (require_attempt_id, "0" * 32),
        (require_sha256, "0" * 64),
        (require_utc_rfc3339_z, "2026-02-30T10:00:00Z"),
        (require_utc_rfc3339_z, "2026-07-19T10:00:00+00:00"),
    ),
)
def test共享基础校验拒绝bool零身份与伪时间(validator, value: object) -> None:
    with pytest.raises(RuntimeContractSupportError):
        validator(value)
