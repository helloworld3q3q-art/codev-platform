"""审计访问日志只读仓 —— tail + filter + 分页读 data_root/audit/access.jsonl。

core/audit.py 是写侧 (append jsonl); 本仓是读侧, 仅查询不写。jsonl 无索引, 故
tail 最近 _MAX_SCAN 行 (防文件无限增长撑爆内存) → 反序解析 → 过滤 → 内存分页。
坏行静默跳过 (审计读不得因单条脏数据炸)。
记录字段: ts/service/user_id/org_id/via/project_id/allowed/reason (core/audit.py 写入)。
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass

from codev_platform.core.audit import audit_log_path

_MAX_SCAN = 50_000  # tail 行上限 (防大文件 OOM); 超出部分为更旧记录, 不参与查询


@dataclass(frozen=True)
class AuditFilter:
    service: str | None = None
    user_id: str | None = None
    org_id: str | None = None
    project_id: str | None = None
    allowed: bool | None = None
    ts_from: str | None = None  # ISO8601, 含界 (>=)
    ts_to: str | None = None    # ISO8601, 含界 (<=)


def _match(rec: dict, f: AuditFilter) -> bool:
    if f.service is not None and rec.get("service") != f.service:
        return False
    if f.user_id is not None and rec.get("user_id") != f.user_id:
        return False
    if f.org_id is not None and rec.get("org_id") != f.org_id:
        return False
    if f.project_id is not None and rec.get("project_id") != f.project_id:
        return False
    if f.allowed is not None and bool(rec.get("allowed")) != f.allowed:
        return False
    ts = rec.get("ts") or ""
    if f.ts_from is not None and ts < f.ts_from:
        return False
    if f.ts_to is not None and ts > f.ts_to:
        return False
    return True


def query(f: AuditFilter, *, offset: int, limit: int) -> tuple[list[dict], int]:
    """过滤 + 分页 (最新在前)。返回 (当前页记录, 命中总数)。文件不存在 → 空。"""
    path = audit_log_path()
    if not path.exists():
        return [], 0
    with path.open(encoding="utf-8") as fh:
        lines = deque(fh, maxlen=_MAX_SCAN)
    matched: list[dict] = []
    for line in reversed(lines):  # 最新在前
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue  # 脏行跳过
        if _match(rec, f):
            matched.append(rec)
    total = len(matched)
    return matched[offset:offset + limit], total
