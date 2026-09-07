"""code_vec 增量 manifest 的有界 schema 与失败关闭 codec。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_ENTRIES = 1_000_000
MAX_NODE_ID_CHARS = 4_096


def valid_code_vector_manifest(value: object) -> bool:
    if type(value) is not dict or len(value) > MAX_MANIFEST_ENTRIES:
        return False
    schema_valid = all(
        type(node_id) is str
        and 0 < len(node_id) <= MAX_NODE_ID_CHARS
        and type(digest) is str
        and len(digest) == 40
        and all(char in "0123456789abcdef" for char in digest)
        for node_id, digest in value.items()
    )
    return schema_valid and _encoded_size_within_limit(value)


def _encoded_size_within_limit(value: dict) -> bool:
    """流式计算真实 JSON 字节数，避免为总量校验复制整份清单。"""
    encoder = json.JSONEncoder(ensure_ascii=False)
    size = 0
    for chunk in encoder.iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > MAX_MANIFEST_BYTES:
            return False
    return True


def load_incremental_manifest(manifest_path: Path) -> tuple[dict, bool]:
    """大小/schema 任一越界即撤销，让调用方转干净全量。"""
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("manifest too large")
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not valid_code_vector_manifest(value):
            raise ValueError("manifest schema invalid")
        return value, True
    except Exception:  # noqa: BLE001 - 任意 codec/schema 损坏都转全量
        manifest_path.unlink(missing_ok=True)
        logger.warning("[code_vec] manifest 损坏, 转全量重建")
        return {}, False


__all__ = [
    "MAX_MANIFEST_BYTES",
    "MAX_MANIFEST_ENTRIES",
    "MAX_NODE_ID_CHARS",
    "load_incremental_manifest",
    "valid_code_vector_manifest",
]
