"""运行时领域契约内部共享的严格 JSON 与基础值校验。"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime

from codev_platform.core.runtime_models import require_sha256 as _require_core_sha256

_ATTEMPT_ID = re.compile(r"[0-9a-f]{32}\Z")
_UTC_RFC3339_Z = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")


class RuntimeContractSupportError(ValueError):
    """共享运行时契约输入不满足严格格式。"""


def decode_exact_json_mapping(
    payload: bytes,
    *,
    required_fields: frozenset[str],
    optional_defaults: Mapping[str, object],
    max_bytes: int,
) -> dict[str, object]:
    """在固定字节上限内解码无重复键且字段集合精确的 JSON 对象。"""
    if type(payload) is not bytes:
        raise RuntimeContractSupportError("payload 必须是 bytes")
    if (
        type(required_fields) is not frozenset
        or not required_fields
        or not all(type(field) is str and field for field in required_fields)
    ):
        raise RuntimeContractSupportError("required_fields 必须是非空字段名 frozenset")
    if not isinstance(optional_defaults, Mapping) or not all(
        type(field) is str and field for field in optional_defaults
    ):
        raise RuntimeContractSupportError("optional_defaults 必须是字段默认值映射")
    optional_fields = frozenset(optional_defaults)
    if required_fields & optional_fields:
        raise RuntimeContractSupportError("必填与可选字段不得重叠")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise RuntimeContractSupportError("max_bytes 必须是正整数")
    if not payload or len(payload) > max_bytes:
        raise RuntimeContractSupportError("payload 为空或超过固定上限")

    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeContractSupportError("JSON 字段重复")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise RuntimeContractSupportError(f"非法 JSON 常量: {value}")

    try:
        text = payload.decode("utf-8")
        decoded = json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_constant=reject_constant,
        )
    except RuntimeContractSupportError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise RuntimeContractSupportError("payload 不是合法 UTF-8 JSON") from None
    if type(decoded) is not dict:
        raise RuntimeContractSupportError("payload 顶层必须是 JSON 对象")

    actual_fields = frozenset(decoded)
    allowed_fields = required_fields | optional_fields
    if not required_fields <= actual_fields or not actual_fields <= allowed_fields:
        raise RuntimeContractSupportError("payload 字段集合不匹配")
    for field, default in optional_defaults.items():
        decoded.setdefault(field, default)
    return decoded


def require_schema(value: object, expected: int) -> int:
    """校验精确整数 schema 版本。"""
    if type(expected) is not int or expected <= 0:
        raise RuntimeContractSupportError("expected schema 必须是正整数")
    if type(value) is not int or value != expected:
        raise RuntimeContractSupportError(f"schema_version 只接受整数 {expected}")
    return value


def require_attempt_id(value: object) -> str:
    """校验非零 128 位小写十六进制部署尝试身份。"""
    if type(value) is not str or _ATTEMPT_ID.fullmatch(value) is None or set(value) == {"0"}:
        raise RuntimeContractSupportError("attempt_id 必须是非零 128 位小写十六进制")
    return value


def require_sha256(value: object, *, field: str = "sha256") -> str:
    """校验非零规范 SHA-256，并统一转换底层异常类型。"""
    try:
        return _require_core_sha256(value, field=field)
    except ValueError as exc:
        raise RuntimeContractSupportError(str(exc)) from None


def require_utc_rfc3339_z(value: object, *, field: str = "timestamp") -> str:
    """校验真实的 UTC RFC3339 Z 时间。"""
    if type(field) is not str or not field:
        raise RuntimeContractSupportError("时间字段名必须是非空字符串")
    if type(value) is not str or _UTC_RFC3339_Z.fullmatch(value) is None:
        raise RuntimeContractSupportError(f"{field} 必须是 UTC RFC3339 Z 时间")
    try:
        datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        raise RuntimeContractSupportError(f"{field} 必须是真实 UTC 时间") from None
    return value


def require_strictly_later(
    value: object,
    boundary: object,
    *,
    field: str,
    boundary_field: str,
) -> str:
    """校验两个 UTC 审计时间格式，并要求前者严格晚于后者。"""
    current = require_utc_rfc3339_z(value, field=field)
    previous = require_utc_rfc3339_z(boundary, field=boundary_field)
    current_time = datetime.fromisoformat(f"{current[:-1]}+00:00")
    previous_time = datetime.fromisoformat(f"{previous[:-1]}+00:00")
    if current_time <= previous_time:
        raise RuntimeContractSupportError(f"{field} 必须严格晚于 {boundary_field}")
    return current


__all__ = [
    "RuntimeContractSupportError",
    "decode_exact_json_mapping",
    "require_attempt_id",
    "require_schema",
    "require_sha256",
    "require_strictly_later",
    "require_utc_rfc3339_z",
]
