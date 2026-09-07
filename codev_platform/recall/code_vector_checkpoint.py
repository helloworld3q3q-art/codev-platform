"""code_vec side-build checkpoint 的构建指纹与原子元数据。"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from codev_platform.core.config import REINDEX_CONFIG_DIGEST_ENV, get as config_get
from codev_platform.core.repo_input_guard import REINDEX_TARGET_COMMIT_ENV
from codev_platform.core.runtime_interpreter import REINDEX_RUNTIME_REVISION_ENV
from codev_platform.recall.code_vector_chroma_config import CODE_VEC_CHROMA_STORAGE_POLICY_VERSION

CHECKPOINT_SCHEMA_VERSION = 1
_FINGERPRINT_FIELDS = frozenset(
    {
        "schema_version",
        "target_commit",
        "runtime_revision",
        "config_digest",
        "embed_backend",
        "embed_model_stamp",
        "embed_device",
        "embed_max_seq_length",
        "chunk_policy",
        "skip_kinds",
        "enrich",
    }
)
_META_FIELDS = frozenset(
    {
        "schema_version",
        "fingerprint",
        "embedding_dimension",
        "checkpoint_entries",
        "storage_policy_version",
    }
)
_HEX = frozenset("0123456789abcdef")


def _hex_digest(value: object, lengths: tuple[int, ...]) -> bool:
    return (
        type(value) is str
        and len(value) in lengths
        and all(character in _HEX for character in value)
    )


def _sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _model_stamp(model_path: object) -> str:
    """用路径身份、关键小文件内容和大文件 stat 生成轻量模型印记。"""
    root = Path(str(model_path or "")).expanduser()
    evidence: list[tuple[object, ...]] = [("root", str(root.resolve(strict=False)))]
    if not root.is_dir():
        return _sha256_json(evidence)
    try:
        files = sorted(path for path in root.rglob("*") if path.is_file())
        if len(files) > 4096:
            files = files[:4096]
        for path in files:
            stat = path.stat()
            rel = path.relative_to(root).as_posix()
            content_digest = None
            if stat.st_size <= 1024 * 1024 and path.suffix.lower() in {".json", ".txt"}:
                content_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            evidence.append((rel, stat.st_size, stat.st_mtime_ns, content_digest))
    except OSError as exc:
        evidence.append(("unavailable", type(exc).__name__))
    return _sha256_json(evidence)


def build_checkpoint_fingerprint(
    cfg: dict,
    *,
    skip_kinds: frozenset[str],
    enrich: bool,
    chunk_policy: dict[str, object],
    repo_root: Path | None,
) -> dict[str, object]:
    """构造不含密钥的严格构建身份；任一字段变化都禁止复用旧 side-build。"""
    from codev_platform.agent.embed.registry import (
        code_vec_embedding_device,
        code_vec_embedding_max_seq_length,
    )
    from codev_platform.index_manifest import git_head

    backend = str(config_get(cfg, "recall.code_vec.embed_backend", "remote") or "remote")
    model_path = config_get(cfg, "models.embed_path", "")
    target = os.environ.get(REINDEX_TARGET_COMMIT_ENV)
    if not target and repo_root is not None:
        target = git_head(repo_root)
    runtime = os.environ.get(REINDEX_RUNTIME_REVISION_ENV) or "manual"
    config_digest = os.environ.get(REINDEX_CONFIG_DIGEST_ENV)
    if not config_digest:
        config_digest = _sha256_json(
            {
                "backend": backend,
                "device": code_vec_embedding_device(cfg),
                "max_seq": code_vec_embedding_max_seq_length(cfg),
                "chunk_policy": chunk_policy,
                "skip_kinds": sorted(skip_kinds),
            }
        )
    fingerprint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "target_commit": str(target or "manual"),
        "runtime_revision": str(runtime),
        "config_digest": str(config_digest),
        "embed_backend": backend,
        "embed_model_stamp": _model_stamp(model_path),
        "embed_device": code_vec_embedding_device(cfg),
        "embed_max_seq_length": code_vec_embedding_max_seq_length(cfg),
        "chunk_policy": _sha256_json(chunk_policy),
        "skip_kinds": sorted(skip_kinds),
        "enrich": enrich,
    }
    if not valid_checkpoint_fingerprint(fingerprint):
        raise ValueError("code_vec checkpoint 构建指纹无效")
    return fingerprint


def valid_checkpoint_fingerprint(value: object) -> bool:
    if type(value) is not dict or set(value) != _FINGERPRINT_FIELDS:
        return False
    return (
        value.get("schema_version") == CHECKPOINT_SCHEMA_VERSION
        and (
            value.get("target_commit") == "manual"
            or _hex_digest(value.get("target_commit"), (40, 64))
        )
        and (
            value.get("runtime_revision") == "manual"
            or _hex_digest(value.get("runtime_revision"), (40, 64))
        )
        and _hex_digest(value.get("config_digest"), (64,))
        and value.get("embed_backend") in {"remote", "qwen-local"}
        and _hex_digest(value.get("embed_model_stamp"), (64,))
        and type(value.get("embed_device")) is str
        and bool(value.get("embed_device"))
        and _hex_digest(value.get("chunk_policy"), (64,))
        and type(value.get("embed_max_seq_length")) is int
        and value["embed_max_seq_length"] >= 0
        and type(value.get("skip_kinds")) is list
        and all(type(item) is str and item for item in value["skip_kinds"])
        and value["skip_kinds"] == sorted(set(value["skip_kinds"]))
        and type(value.get("enrich")) is bool
    )


def load_checkpoint_meta(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if type(value) is not dict or set(value) != _META_FIELDS:
        return None
    if (
        value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
        or value.get("storage_policy_version") != CODE_VEC_CHROMA_STORAGE_POLICY_VERSION
        or not valid_checkpoint_fingerprint(value.get("fingerprint"))
        or type(value.get("embedding_dimension")) is not int
        or value["embedding_dimension"] <= 0
        or type(value.get("checkpoint_entries")) is not int
        or value["checkpoint_entries"] <= 0
    ):
        return None
    return value


def checkpoint_matches(path: Path, expected: dict[str, object]) -> bool:
    meta = load_checkpoint_meta(path)
    return meta is not None and meta["fingerprint"] == expected


def storage_policy_matches(path: Path) -> bool:
    """旧 checkpoint 不得继续写入默认 HNSW 阈值的 collection。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return (
        type(value) is dict
        and value.get("storage_policy_version") == CODE_VEC_CHROMA_STORAGE_POLICY_VERSION
    )


def checkpoint_fingerprint_digest(fingerprint: dict[str, object]) -> str:
    """返回严格指纹的稳定摘要，供跨 side-build 比较同一策略的进度高水位。"""
    if not valid_checkpoint_fingerprint(fingerprint):
        raise ValueError("code_vec checkpoint 构建指纹无效")
    return _sha256_json(fingerprint)


def write_checkpoint_meta(
    path: Path,
    fingerprint: dict[str, object],
    *,
    embedding_dimension: int,
    checkpoint_entries: int,
) -> None:
    """同目录原子替换，避免进程中断留下半截 JSON 被下次误判为可续建。"""
    if (
        not valid_checkpoint_fingerprint(fingerprint)
        or type(embedding_dimension) is not int
        or embedding_dimension <= 0
        or type(checkpoint_entries) is not int
        or checkpoint_entries <= 0
    ):
        raise ValueError("code_vec checkpoint 元数据无效")
    value = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "storage_policy_version": CODE_VEC_CHROMA_STORAGE_POLICY_VERSION,
        "fingerprint": fingerprint,
        "embedding_dimension": embedding_dimension,
        "checkpoint_entries": checkpoint_entries,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "build_checkpoint_fingerprint",
    "checkpoint_fingerprint_digest",
    "checkpoint_matches",
    "load_checkpoint_meta",
    "storage_policy_matches",
    "valid_checkpoint_fingerprint",
    "write_checkpoint_meta",
]
