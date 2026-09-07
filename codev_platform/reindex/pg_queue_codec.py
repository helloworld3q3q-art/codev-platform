"""Pg 队列领域值与 JSON 之间的无状态转换。"""
from __future__ import annotations

import json
import re

from codev_platform.reindex.queue_ports import JobMeta

_XMIN_TEXT_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def required_text(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


def kind_rank() -> dict[str, int]:
    from codev_platform.reindex.runners import kinds

    return {kind: index for index, kind in enumerate(kinds())}


def _normalize_meta(meta: JobMeta | None) -> JobMeta:
    return meta if isinstance(meta, JobMeta) else JobMeta()


def meta_json(meta: JobMeta | None) -> str:
    value = _normalize_meta(meta)
    return json.dumps(
        {
            "source": value.source,
            "pull_policy": value.pull_policy,
            "target_commit": value.target_commit,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def meta_from_json(raw: object) -> JobMeta:
    if type(raw) is not str or not raw:
        return JobMeta()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return JobMeta()
    if type(data) is not dict or type(data.get("source")) is not str:
        return JobMeta()
    pull_policy = data.get("pull_policy")
    target_commit = data.get("target_commit")
    if pull_policy is not None and type(pull_policy) is not str:
        return JobMeta()
    if target_commit is not None and type(target_commit) is not str:
        return JobMeta()
    return JobMeta(data["source"], pull_policy, target_commit)


def strict_meta_from_json(raw: object) -> JobMeta | None:
    """迁移写侧仅接受完整且类型正确的 v2 元数据。"""
    if type(raw) is not str or not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if type(data) is not dict or type(data.get("source")) is not str:
        return None
    pull_policy = data.get("pull_policy")
    target_commit = data.get("target_commit")
    if pull_policy is not None and type(pull_policy) is not str:
        return None
    if target_commit is not None and type(target_commit) is not str:
        return None
    return JobMeta(data["source"], pull_policy, target_commit)


def legacy_pending_version_from_facts(
    *,
    pending_enqueued_at: object,
    pending_meta: object,
    status: object,
    claim_token: object,
    owner_token: object,
    pending_token: object,
    quarantine: object,
    xmin: object,
) -> str | None:
    """仅为可由 legacy xmin 写侧接受的规范 pending 生成版本。"""
    if any((
        pending_enqueued_at is not None,
        pending_meta is not None,
        status != "pending",
        claim_token is not None,
        owner_token is not None,
        pending_token is not None,
        quarantine is not None,
    )):
        return None
    if type(xmin) is not str or _XMIN_TEXT_RE.fullmatch(xmin) is None:
        return None
    return f"px:{xmin}"


def pending_version_from_row(row: object) -> str | None:
    """从 scan 行的 pending token 或 legacy xmin 构造 CAS 版本。"""
    if not isinstance(row, tuple) or len(row) < 24:
        return None
    token = row[22] if len(row) > 22 else None
    if row[3] is not None:
        return f"pt:{token}" if type(token) is str and token.strip() else None
    return legacy_pending_version_from_facts(
        pending_enqueued_at=row[3],
        pending_meta=row[4],
        status=row[5],
        claim_token=row[8],
        owner_token=row[14],
        pending_token=token,
        quarantine=row[21],
        xmin=row[23],
    )


__all__ = [
    "kind_rank", "legacy_pending_version_from_facts", "meta_from_json", "meta_json",
    "pending_version_from_row", "required_text", "strict_meta_from_json",
]
