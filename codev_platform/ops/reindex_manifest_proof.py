"""部署验收使用的四类索引 manifest 与队列只读证明。"""

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
from codev_platform.reindex.queue_ports import QueueSnapshot
from codev_platform.reindex.target_commit import TargetCommitError, require_target_commit


_REQUIRED_COLUMNS = frozenset(
    {
        "project_id",
        "kind",
        "status",
        "git_commit",
        "target_commit",
        "runtime_revision",
        "depends_json",
        "result_digest",
        "process_rc",
        "validated_at",
        "validation_evidence",
    }
)
_RESULT_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_POLL_INTERVAL_SEC = 1.0


class ReindexManifestProofError(RuntimeError):
    """四库索引事实尚未精确覆盖本次部署目标。"""


@dataclass(frozen=True, slots=True)
class ReindexManifestProof:
    """只公开种类集合与规范摘要，不暴露日志、路径或数据库正文。"""

    kinds: tuple[str, ...]
    target_commit: str
    runtime_revision: str
    evidence_sha256: str


def require_all_reindex_manifests_target_ok(
    project_id: str,
    target_commit: str,
    runtime_revision: str,
    *,
    path: Path,
) -> ReindexManifestProof:
    """在 SQLite 只读连接中证明四种构建均由目标 release 成功发布。"""
    project = _require_project_id(project_id)
    target = _require_target(target_commit)
    runtime = _require_runtime(runtime_revision)
    manifest_path = _require_manifest_path(path)
    expected_kinds = REQUIRED_INDEX_KINDS
    rows = _read_rows(manifest_path, project, expected_kinds)
    by_kind = _validate_rows(rows, expected_kinds, target, runtime)
    _verify_code_vec_dependency(by_kind["code_vec"], by_kind["codegraph"], target)
    evidence = [
        {
            "kind": kind,
            "result_digest": by_kind[kind][5],
            "validated_at": by_kind[kind][7],
        }
        for kind in expected_kinds
    ]
    digest = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ReindexManifestProof(expected_kinds, target, runtime, digest)


def wait_for_all_reindex_manifests(
    project_id: str,
    target_commit: str,
    runtime_revision: str,
    *,
    path: Path,
    queue_snapshot: Callable[[], QueueSnapshot],
    timeout_sec: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ReindexManifestProof:
    """有界等待四库和队列同时收敛；瞬时 SQLite 写锁只视为尚未完成。"""
    if type(timeout_sec) not in (int, float) or not math.isfinite(float(timeout_sec)):
        raise ReindexManifestProofError("索引等待时限无效")
    if float(timeout_sec) <= 0 or not all(callable(item) for item in (queue_snapshot, monotonic, sleep)):
        raise ReindexManifestProofError("索引等待端口无效")
    deadline = monotonic() + float(timeout_sec)
    last_error: ReindexManifestProofError | None = None
    while True:
        try:
            proof = require_all_reindex_manifests_target_ok(
                project_id,
                target_commit,
                runtime_revision,
                path=path,
            )
            require_project_queue_drained(queue_snapshot(), project_id)
            return proof
        except ReindexManifestProofError as error:
            last_error = error
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ReindexManifestProofError("四库索引在部署时限内未完成") from last_error
        sleep(min(_POLL_INTERVAL_SEC, remaining))


def require_project_queue_drained(snapshot: QueueSnapshot, project_id: str) -> None:
    """成功 manifest 之外，还要求目标项目不存在 pending/active/隔离残留。"""
    project = _require_project_id(project_id)
    if not isinstance(snapshot, QueueSnapshot):
        raise ReindexManifestProofError("reindex 队列快照无效")
    jobs = (*snapshot.pending, *snapshot.active, *snapshot.expired_active)
    if any(getattr(job, "project_id", None) == project for job in jobs):
        raise ReindexManifestProofError("目标项目 reindex 队列尚未清空")
    if any(getattr(item, "project_id", None) == project for item in snapshot.quarantined):
        raise ReindexManifestProofError("目标项目仍有隔离 reindex 任务")


def _read_rows(
    path: Path,
    project_id: str,
    kinds: tuple[str, ...],
) -> list[tuple[object, ...]]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.0)
        connection.execute("PRAGMA query_only = ON")
        columns = {
            str(row[1]).lower() for row in connection.execute("PRAGMA table_info(index_builds)")
        }
        if not _REQUIRED_COLUMNS.issubset(columns):
            raise ReindexManifestProofError("索引 manifest schema 无法证明")
        placeholders = ",".join("?" for _kind in kinds)
        return connection.execute(
            "SELECT kind, status, git_commit, target_commit, runtime_revision, "
            "result_digest, process_rc, validated_at, validation_evidence, depends_json "
            f"FROM index_builds WHERE project_id = ? AND kind IN ({placeholders}) ORDER BY kind",
            (project_id, *kinds),
        ).fetchall()
    except ReindexManifestProofError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError):
        raise ReindexManifestProofError("索引 manifest 只读验证失败") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass


def _validate_rows(
    rows: list[tuple[object, ...]],
    expected_kinds: tuple[str, ...],
    target: str,
    runtime: str,
) -> dict[str, tuple[object, ...]]:
    if len(rows) != len(expected_kinds):
        raise ReindexManifestProofError("索引 manifest 未包含唯一四类构建记录")
    by_kind: dict[str, tuple[object, ...]] = {}
    for row in rows:
        if len(row) != 10 or type(row[0]) is not str or row[0] in by_kind:
            raise ReindexManifestProofError("索引 manifest 记录格式无效")
        kind, status, git_commit, recorded_target, recorded_runtime = row[:5]
        result_digest, process_rc, validated_at, evidence = row[5:9]
        if (
            kind not in expected_kinds
            or status != "ok"
            or git_commit != target
            or recorded_target != target
            or recorded_runtime != runtime
            or type(result_digest) is not str
            or _RESULT_DIGEST.fullmatch(result_digest) is None
            or type(process_rc) is not int
            or process_rc != 0
            or type(validated_at) not in (int, float)
            or not math.isfinite(float(validated_at))
            or float(validated_at) < 0
            or evidence != "completion_receipt"
        ):
            raise ReindexManifestProofError("索引 manifest 未证明目标提交成功构建")
        by_kind[kind] = row
    if frozenset(by_kind) != frozenset(expected_kinds):
        raise ReindexManifestProofError("索引 manifest 种类集合不完整")
    return by_kind


def _verify_code_vec_dependency(
    code_vec: tuple[object, ...],
    codegraph: tuple[object, ...],
    target: str,
) -> None:
    raw = code_vec[9]
    try:
        dependencies = json.loads(raw) if type(raw) is str else None
    except (TypeError, ValueError, json.JSONDecodeError):
        dependencies = None
    expected = {
        "git_commit": target,
        "kind": "codegraph",
        "status": "ok",
        "target_commit": target,
    }
    if dependencies != [expected] or codegraph[2] != target or codegraph[3] != target:
        raise ReindexManifestProofError("code_vec 没有绑定本次 CodeGraph 成功事实")


def _require_project_id(value: str) -> str:
    try:
        return validate_project_id(value)
    except (ProjectIdError, TypeError, ValueError):
        raise ReindexManifestProofError("项目标识无法验证") from None


def _require_target(value: str) -> str:
    try:
        return require_target_commit(value)
    except (TargetCommitError, TypeError, ValueError):
        raise ReindexManifestProofError("目标提交无法验证") from None


def _require_runtime(value: str) -> str:
    try:
        return require_runtime_revision(value, git_only=True)
    except (RuntimeModelError, TypeError, ValueError):
        raise ReindexManifestProofError("运行时版本无法验证") from None


def _require_manifest_path(value: Path) -> Path:
    try:
        path = Path(value)
        metadata = path.lstat()
    except (OSError, TypeError, ValueError):
        raise ReindexManifestProofError("索引 manifest 路径不可用") from None
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or metadata.st_nlink != 1:
        raise ReindexManifestProofError("索引 manifest 路径不受信任")
    return path


__all__ = [
    "ReindexManifestProof",
    "ReindexManifestProofError",
    "require_all_reindex_manifests_target_ok",
    "require_project_queue_drained",
    "wait_for_all_reindex_manifests",
]
