"""运行时元数据的严格类型化 JSON 解码与原子文件 I/O。"""
from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from .runtime_models import BaseMetadata, RuntimeAbi, RuntimeModelError, canonical_json_bytes

T = TypeVar("T")


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeModelError("JSON 包含重复字段")
        value[key] = item
    return value


def _reject_constant(_value: str) -> object:
    raise RuntimeModelError("JSON 数值非法")


def _decode(model_type: type[T], payload: Mapping[str, object]) -> T:
    expected = {field.name for field in dataclasses.fields(model_type)}
    if set(payload) != expected:
        raise RuntimeModelError("JSON 字段集合不匹配")
    values = dict(payload)
    if model_type is BaseMetadata:
        nested = values.get("abi")
        if type(nested) is not dict:
            raise RuntimeModelError("abi 必须是 JSON 对象")
        values["abi"] = _decode(RuntimeAbi, nested)
    try:
        return model_type(**values)
    except (TypeError, ValueError) as exc:
        raise RuntimeModelError(f"{model_type.__name__} 数据无效") from exc


def decode_typed_bytes(payload: bytes, model_type: type[T]) -> T:
    """从一次性字节快照严格解码指定类型，供无链接 fd 读取复用。"""
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
        if type(value) is not dict:
            raise RuntimeModelError("JSON 根必须是对象")
        return _decode(model_type, value)
    except (AttributeError, UnicodeError, json.JSONDecodeError, RuntimeModelError):
        raise RuntimeModelError(f"{model_type.__name__} 元数据无效") from None


def read_typed(path: Path, model_type: type[T]) -> T:
    """严格读取一个指定类型的运行时元数据对象。"""
    try:
        payload = Path(path).read_bytes()
    except OSError:
        raise RuntimeModelError(f"{model_type.__name__} 元数据无效") from None
    return decode_typed_bytes(payload, model_type)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_typed_atomic(path: Path, value: object, model_type: type[object]) -> None:
    """以同目录临时文件原子写入一个指定类型的元数据对象。"""
    if type(value) is not model_type:
        raise RuntimeModelError(f"只接受 {model_type.__name__}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    owned = False
    try:
        with temp.open("xb") as stream:
            owned = True
            stream.write(canonical_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
        _fsync_directory(target.parent)
    finally:
        if owned:
            temp.unlink(missing_ok=True)
