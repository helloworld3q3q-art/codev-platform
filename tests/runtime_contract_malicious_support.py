"""从真实规范编码逐项生成单变量严格 JSON 恶意载荷。"""

from __future__ import annotations

import codecs
import json


def strict_json_mutations(
    valid: bytes,
    *,
    max_bytes: int,
    wrong_field: str,
) -> tuple[tuple[str, object], ...]:
    """保留完整合法基线，每个结果只改变一个字段或传输语义。"""
    text = valid.decode("utf-8")
    mapping = json.loads(text)
    if type(mapping) is not dict or wrong_field not in mapping:
        raise AssertionError("恶意矩阵基线必须是含指定字段的完整 JSON 对象")
    if _canonical(mapping) != valid:
        raise AssertionError("恶意矩阵必须直接起源于规范 encoder 输出")

    first_field = next(iter(mapping))
    unknown = dict(mapping)
    unknown["unknown_field"] = "unexpected"
    missing = dict(mapping)
    missing.pop(first_field)
    wrong_nested = dict(mapping)
    wrong_nested[wrong_field] = {}
    nan = _replace_field_value(valid, first_field, b"NaN")
    infinity = _replace_field_value(valid, first_field, b"Infinity")
    huge_integer = _replace_field_value(valid, first_field, b"9" * 5_000)
    if len(huge_integer) >= max_bytes:
        raise AssertionError("超长整数样例必须低于领域载荷上限")
    oversize = valid + b" " * (max_bytes - len(valid) + 1)
    if len(oversize) != max_bytes + 1:
        raise AssertionError("oversize 样例必须仅超过上限一个字节")

    encoded = text.encode
    return (
        ("unknown", _canonical(unknown)),
        ("missing", _canonical(missing)),
        ("duplicate", _duplicate_first_field(valid)),
        ("nan", nan),
        ("infinity", infinity),
        ("invalid-utf8", valid + b"\xff"),
        ("utf16-le", encoded("utf-16-le")),
        ("utf16-be", encoded("utf-16-be")),
        ("utf32-le", encoded("utf-32-le")),
        ("utf32-be", encoded("utf-32-be")),
        ("utf16-le-bom", codecs.BOM_UTF16_LE + encoded("utf-16-le")),
        ("utf16-be-bom", codecs.BOM_UTF16_BE + encoded("utf-16-be")),
        ("utf32-le-bom", codecs.BOM_UTF32_LE + encoded("utf-32-le")),
        ("utf32-be-bom", codecs.BOM_UTF32_BE + encoded("utf-32-be")),
        ("nonbytes", text),
        ("wrong-top-level", _canonical([mapping])),
        ("wrong-nested-type", _canonical(wrong_nested)),
        ("below-limit-huge-integer", huge_integer),
        ("oversize-valid-json", oversize),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _replace_field_value(payload: bytes, field: str, replacement: bytes) -> bytes:
    mapping = json.loads(payload)
    original = _canonical(mapping[field])
    needle = _canonical(field) + b":" + original
    if payload.count(needle) != 1:
        raise AssertionError("恶意矩阵字段必须在顶层唯一出现")
    return payload.replace(needle, _canonical(field) + b":" + replacement, 1)


def _duplicate_first_field(payload: bytes) -> bytes:
    comma = payload.find(b",")
    if not payload.startswith(b"{") or comma < 0:
        raise AssertionError("恶意矩阵基线必须含至少两个顶层字段")
    first_pair = payload[1 : comma + 1]
    return b"{" + first_pair + payload[1:]


__all__ = ["strict_json_mutations"]
