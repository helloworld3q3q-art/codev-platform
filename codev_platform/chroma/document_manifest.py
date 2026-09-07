"""平台文档索引 manifest 的唯一 schema 与读写 codec。"""
from __future__ import annotations

import json
from pathlib import Path

from codev_platform.chroma._index_config import logger

# v2 把 embed_max_seq_length 纳入严格指纹；旧运行版与新运行版回滚时必须互相触发重建。
MANIFEST_VERSION = 2
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_FILES = 100_000
MAX_CHUNKS_PER_FILE = 100_000
MAX_TOTAL_CHUNKS = 1_000_000


def empty_manifest() -> dict:
    return {"version": MANIFEST_VERSION, "params": {}, "files": {}}


def _valid_params(params: object) -> bool:
    if type(params) is not dict:
        return False
    model = params.get("embed_model")
    dimension = params.get("embed_dim")
    max_seq_length = params.get("embed_max_seq_length")
    target = params.get("chunk_target_max")
    hard = params.get("chunk_hard_max")
    return (
        type(params.get("manifest_version")) is int
        and params.get("manifest_version") == MANIFEST_VERSION
        and type(model) is str
        and 0 < len(model) <= 4_096
        and type(dimension) is int
        and 0 < dimension <= 1_000_000
        and type(max_seq_length) is int
        and 0 <= max_seq_length <= 1_000_000
        and type(target) is int
        and 0 < target <= 10_000_000
        and type(hard) is int
        and target <= hard <= 10_000_000
    )


def valid_document_manifest(manifest: object) -> bool:
    if (
        type(manifest) is not dict
        or type(manifest.get("version")) is not int
        or manifest.get("version") != MANIFEST_VERSION
        or not _valid_params(manifest.get("params"))
        or type(manifest.get("files")) is not dict
        or len(manifest["files"]) > MAX_MANIFEST_FILES
    ):
        return False
    total_chunks = 0
    for rel, entry in manifest["files"].items():
        if type(rel) is not str or not rel or type(entry) is not dict:
            return False
        digest = entry.get("sha256")
        count = entry.get("chunk_count")
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or type(count) is not int
            or count < 0
            or count > MAX_CHUNKS_PER_FILE
        ):
            return False
        total_chunks += count
        if total_chunks > MAX_TOTAL_CHUNKS:
            return False
    return True


def load_manifest(manifest_path: Path) -> tuple[dict, bool]:
    """读取并严格校验；损坏即撤销，使调用方进入全量自愈。"""
    if not manifest_path.exists():
        return empty_manifest(), False
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("manifest too large")
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if type(data) is not dict:
            raise ValueError("manifest root invalid")
        if not valid_document_manifest(data):
            raise ValueError("manifest schema invalid")
        return data, True
    except Exception as exc:  # noqa: BLE001 - 任意 codec/schema 损坏都转全量
        logger.warning("manifest 损坏(%s),按全量重建", exc)
        manifest_path.unlink(missing_ok=True)
        return empty_manifest(), False


def save_manifest(manifest: dict, manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


__all__ = [
    "MANIFEST_VERSION",
    "MAX_CHUNKS_PER_FILE",
    "MAX_MANIFEST_BYTES",
    "MAX_MANIFEST_FILES",
    "MAX_TOTAL_CHUNKS",
    "empty_manifest",
    "load_manifest",
    "save_manifest",
    "valid_document_manifest",
]
