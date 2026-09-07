"""Webhook 验收事件与新索引 attempt 的只读关联证明。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.project_id import ProjectIdError, validate as validate_project_id
from codev_platform.core.runtime_models import RuntimeModelError, require_runtime_revision
from codev_platform.index_kind_contract import REQUIRED_INDEX_KINDS
from codev_platform.ops.reindex_manifest_proof import (
    ReindexManifestProofError,
    require_project_queue_drained,
)
from codev_platform.reindex.queue_ports import QueueSnapshot
from codev_platform.reindex.target_commit import TargetCommitError, require_target_commit


_REQUIRED_COLUMNS = frozenset(
    {
        "attempt_id",
        "git_commit",
        "kind",
        "process_rc",
        "project_id",
        "result_digest",
        "runtime_revision",
        "source",
        "status",
        "target_commit",
        "validated_at",
        "validation_evidence",
    }
)
_RESULT_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_POLL_INTERVAL_SEC = 1.0


class ReindexIngressProofError(RuntimeError):
    """索引事实无法证明由本次 Webhook 验收事件产生。"""


@dataclass(frozen=True, slots=True)
class IngressManifestBaseline:
    """发送事件前四类 manifest 的 attempt 身份快照。"""

    project_id: str
    attempts: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ReindexIngressProof:
    """只公开受影响种类与规范摘要，不暴露 attempt 或数据库正文。"""

    kinds: tuple[str, ...]
    evidence_sha256: str


def capture_ingress_manifest_baseline(
    project_id: str,
    *,
    path: Path,
) -> IngressManifestBaseline:
    """在发送签名事件前读取唯一四类 attempt，作为不可复用的历史边界。"""
    project = _require_project_id(project_id)
    expected = _registered_kinds()
    rows = _read_rows(
        _require_manifest_path(path),
        project,
        baseline=True,
        kinds=expected,
    )
    if len(rows) != len(expected):
        raise ReindexIngressProofError("入口验收基线缺少唯一四类索引记录")
    attempts: dict[str, str] = {}
    for row in rows:
        if len(row) != 2 or type(row[0]) is not str or type(row[1]) is not str or not row[1]:
            raise ReindexIngressProofError("入口验收基线 attempt 无效")
        kind, attempt_id = row
        if kind not in expected or kind in attempts:
            raise ReindexIngressProofError("入口验收基线种类无效")
        attempts[kind] = attempt_id
    if frozenset(attempts) != frozenset(expected):
        raise ReindexIngressProofError("入口验收基线种类不完整")
    return IngressManifestBaseline(
        project_id=project,
        attempts=tuple((kind, attempts[kind]) for kind in expected),
    )


def wait_for_ingress_manifests(
    project_id: str,
    target_commit: str,
    runtime_revision: str,
    kinds: tuple[str, ...],
    baseline: IngressManifestBaseline,
    *,
    path: Path,
    queue_snapshot: Callable[[], QueueSnapshot],
    timeout_sec: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ReindexIngressProof:
    """有界等待受影响种类产生全新 Webhook attempt，且目标项目队列完全收敛。"""
    project = _require_project_id(project_id)
    target = _require_target(target_commit)
    runtime = _require_runtime(runtime_revision)
    affected = _require_kinds(kinds)
    _require_baseline(baseline, project)
    manifest_path = _require_manifest_path(path)
    if type(timeout_sec) not in (int, float) or not math.isfinite(float(timeout_sec)):
        raise ReindexIngressProofError("入口索引等待时限无效")
    if float(timeout_sec) <= 0 or not all(callable(item) for item in (queue_snapshot, monotonic, sleep)):
        raise ReindexIngressProofError("入口索引等待端口无效")
    deadline = monotonic() + float(timeout_sec)
    while True:
        try:
            proof = _require_new_attempts(
                project,
                target,
                runtime,
                affected,
                baseline,
                path=manifest_path,
            )
            require_project_queue_drained(queue_snapshot(), project)
            return proof
        except (ReindexIngressProofError, ReindexManifestProofError):
            pass
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ReindexIngressProofError("索引未在时限内证明由本次 Webhook 事件完成") from None
        sleep(min(_POLL_INTERVAL_SEC, remaining))


def _require_new_attempts(
    project: str,
    target: str,
    runtime: str,
    kinds: tuple[str, ...],
    baseline: IngressManifestBaseline,
    *,
    path: Path,
) -> ReindexIngressProof:
    rows = _read_rows(path, project, baseline=False, kinds=kinds)
    if len(rows) != len(kinds):
        raise ReindexIngressProofError("本次 Webhook 事件的索引记录不完整")
    previous = dict(baseline.attempts)
    evidence: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        if len(row) != 11 or type(row[0]) is not str:
            raise ReindexIngressProofError("本次 Webhook 事件的索引记录无效")
        kind, status, git_commit, recorded_target, recorded_runtime = row[:5]
        result_digest, process_rc, validated_at, validation_evidence, attempt_id, source = row[5:]
        valid = (
            kind in kinds
            and kind not in seen
            and status == "ok"
            and git_commit == target
            and recorded_target == target
            and recorded_runtime == runtime
            and type(result_digest) is str
            and _RESULT_DIGEST.fullmatch(result_digest) is not None
            and type(process_rc) is int
            and process_rc == 0
            and type(validated_at) in (int, float)
            and math.isfinite(float(validated_at))
            and float(validated_at) >= 0
            and validation_evidence == "completion_receipt"
            and type(attempt_id) is str
            and bool(attempt_id)
            and attempt_id != previous.get(kind)
            and source == "webhook"
        )
        if not valid:
            raise ReindexIngressProofError("索引未证明由本次 Webhook 事件完成")
        seen.add(kind)
        evidence.append(
            {
                "attempt_sha256": hashlib.sha256(attempt_id.encode("utf-8")).hexdigest(),
                "kind": kind,
                "result_digest": result_digest,
                "validated_at": validated_at,
            }
        )
    if seen != set(kinds):
        raise ReindexIngressProofError("本次 Webhook 事件的索引种类不完整")
    digest = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ReindexIngressProof(kinds=kinds, evidence_sha256=digest)


def _read_rows(
    path: Path,
    project_id: str,
    *,
    baseline: bool,
    kinds: tuple[str, ...] = (),
) -> list[tuple[object, ...]]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.0)
        connection.execute("PRAGMA query_only = ON")
        columns = {
            str(row[1]).lower() for row in connection.execute("PRAGMA table_info(index_builds)")
        }
        if not _REQUIRED_COLUMNS.issubset(columns):
            raise ReindexIngressProofError("入口索引 manifest schema 无法证明")
        placeholders = ",".join("?" for _kind in kinds)
        if baseline:
            return connection.execute(
                "SELECT kind, attempt_id FROM index_builds WHERE project_id = ? "
                f"AND kind IN ({placeholders}) ORDER BY kind",
                (project_id, *kinds),
            ).fetchall()
        return connection.execute(
            "SELECT kind, status, git_commit, target_commit, runtime_revision, result_digest, "
            "process_rc, validated_at, validation_evidence, attempt_id, source "
            f"FROM index_builds WHERE project_id = ? AND kind IN ({placeholders}) ORDER BY kind",
            (project_id, *kinds),
        ).fetchall()
    except ReindexIngressProofError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise ReindexIngressProofError("入口索引 manifest 只读验证失败") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def _registered_kinds() -> tuple[str, ...]:
    return REQUIRED_INDEX_KINDS


def _require_kinds(value: tuple[str, ...]) -> tuple[str, ...]:
    registered = _registered_kinds()
    if type(value) is not tuple or not value or any(type(item) is not str for item in value):
        raise ReindexIngressProofError("Webhook 受影响索引种类无效")
    selected = frozenset(value)
    if len(selected) != len(value) or not selected.issubset(registered):
        raise ReindexIngressProofError("Webhook 受影响索引种类无效")
    return tuple(kind for kind in registered if kind in selected)


def _require_baseline(value: IngressManifestBaseline, project_id: str) -> None:
    expected = _registered_kinds()
    if (
        type(value) is not IngressManifestBaseline
        or value.project_id != project_id
        or tuple(kind for kind, _attempt in value.attempts) != expected
        or any(type(attempt) is not str or not attempt for _kind, attempt in value.attempts)
    ):
        raise ReindexIngressProofError("入口验收基线无效")


def _require_project_id(value: str) -> str:
    try:
        return validate_project_id(value)
    except (ProjectIdError, TypeError, ValueError):
        raise ReindexIngressProofError("项目标识无法验证") from None


def _require_target(value: str) -> str:
    try:
        return require_target_commit(value)
    except (TargetCommitError, TypeError, ValueError):
        raise ReindexIngressProofError("目标提交无法验证") from None


def _require_runtime(value: str) -> str:
    try:
        return require_runtime_revision(value, git_only=True)
    except (RuntimeModelError, TypeError, ValueError):
        raise ReindexIngressProofError("运行时版本无法验证") from None


def _require_manifest_path(value: Path) -> Path:
    try:
        path = Path(value)
        metadata = path.lstat()
    except (OSError, TypeError, ValueError):
        raise ReindexIngressProofError("入口索引 manifest 路径不可用") from None
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or metadata.st_nlink != 1:
        raise ReindexIngressProofError("入口索引 manifest 路径不受信任")
    return path


__all__ = [
    "IngressManifestBaseline",
    "ReindexIngressProof",
    "ReindexIngressProofError",
    "capture_ingress_manifest_baseline",
    "wait_for_ingress_manifests",
]
