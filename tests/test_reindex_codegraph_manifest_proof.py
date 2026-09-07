"""CodeGraph 恢复前的严格只读 manifest 证明回归。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_TARGET = "a" * 40
_RUNTIME_REVISION = "b" * 40


def _manifest(
    path: Path,
    *,
    status: str = "ok",
    target: str = _TARGET,
    runtime_revision: str = _RUNTIME_REVISION,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE index_builds (
                project_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                target_commit TEXT,
                runtime_revision TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO index_builds"
            "(project_id, kind, status, target_commit, runtime_revision) "
            "VALUES (?, ?, ?, ?, ?)",
            ("demo-project", "codegraph", status, target, runtime_revision),
        )


def test_缺失manifest只读证明拒绝且不创建任何文件(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_manifest_proof import (
        CodegraphManifestProofError,
        require_codegraph_manifest_target_ok,
    )

    path = tmp_path / "missing" / "index_manifest.sqlite"

    with pytest.raises(CodegraphManifestProofError):
        require_codegraph_manifest_target_ok(
            "demo-project",
            _TARGET,
            _RUNTIME_REVISION,
            path=path,
        )

    assert not path.exists()
    assert not path.parent.exists()


def test_旧schema缺少target_commit时拒绝(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_manifest_proof import (
        CodegraphManifestProofError,
        require_codegraph_manifest_target_ok,
    )

    path = tmp_path / "index_manifest.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE index_builds (project_id TEXT, kind TEXT, status TEXT)")
        connection.execute(
            "INSERT INTO index_builds(project_id, kind, status) VALUES (?, ?, ?)",
            ("demo-project", "codegraph", "ok"),
        )

    with pytest.raises(CodegraphManifestProofError):
        require_codegraph_manifest_target_ok(
            "demo-project",
            _TARGET,
            _RUNTIME_REVISION,
            path=path,
        )


@pytest.mark.parametrize(
    ("status", "target", "runtime_revision"),
    [
        ("failed", _TARGET, _RUNTIME_REVISION),
        ("ok", "c" * 40, _RUNTIME_REVISION),
        ("ok", _TARGET, "c" * 40),
        ("running", _TARGET, _RUNTIME_REVISION),
    ],
)
def test_非成功或错误目标提交的manifest拒绝(
    tmp_path: Path,
    status: str,
    target: str,
    runtime_revision: str,
) -> None:
    from codev_platform.ops.reindex_codegraph_manifest_proof import (
        CodegraphManifestProofError,
        require_codegraph_manifest_target_ok,
    )

    path = tmp_path / "index_manifest.sqlite"
    _manifest(
        path,
        status=status,
        target=target,
        runtime_revision=runtime_revision,
    )

    with pytest.raises(CodegraphManifestProofError):
        require_codegraph_manifest_target_ok(
            "demo-project",
            _TARGET,
            _RUNTIME_REVISION,
            path=path,
        )


def test_成功且精确目标提交的codegraph_manifest通过(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_codegraph_manifest_proof import (
        require_codegraph_manifest_target_ok,
    )

    path = tmp_path / "index_manifest.sqlite"
    _manifest(path)

    require_codegraph_manifest_target_ok(
        "demo-project",
        _TARGET,
        _RUNTIME_REVISION,
        path=path,
    )


def test_SQLite读取异常失败关闭(tmp_path: Path, monkeypatch) -> None:
    from codev_platform.ops import reindex_codegraph_manifest_proof as proof

    path = tmp_path / "index_manifest.sqlite"
    _manifest(path)

    def fail_connect(*_args, **_kwargs):
        raise sqlite3.OperationalError("模拟只读数据库故障")

    monkeypatch.setattr(proof.sqlite3, "connect", fail_connect)

    with pytest.raises(proof.CodegraphManifestProofError):
        proof.require_codegraph_manifest_target_ok(
            "demo-project",
            _TARGET,
            _RUNTIME_REVISION,
            path=path,
        )
