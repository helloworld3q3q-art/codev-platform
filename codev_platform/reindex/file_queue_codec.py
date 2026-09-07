"""File 队列状态文件的 JSON 编解码与负载构造。"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Protocol
from uuid import uuid4

from codev_platform.reindex.queue_ports import JobMeta, QuarantineRecord

MAX_JSON_BYTES = 1024 * 1024


class _JobPayload(Protocol):
    project_id: str
    kind: str
    enqueued_at: float
    meta: JobMeta


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"队列 JSON 含重复字段: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> object:
    raise ValueError(f"队列 JSON 含非有限数字: {value}")


def decode_object(raw: bytes) -> dict[str, object]:
    """严格解码有限大小的队列对象，拒绝含歧义的 JSON。"""
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("队列 JSON 超过大小上限")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except UnicodeError:
        raise ValueError("队列 JSON 不是 UTF-8") from None
    except json.JSONDecodeError:
        raise ValueError("队列 JSON 格式无效") from None
    if type(value) is not dict:
        raise ValueError("队列 JSON 根必须是对象")
    return value


def _meta_payload(meta: JobMeta) -> dict[str, str | None]:
    return {
        "source": meta.source,
        "pull_policy": meta.pull_policy,
        "target_commit": meta.target_commit,
    }


def meta_from_payload(
    data: object,
    *,
    fallback: dict[str, object] | None = None,
) -> JobMeta:
    """兼容旧平铺字段，但无效元数据一律降为 legacy 默认值。"""
    source = pull_policy = target_commit = None
    if type(data) is dict:
        source = data.get("source")
        pull_policy = data.get("pull_policy")
        target_commit = data.get("target_commit")
    elif fallback is not None:
        source = fallback.get("source")
        pull_policy = fallback.get("pull_policy")
        target_commit = fallback.get("target_commit")
    if type(source) is not str:
        return JobMeta()
    if pull_policy is not None and type(pull_policy) is not str:
        return JobMeta()
    if target_commit is not None and type(target_commit) is not str:
        return JobMeta()
    return JobMeta(source=source, pull_policy=pull_policy, target_commit=target_commit)


def strict_meta_from_payload(data: object) -> JobMeta | None:
    """迁移写侧只接受完整且类型正确的 v2 元数据。"""
    if type(data) is not dict or type(data.get("source")) is not str:
        return None
    pull_policy = data.get("pull_policy")
    target_commit = data.get("target_commit")
    if pull_policy is not None and type(pull_policy) is not str:
        return None
    if target_commit is not None and type(target_commit) is not str:
        return None
    return JobMeta(data["source"], pull_policy, target_commit)


def pending_payload_has_no_claim_or_owner(data: dict[str, object]) -> bool:
    """仅原始负载中两个字段都不存在，才能证明 pending 未被认领。"""
    return "claim_token" not in data and "owner_token" not in data


def pending_version_from_payload(
    data: dict[str, object],
    raw: bytes,
    stat: os.stat_result,
) -> str | None:
    """为 pending sidecar 生成严格 CAS 版本。"""
    if "pending_token" in data:
        token = data.get("pending_token")
        if type(token) is str and token.strip():
            return f"pt:{token}"
        return None
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join((
        "pf",
        digest,
        str(stat.st_dev),
        str(stat.st_ino),
        str(stat.st_mtime_ns),
        str(stat.st_size),
    ))


def _base_payload(
    record: _JobPayload,
    *,
    enqueued_at: float | None = None,
) -> dict[str, object]:
    return {
        "project_id": record.project_id,
        "kind": record.kind,
        "enqueued_at": record.enqueued_at if enqueued_at is None else enqueued_at,
        "meta": _meta_payload(record.meta),
    }


def pending_payload(
    record: _JobPayload,
    *,
    enqueued_at: float | None = None,
    pending_token: str | None = None,
) -> dict[str, object]:
    """生成带独立版本 token 的 pending 负载。"""
    payload = _base_payload(record, enqueued_at=enqueued_at)
    payload["pending_token"] = pending_token or uuid4().hex
    return payload


def active_payload(
    record: _JobPayload,
    *,
    claim_token: str,
    owner_token: str,
    lease_expires_at: float,
) -> dict[str, object]:
    payload = _base_payload(record)
    payload.update({
        "claim_token": claim_token,
        "owner_token": owner_token,
        "lease_expires_at": lease_expires_at,
    })
    return payload


def result_payload(
    record: _JobPayload,
    *,
    status: str,
    failure_reason: str | None = None,
) -> dict[str, object]:
    payload = _base_payload(record)
    payload.update({"result_status": status, "finished_at": time.time()})
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    return payload


def quarantine_payload(record: QuarantineRecord) -> dict[str, object]:
    return {field: getattr(record, field) for field in (
        "project_id", "kind", "claim_token", "attempt_id", "fence",
        "process_identity", "containment_kind", "native_ref", "reason",
        "quarantined_at",
    )}


__all__ = [
    "MAX_JSON_BYTES",
    "active_payload",
    "decode_object",
    "meta_from_payload",
    "pending_payload_has_no_claim_or_owner",
    "pending_version_from_payload",
    "pending_payload",
    "quarantine_payload",
    "result_payload",
    "strict_meta_from_payload",
]
