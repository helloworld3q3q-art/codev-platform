"""Typed HTTP read model for project-scoped index manifest status."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from codev_platform.core.platform_http import (
    UrlOpen,
    authenticated_json_request,
    platform_web_url,
)


class IndexStatusProtocolError(RuntimeError):
    """The platform index-status response does not satisfy its public contract."""


@dataclass(frozen=True, slots=True)
class IndexStatusItem:
    kind: str
    status: str | None
    git_commit: str | None
    fresh: bool | None
    reason: str | None
    finished_at: float | None = None
    elapsed_sec: float | None = None


@dataclass(frozen=True, slots=True)
class IndexStatusSnapshot:
    head_commit: str | None
    items: tuple[IndexStatusItem, ...]

    @property
    def by_kind(self) -> dict[str, IndexStatusItem]:
        return {item.kind: item for item in self.items}


class CoverageState(str, Enum):
    OK = "ok"
    PENDING = "pending"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IndexCoverage:
    state: CoverageState
    detail: str


CommitCovers = Callable[[str, str | None], bool]
_DEPENDENCY_REASON_RE = re.compile(
    r"(?:code_vec:)?dependency:[A-Za-z0-9_.-]+:(?:ok|stale|failed|missing|metadata-missing)"
)
_TERMINAL_DEPENDENCY_SUFFIXES = (":failed", ":missing", ":metadata-missing")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IndexStatusProtocolError("index status string field is invalid")
    return value


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndexStatusProtocolError("index status numeric field is invalid")
    return float(value)


def _parse_snapshot(payload: Any) -> IndexStatusSnapshot:
    if not isinstance(payload, dict) or payload.get("result") != 0:
        raise IndexStatusProtocolError("index status request did not succeed")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise IndexStatusProtocolError("index status data envelope is invalid")
    head = _optional_string(data.get("headCommit"))
    parsed: list[IndexStatusItem] = []
    kinds: set[str] = set()
    for raw in data["items"]:
        if not isinstance(raw, dict):
            raise IndexStatusProtocolError("index status item is invalid")
        kind = raw.get("kind")
        if not isinstance(kind, str) or not kind or kind in kinds:
            raise IndexStatusProtocolError("index status kind is invalid")
        fresh = raw.get("fresh")
        if fresh is not None and not isinstance(fresh, bool):
            raise IndexStatusProtocolError("index status freshness is invalid")
        kinds.add(kind)
        parsed.append(
            IndexStatusItem(
                kind=kind,
                status=_optional_string(raw.get("status")),
                git_commit=_optional_string(raw.get("gitCommit")),
                fresh=fresh,
                reason=_optional_string(raw.get("reason")),
                finished_at=_optional_number(raw.get("finishedAt")),
                elapsed_sec=_optional_number(raw.get("elapsedSec")),
            )
        )
    return IndexStatusSnapshot(head_commit=head, items=tuple(parsed))


def read_platform_index_status(
    project_id: str,
    cfg: dict,
    *,
    timeout: float = 10,
    opener: UrlOpen | None = None,
) -> IndexStatusSnapshot:
    """Read one project's manifest status through the authenticated web API."""
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project_id must be non-empty")
    url = platform_web_url(cfg) + "/api/v1/indexes/status"
    payload = authenticated_json_request(
        url,
        cfg,
        method="POST",
        headers={"X-Project-Id": project_id},
        timeout=timeout,
        opener=opener,
    )
    return _parse_snapshot(payload)


def _stale_detail(item: IndexStatusItem) -> str:
    match = _DEPENDENCY_REASON_RE.search(item.reason or "")
    return match.group(0) if match else f"{item.kind}:stale"


def assess_index_coverage(
    snapshot: IndexStatusSnapshot,
    expected_kinds: Iterable[str],
    target_commit: str,
    *,
    commit_covers: CommitCovers,
    failure_not_before: float | None = None,
) -> IndexCoverage:
    """Classify expected indexes as covered, still pending, or terminally failed."""
    by_kind = snapshot.by_kind
    failures: list[str] = []
    pending: list[str] = []
    selected = sorted(set(expected_kinds))
    for kind in selected:
        item = by_kind.get(kind)
        if item is None:
            pending.append(f"{kind}:missing")
            continue
        if not commit_covers(target_commit, item.git_commit):
            pending.append(f"{kind}:stale")
            continue
        if item.status == "failed":
            if failure_not_before is not None and (
                item.finished_at is None or item.finished_at < failure_not_before
            ):
                pending.append(f"{kind}:retry-pending")
            else:
                failures.append(f"{kind}:failed")
            continue
        if item.status != "ok":
            pending.append(f"{kind}:{item.status or 'unknown'}")
            continue
        # `fresh` is relative to the server's current HEAD. It is authoritative for
        # the common wait/health case where that HEAD is exactly the requested target.
        if snapshot.head_commit == target_commit and item.fresh is not True:
            detail = _stale_detail(item)
            if detail.endswith(_TERMINAL_DEPENDENCY_SUFFIXES):
                failures.append(detail)
            else:
                pending.append(detail)
    if failures:
        return IndexCoverage(CoverageState.FAILED, ", ".join(failures))
    if pending:
        return IndexCoverage(CoverageState.PENDING, ", ".join(pending))
    return IndexCoverage(CoverageState.OK, ", ".join(selected))


__all__ = [
    "CoverageState",
    "IndexCoverage",
    "IndexStatusItem",
    "IndexStatusProtocolError",
    "IndexStatusSnapshot",
    "assess_index_coverage",
    "read_platform_index_status",
]
