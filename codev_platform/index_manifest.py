"""统一索引构建 manifest —— roadmap-2026-06-07 Phase 1 (MVP 切片)。

**问题**: 索引新鲜度此前分散在三处格式 —— chroma `.last_build.{pid}.json`、graph
`ingest_meta` 表、codegraph.db mtime —— 没有"这个项目每类索引是否新鲜/上次构建成功否"
的统一真值。

一张 sqlite 表 `index_builds` 记录每个 `(project_id, kind)` 的最近构建事实。legacy worker
继续通过 `record_build` 保持替换语义；隔离执行路径只通过 `publish_build`，在单个
`BEGIN IMMEDIATE` 事务内比较完整 attempt 记录并写入。同一 attempt 任一持久字段不同都
失败关闭，不覆盖既有真值；不同 attempt 的期望版本围栏由上层 QueuePort 持有。

读侧给 CLI `index status` 与 health 使用。`freshness()` 比较记录 commit、当前 HEAD 和
code_vec 的 codegraph 依赖快照；发布记录同时保存规范结果摘要、观察返回码与验证时间，
不把 executor 自报结果单独当作成功真值。
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from codev_platform.index_kind_contract import REQUIRED_INDEX_KINDS

# manifest 写入逻辑 / schema 版本 (升级时 bump, 供审计 "这行是哪版 builder 写的")。
BUILDER_VERSION = "3"
DEFAULT_MANIFEST_BUSY_TIMEOUT_SEC = 10.0
MAX_MANIFEST_BUSY_TIMEOUT_SEC = 30.0

# 持久化层保留旧名称兼容；生产必需集合由中立契约维护。
KNOWN_KINDS = REQUIRED_INDEX_KINDS

_DDL = """
CREATE TABLE IF NOT EXISTS index_builds (
    project_id      TEXT NOT NULL,
    kind            TEXT NOT NULL,
    git_commit      TEXT,
    target_commit   TEXT,
    source          TEXT,
    pull_policy     TEXT,
    repo_commits_json TEXT,
    depends_json    TEXT,
    attempt_id      TEXT,
    runtime_revision TEXT,
    input_trees_json TEXT,
    proof_json      TEXT,
    log_ref         TEXT,
    result_digest   TEXT,
    process_rc      INTEGER,
    validated_at    REAL,
    validation_evidence TEXT,
    builder_version TEXT,
    started_at      REAL,
    finished_at     REAL,
    status          TEXT,          -- ok | failed
    file_count      INTEGER,
    node_count      INTEGER,
    edge_count      INTEGER,
    chunk_count     INTEGER,
    note            TEXT,
    PRIMARY KEY (project_id, kind)
)
"""

_OPTIONAL_COLUMNS = (
    ("target_commit", "TEXT"),
    ("source", "TEXT"),
    ("pull_policy", "TEXT"),
    ("repo_commits_json", "TEXT"),
    ("depends_json", "TEXT"),
    ("attempt_id", "TEXT"),
    ("runtime_revision", "TEXT"),
    ("input_trees_json", "TEXT"),
    ("proof_json", "TEXT"),
    ("log_ref", "TEXT"),
    ("result_digest", "TEXT"),
    ("process_rc", "INTEGER"),
    ("validated_at", "REAL"),
    ("validation_evidence", "TEXT"),
)

_BUILD_COLUMNS = (
    "project_id",
    "kind",
    "git_commit",
    "target_commit",
    "source",
    "pull_policy",
    "repo_commits_json",
    "depends_json",
    "attempt_id",
    "runtime_revision",
    "input_trees_json",
    "proof_json",
    "log_ref",
    "result_digest",
    "process_rc",
    "validated_at",
    "validation_evidence",
    "builder_version",
    "started_at",
    "finished_at",
    "status",
    "file_count",
    "node_count",
    "edge_count",
    "chunk_count",
    "note",
)
_RESULT_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_SQLITE_INT_MIN = -(2**63)
_SQLITE_INT_MAX = 2**63 - 1


@dataclass
class BuildRecord:
    """一次索引构建的记录 (一个 project × 一类索引)。"""
    project_id: str
    kind: str
    git_commit: str | None = None
    target_commit: str | None = None
    source: str | None = None
    pull_policy: str | None = None
    repo_commits_json: str | None = None
    depends_json: str | None = None
    builder_version: str = BUILDER_VERSION
    started_at: float | None = None
    finished_at: float | None = None
    status: str = "ok"               # ok | failed
    file_count: int | None = None
    node_count: int | None = None
    edge_count: int | None = None
    chunk_count: int | None = None
    note: str = ""
    attempt_id: str | None = None
    runtime_revision: str | None = None
    input_trees_json: str | None = None
    proof_json: str | None = None
    log_ref: str | None = None
    result_digest: str | None = None
    process_rc: int | None = None
    validated_at: float | None = None
    validation_evidence: str | None = None

    @property
    def elapsed_sec(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return round(self.finished_at - self.started_at, 2)


class ManifestPublishOutcome(str, Enum):
    """受围栏发布在 manifest 事务中的确定结果。"""

    PUBLISHED = "published"
    IDEMPOTENT = "idempotent"
    CONFLICT = "conflict"


def _manifest_path(path: Path | None) -> Path:
    if path is not None:
        return path
    from codev_platform.core.paths import index_manifest_path
    return index_manifest_path()


def validate_manifest_busy_timeout(value: object) -> float:
    """校验发布锁等待预算，避免隐式或异常长阻塞。"""
    if (
        type(value) is not float
        or not math.isfinite(value)
        or not 0.0 <= value <= MAX_MANIFEST_BUSY_TIMEOUT_SEC
    ):
        raise ValueError(
            "manifest busy_timeout_sec 必须是 0 到 "
            f"{MAX_MANIFEST_BUSY_TIMEOUT_SEC:g} 秒的有限浮点数"
        )
    return value


def _connect(
    path: Path | None = None,
    *,
    busy_timeout_sec: float = DEFAULT_MANIFEST_BUSY_TIMEOUT_SEC,
) -> sqlite3.Connection:
    timeout = validate_manifest_busy_timeout(busy_timeout_sec)
    p = _manifest_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=timeout)
    try:
        conn.execute(_DDL)
        _ensure_optional_columns(conn)
    except BaseException:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    return conn


def _ensure_optional_columns(conn: sqlite3.Connection) -> None:
    cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(index_builds)").fetchall()}
    missing = [(name, ddl) for name, ddl in _OPTIONAL_COLUMNS if name not in cols]
    if not missing:
        conn.commit()
        return
    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = {str(row[1]) for row in conn.execute("PRAGMA table_info(index_builds)")}
        for name, ddl in missing:
            if name not in current:
                conn.execute(f"ALTER TABLE index_builds ADD COLUMN {name} {ddl}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _decode_json_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _decode_json_list(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _record_values(rec: BuildRecord) -> tuple[object, ...]:
    return tuple(getattr(rec, column) for column in _BUILD_COLUMNS)


def _write_record(conn: sqlite3.Connection, rec: BuildRecord) -> None:
    columns = ", ".join(_BUILD_COLUMNS)
    placeholders = ", ".join("?" for _column in _BUILD_COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO index_builds ({columns}) VALUES ({placeholders})",
        _record_values(rec),
    )


def record_build(rec: BuildRecord, *, path: Path | None = None) -> None:
    """保留 legacy 的按项目与类型替换最新记录语义。"""
    conn = _connect(path)
    try:
        _write_record(conn, rec)
        conn.commit()
    finally:
        conn.close()


def _select_build(
    conn: sqlite3.Connection,
    project_id: str,
    kind: str,
) -> BuildRecord | None:
    columns = ", ".join(_BUILD_COLUMNS)
    row = conn.execute(
        f"SELECT {columns} FROM index_builds WHERE project_id = ? AND kind = ?",
        (project_id, kind),
    ).fetchone()
    if row is None:
        return None
    return BuildRecord(**dict(zip(_BUILD_COLUMNS, row, strict=True)))


def _code_vec_dependency_json(conn: sqlite3.Connection, rec: BuildRecord) -> str:
    """在发布事务中读取 codegraph，冻结 code_vec 对应的依赖事实。"""
    dependency = _select_build(conn, rec.project_id, "codegraph")
    payload = {
        "kind": "codegraph",
        "status": dependency.status if dependency is not None else "missing",
        "git_commit": dependency.git_commit if dependency is not None else None,
        "target_commit": (
            dependency.target_commit
            if dependency is not None and dependency.target_commit
            else rec.target_commit
        ),
    }
    return json.dumps([payload], ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _with_dependency_snapshot(
    conn: sqlite3.Connection,
    rec: BuildRecord,
    existing: BuildRecord | None = None,
) -> BuildRecord:
    if rec.kind != "code_vec" or rec.depends_json is not None:
        return rec
    if existing is not None and existing.attempt_id == rec.attempt_id:
        # 依赖快照属于首次发布事实；同一 attempt 重放必须复用，不能受后来构建漂移影响。
        return replace(rec, depends_json=existing.depends_json)
    return replace(rec, depends_json=_code_vec_dependency_json(conn, rec))


def _validate_publish_record(rec: BuildRecord) -> None:
    if type(rec) is not BuildRecord:
        raise TypeError("publish_build 只接受 BuildRecord")
    for field in ("project_id", "kind", "attempt_id"):
        value = getattr(rec, field)
        if type(value) is not str or not value.strip():
            raise ValueError(f"发布记录的 {field} 无效")
    if (
        type(rec.result_digest) is not str
        or _RESULT_DIGEST_RE.fullmatch(rec.result_digest) is None
    ):
        raise ValueError("发布记录的 result_digest 无效")
    if rec.status not in {"ok", "failed"}:
        raise ValueError("发布记录状态必须是 ok 或 failed")
    if rec.validation_evidence == "completion_receipt":
        if (
            type(rec.process_rc) is not int
            or not _SQLITE_INT_MIN <= rec.process_rc <= _SQLITE_INT_MAX
        ):
            raise ValueError("completion_receipt 发布记录的 process_rc 无效")
    elif rec.validation_evidence == "dependency_block":
        if rec.process_rc is not None or rec.status != "failed":
            raise ValueError("dependency_block 发布记录不得伪造进程或成功状态")
    else:
        raise ValueError("发布记录的 validation_evidence 无效")
    if (
        type(rec.validated_at) is not float
        or not math.isfinite(rec.validated_at)
        or rec.validated_at < 0.0
    ):
        raise ValueError("发布记录的 validated_at 无效")


def _publish_in_transaction(
    conn: sqlite3.Connection,
    rec: BuildRecord,
) -> ManifestPublishOutcome:
    existing = _select_build(conn, rec.project_id, rec.kind)
    candidate = _with_dependency_snapshot(conn, rec, existing)
    if existing is not None and existing.attempt_id == candidate.attempt_id:
        if _record_values(existing) == _record_values(candidate):
            return ManifestPublishOutcome.IDEMPOTENT
        if (
            existing.builder_version == "2"
            and candidate.builder_version == BUILDER_VERSION
            and existing.validation_evidence is None
            and candidate.validation_evidence == "completion_receipt"
            and type(existing.process_rc) is int
        ):
            migrated = replace(
                existing,
                builder_version=BUILDER_VERSION,
                validation_evidence="completion_receipt",
            )
            if _record_values(migrated) == _record_values(candidate):
                _write_record(conn, candidate)
                return ManifestPublishOutcome.IDEMPOTENT
        return ManifestPublishOutcome.CONFLICT
    _write_record(conn, candidate)
    return ManifestPublishOutcome.PUBLISHED


def publish_build(
    rec: BuildRecord,
    *,
    path: Path | None = None,
    busy_timeout_sec: float = DEFAULT_MANIFEST_BUSY_TIMEOUT_SEC,
) -> ManifestPublishOutcome:
    """在单个 SQLite 写事务内完成同 attempt 比较与发布。"""
    _validate_publish_record(rec)
    conn = _connect(path, busy_timeout_sec=busy_timeout_sec)
    try:
        conn.execute("BEGIN IMMEDIATE")
        outcome = _publish_in_transaction(conn, rec)
        if outcome is ManifestPublishOutcome.CONFLICT:
            conn.rollback()
        else:
            conn.commit()
        return outcome
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_manifest(
    project_id: str | None = None,
    *,
    path: Path | None = None,
    busy_timeout_sec: float = DEFAULT_MANIFEST_BUSY_TIMEOUT_SEC,
) -> list[BuildRecord]:
    """读全部 (或某 project) 的构建记录, 按 project_id, kind 排序。库不存在 → 空列表。"""
    p = _manifest_path(path)
    if not p.exists():
        return []
    conn = _connect(path, busy_timeout_sec=busy_timeout_sec)
    try:
        columns = ", ".join(_BUILD_COLUMNS)
        if project_id is None:
            cur = conn.execute(f"SELECT {columns} FROM index_builds ORDER BY project_id, kind")
        else:
            cur = conn.execute(
                f"SELECT {columns} FROM index_builds WHERE project_id = ? ORDER BY kind",
                (project_id,),
            )
        return [
            BuildRecord(**dict(zip(_BUILD_COLUMNS, row, strict=True)))
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


def latest_build(
    project_id: str,
    kind: str,
    *,
    path: Path | None = None,
    busy_timeout_sec: float = DEFAULT_MANIFEST_BUSY_TIMEOUT_SEC,
) -> BuildRecord | None:
    """读某 project + kind 的最新记录。不存在时返回 None。"""
    if busy_timeout_sec == DEFAULT_MANIFEST_BUSY_TIMEOUT_SEC:
        records = read_manifest(project_id, path=path)
    else:
        records = read_manifest(
            project_id,
            path=path,
            busy_timeout_sec=busy_timeout_sec,
        )
    for rec in records:
        if rec.kind == kind:
            return rec
    return None


def git_head(repo: Path | str) -> str | None:
    """仓库当前 HEAD commit (best-effort; 非 git 仓 / git 缺失 → None)。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        pass
    return None


def repo_commits(record: BuildRecord) -> dict:
    return _decode_json_dict(record.repo_commits_json)


def dependencies(record: BuildRecord) -> list[dict]:
    return _decode_json_list(record.depends_json)


def _commit_covers(repo: Path | str | None, target: str | None, indexed: str | None) -> bool:
    if not target or not indexed:
        return False
    target = target.strip()
    indexed = indexed.strip()
    if not target or not indexed:
        return False
    if target == indexed:
        return True
    if repo is None:
        return False
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", target, indexed],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        return False
    return out.returncode == 0


def record_dependency_ok(
    record: BuildRecord,
    by_kind: dict[str, BuildRecord],
    repo: Path | str | None = None,
    target_commit: str | None = None,
) -> tuple[bool, str]:
    if record.kind != "code_vec":
        return True, "ok"
    dep = next((item for item in dependencies(record) if item.get("kind") == "codegraph"), None)
    latest = by_kind.get("codegraph")
    if dep is None:
        return False, "dependency:codegraph:metadata-missing"

    dep_status = str(dep.get("status") or "")
    if dep_status == "missing":
        return False, "dependency:codegraph:missing"
    if dep_status and dep_status != "ok":
        return False, f"dependency:codegraph:{dep_status}"
    if latest is None:
        return False, "dependency:codegraph:missing"

    latest_commit = latest.git_commit
    dep_commit = str(dep.get("git_commit") or latest_commit or "").strip() or None
    dep_target = (
        target_commit
        or str(dep.get("target_commit") or "").strip()
        or record.target_commit
        or latest.target_commit
    )
    if latest.status != "ok":
        return False, f"dependency:codegraph:{latest.status}"
    if not _commit_covers(repo, dep_target, dep_commit):
        return False, "dependency:codegraph:stale"
    if not _commit_covers(repo, dep_target, latest.git_commit):
        return False, "dependency:codegraph:stale"
    return True, "ok"


def freshness(project_id: str, repo: Path | str | None, *,
              path: Path | None = None) -> list[dict]:
    """每类索引: 记录里的 commit vs 仓库当前 HEAD。

    返回 [{kind, status, git_commit, elapsed_sec, finished_at, head, fresh, reason}, ...]。
    - fresh=True: 记录 commit == 当前 HEAD (索引追平工作树 HEAD)。
    - fresh=False: commit 落后 (HEAD 已变, 索引未重建)。
    - fresh=None: 无法判定 (无 repo / 拿不到 HEAD / 记录无 commit)。
    """
    head = git_head(repo) if repo is not None else None
    records = read_manifest(project_id, path=path)
    by_kind = {rec.kind: rec for rec in records}
    out: list[dict] = []
    for rec in records:
        if head is None or rec.git_commit is None:
            fresh: bool | None = None
            reason = "无法判定 (缺 HEAD 或记录无 commit)"
            dependency_status = None
        elif rec.status != "ok":
            fresh, reason = False, rec.status
            dependency_status = None
        elif _commit_covers(repo, head, rec.git_commit):
            fresh, reason = True, "对齐 HEAD"
            dependency_status = None
        else:
            fresh, reason = False, f"落后 (索引@{rec.git_commit[:8]} != HEAD@{head[:8]})"
            dependency_status = None
        if rec.kind == "code_vec" and fresh is True:
            dep_ok, dep_reason = record_dependency_ok(rec, by_kind, repo=repo, target_commit=head)
            if not dep_ok:
                fresh = False
                reason = f"code_vec:{dep_reason}"
                dependency_status = dep_reason.rsplit(":", 1)[-1]
            else:
                dependency_status = "ok"
        out.append({
            "kind": rec.kind, "status": rec.status, "git_commit": rec.git_commit,
            "elapsed_sec": rec.elapsed_sec, "finished_at": rec.finished_at,
            "head": head, "fresh": fresh, "reason": reason,
            "dependency_status": dependency_status,
        })
    return out
