"""CodeGraph 恢复前的严格只读 manifest 证明。

恢复 CodeGraph 会启动一个会执行 ``catchUpSync`` 的写入者，因此不能仅凭维护标记或
索引目录存在就恢复服务。本模块只在 SQLite 只读连接中核验 ``codegraph`` 的最近构建
事实；不复用 ``index_manifest`` 的读写入口，避免读取旧库时隐式建库或迁移 schema。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from codev_platform.core.paths import index_manifest_path
from codev_platform.core.project_id import ProjectIdError, validate as validate_project_id
from codev_platform.core.runtime_models import RuntimeModelError, require_runtime_revision
from codev_platform.reindex.target_commit import TargetCommitError, require_target_commit


_REQUIRED_COLUMNS = frozenset({"project_id", "kind", "status", "target_commit", "runtime_revision"})


class CodegraphManifestProofError(RuntimeError):
    """CodeGraph manifest 无法严格证明为目标提交成功构建。"""


def require_codegraph_manifest_target_ok(
    project_id: str,
    target_commit: str,
    runtime_revision: str,
    *,
    path: Path | None = None,
) -> None:
    """证明 CodeGraph manifest 已成功且精确对应 ``target_commit``。

    任何路径、连接、schema 或记录异常均失败关闭。此函数绝不创建目录、数据库或 schema，
    供显式 ``resume-codegraph`` 控制面在解除维护保持前调用。
    """
    normalized_project_id = _require_project_id(project_id)
    normalized_target_commit = _require_target_commit(target_commit)
    normalized_runtime_revision = _require_runtime_revision(runtime_revision)
    manifest_path = _require_absolute_manifest_path(path)

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{manifest_path.as_uri()}?mode=ro",
            uri=True,
            timeout=0.0,
        )
        columns = {
            str(row[1]).lower() for row in connection.execute("PRAGMA table_info(index_builds)")
        }
        if not _REQUIRED_COLUMNS.issubset(columns):
            raise CodegraphManifestProofError("CodeGraph manifest schema 无法证明")

        rows = connection.execute(
            "SELECT status, target_commit, runtime_revision FROM index_builds "
            "WHERE project_id = ? AND kind = 'codegraph'",
            (normalized_project_id,),
        ).fetchall()
    except CodegraphManifestProofError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError):
        raise CodegraphManifestProofError("CodeGraph manifest 只读验证失败") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    if len(rows) != 1:
        raise CodegraphManifestProofError("CodeGraph manifest 缺少唯一构建记录")

    status, recorded_target_commit, recorded_runtime_revision = rows[0]
    if (
        status != "ok"
        or recorded_target_commit != normalized_target_commit
        or recorded_runtime_revision != normalized_runtime_revision
    ):
        raise CodegraphManifestProofError("CodeGraph manifest 未证明目标提交与运行时版本构建成功")


def _require_project_id(project_id: str) -> str:
    try:
        return validate_project_id(project_id)
    except (ProjectIdError, TypeError, ValueError):
        raise CodegraphManifestProofError("项目标识无法验证") from None


def _require_target_commit(target_commit: str) -> str:
    try:
        return require_target_commit(target_commit)
    except (TargetCommitError, TypeError, ValueError):
        raise CodegraphManifestProofError("目标提交无法验证") from None


def _require_runtime_revision(runtime_revision: str) -> str:
    try:
        return require_runtime_revision(runtime_revision, git_only=True)
    except (RuntimeModelError, TypeError, ValueError):
        raise CodegraphManifestProofError("运行时版本无法验证") from None


def _require_absolute_manifest_path(path: Path | None) -> Path:
    try:
        manifest_path = index_manifest_path() if path is None else Path(path)
    except (OSError, TypeError, ValueError):
        raise CodegraphManifestProofError("CodeGraph manifest 路径不可用") from None
    if not manifest_path.is_absolute():
        raise CodegraphManifestProofError("CodeGraph manifest 路径必须为绝对路径")
    return manifest_path


__all__ = ["CodegraphManifestProofError", "require_codegraph_manifest_target_ok"]
