"""统一索引构建 manifest —— roadmap-2026-06-07 Phase 1 (MVP 切片)。

**问题**: 索引新鲜度此前分散在三处格式 —— chroma `.last_build.{pid}.json`、graph
`ingest_meta` 表、codegraph.db mtime —— 没有"这个项目每类索引是否新鲜/上次构建成功否"
的统一真值。

**MVP**: 一张 sqlite 表 `index_builds`, 每个 `(project_id, kind)` 一行 = **最近一次**
构建记录 (commit / 起止时间 / 状态)。reindex worker 构建完写一行 (best-effort, 失败不阻断
索引); 读侧给 CLI `index status` + health 用。`freshness()` 把记录里的 commit 与仓库当前
HEAD 比, 答 "索引是否落后工作树"。

**刻意不做** (留后续 / plan 重型部分): 构建 DAG 编排、atomic handoff、dashboard、
node/chunk 计数回填 (worker 拿不到, count 列留空, 后续 runner 补)。MVP 只解决"可信新鲜度真值"。
"""
from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

# manifest 写入逻辑 / schema 版本 (升级时 bump, 供审计 "这行是哪版 builder 写的")。
BUILDER_VERSION = "1"

# 已知索引 kind (与 reindex runners / job.kind 对齐; 开放枚举, 未知 kind 也照记不报错)。
KNOWN_KINDS = ("chroma", "codegraph", "graph", "docs")

_DDL = """
CREATE TABLE IF NOT EXISTS index_builds (
    project_id      TEXT NOT NULL,
    kind            TEXT NOT NULL,
    git_commit      TEXT,
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


@dataclass
class BuildRecord:
    """一次索引构建的记录 (一个 project × 一类索引)。"""
    project_id: str
    kind: str
    git_commit: str | None = None
    builder_version: str = BUILDER_VERSION
    started_at: float | None = None
    finished_at: float | None = None
    status: str = "ok"               # ok | failed
    file_count: int | None = None
    node_count: int | None = None
    edge_count: int | None = None
    chunk_count: int | None = None
    note: str = ""

    @property
    def elapsed_sec(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return round(self.finished_at - self.started_at, 2)


def _manifest_path(path: Path | None) -> Path:
    if path is not None:
        return path
    from codev_platform.core.paths import index_manifest_path
    return index_manifest_path()


def _connect(path: Path | None = None) -> sqlite3.Connection:
    p = _manifest_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.execute(_DDL)
    return conn


def record_build(rec: BuildRecord, *, path: Path | None = None) -> None:
    """写一行 (按 (project_id, kind) INSERT OR REPLACE = 留最新一次)。"""
    conn = _connect(path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO index_builds
                 (project_id, kind, git_commit, builder_version, started_at, finished_at,
                  status, file_count, node_count, edge_count, chunk_count, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec.project_id, rec.kind, rec.git_commit, rec.builder_version,
             rec.started_at, rec.finished_at, rec.status, rec.file_count,
             rec.node_count, rec.edge_count, rec.chunk_count, rec.note),
        )
        conn.commit()
    finally:
        conn.close()


def read_manifest(project_id: str | None = None, *, path: Path | None = None) -> list[BuildRecord]:
    """读全部 (或某 project) 的构建记录, 按 project_id, kind 排序。库不存在 → 空列表。"""
    p = _manifest_path(path)
    if not p.exists():
        return []
    conn = _connect(path)
    try:
        if project_id is None:
            cur = conn.execute("SELECT * FROM index_builds ORDER BY project_id, kind")
        else:
            cur = conn.execute(
                "SELECT * FROM index_builds WHERE project_id = ? ORDER BY kind", (project_id,)
            )
        cols = [c[0] for c in cur.description]
        return [BuildRecord(**dict(zip(cols, row))) for row in cur.fetchall()]
    finally:
        conn.close()


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


def freshness(project_id: str, repo: Path | str | None, *,
              path: Path | None = None) -> list[dict]:
    """每类索引: 记录里的 commit vs 仓库当前 HEAD。

    返回 [{kind, status, git_commit, elapsed_sec, finished_at, head, fresh, reason}, ...]。
    - fresh=True: 记录 commit == 当前 HEAD (索引追平工作树 HEAD)。
    - fresh=False: commit 落后 (HEAD 已变, 索引未重建)。
    - fresh=None: 无法判定 (无 repo / 拿不到 HEAD / 记录无 commit)。
    """
    head = git_head(repo) if repo is not None else None
    out: list[dict] = []
    for rec in read_manifest(project_id, path=path):
        if head is None or rec.git_commit is None:
            fresh: bool | None = None
            reason = "无法判定 (缺 HEAD 或记录无 commit)"
        elif rec.git_commit == head:
            fresh, reason = True, "对齐 HEAD"
        else:
            fresh, reason = False, f"落后 (索引@{rec.git_commit[:8]} != HEAD@{head[:8]})"
        out.append({
            "kind": rec.kind, "status": rec.status, "git_commit": rec.git_commit,
            "elapsed_sec": rec.elapsed_sec, "finished_at": rec.finished_at,
            "head": head, "fresh": fresh, "reason": reason,
        })
    return out
