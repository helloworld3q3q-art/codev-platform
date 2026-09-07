"""四类索引部署验收证明测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from codev_platform.ops.reindex_manifest_proof import (
    ReindexManifestProofError,
    require_all_reindex_manifests_target_ok,
    require_project_queue_drained,
    wait_for_all_reindex_manifests,
)
from codev_platform.reindex.queue_ports import Job, JobMeta, QueueSnapshot, QuarantineRecord


_TARGET = "a" * 40
_RUNTIME = "b" * 40


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE index_builds ("
        "project_id TEXT, kind TEXT, status TEXT, git_commit TEXT, target_commit TEXT, "
        "runtime_revision TEXT, depends_json TEXT, result_digest TEXT, process_rc INTEGER, "
        "validated_at REAL, validation_evidence TEXT, PRIMARY KEY(project_id, kind))"
    )
    for index, kind in enumerate(("chroma", "codegraph", "ingest", "code_vec")):
        dependency = None
        if kind == "code_vec":
            dependency = json.dumps(
                [
                    {
                        "git_commit": _TARGET,
                        "kind": "codegraph",
                        "status": "ok",
                        "target_commit": _TARGET,
                    }
                ],
                sort_keys=True,
                separators=(",", ":"),
            )
        connection.execute(
            "INSERT INTO index_builds VALUES (?, ?, 'ok', ?, ?, ?, ?, ?, 0, ?, "
            "'completion_receipt')",
            ("codev-platform", kind, _TARGET, _TARGET, _RUNTIME, dependency, str(index + 1) * 64, 1.0),
        )
    connection.commit()
    connection.close()


def test_四类记录精确一致时返回无秘密摘要(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    _database(path)

    proof = require_all_reindex_manifests_target_ok(
        "codev-platform",
        _TARGET,
        _RUNTIME,
        path=path,
    )

    assert proof.kinds == ("chroma", "codegraph", "ingest", "code_vec")
    assert len(proof.evidence_sha256) == 64


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("status", "failed"),
        ("target_commit", "c" * 40),
        ("runtime_revision", "d" * 40),
        ("process_rc", 1),
        ("validation_evidence", "self_report"),
        ("result_digest", "short"),
    ],
)
def test_任一构建事实漂移都失败关闭(tmp_path: Path, column: str, value: object) -> None:
    path = tmp_path / "manifest.sqlite"
    _database(path)
    connection = sqlite3.connect(path)
    connection.execute(f"UPDATE index_builds SET {column} = ? WHERE kind = 'ingest'", (value,))
    connection.commit()
    connection.close()

    with pytest.raises(ReindexManifestProofError):
        require_all_reindex_manifests_target_ok(
            "codev-platform",
            _TARGET,
            _RUNTIME,
            path=path,
        )


def test_code_vec必须绑定同一目标CodeGraph(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    _database(path)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE index_builds SET depends_json = ? WHERE kind = 'code_vec'",
        ('[{"kind":"codegraph","status":"ok","git_commit":"old","target_commit":"old"}]',),
    )
    connection.commit()
    connection.close()

    with pytest.raises(ReindexManifestProofError, match="code_vec"):
        require_all_reindex_manifests_target_ok(
            "codev-platform",
            _TARGET,
            _RUNTIME,
            path=path,
        )


def test_读取不存在的manifest不会隐式建库(tmp_path: Path) -> None:
    path = tmp_path / "missing.sqlite"

    with pytest.raises(ReindexManifestProofError):
        require_all_reindex_manifests_target_ok(
            "codev-platform",
            _TARGET,
            _RUNTIME,
            path=path,
        )

    assert not path.exists()


def test_目标项目存在pending或隔离任务时拒绝完成() -> None:
    job = Job(
        "codev-platform",
        "chroma",
        1.0,
        meta=JobMeta(target_commit=_TARGET),
    )
    with pytest.raises(ReindexManifestProofError, match="尚未清空"):
        require_project_queue_drained(QueueSnapshot(pending=[job]), "codev-platform")

    quarantine = QuarantineRecord(
        "codev-platform",
        "chroma",
        "claim",
        "attempt",
        "fence",
        "process",
        "pid",
        "123",
        "failed",
        1.0,
    )
    with pytest.raises(ReindexManifestProofError, match="隔离"):
        require_project_queue_drained(
            QueueSnapshot(quarantined=[quarantine]),
            "codev-platform",
        )


def test_等待会在manifest与队列同时完成后返回(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    _database(path)
    snapshots = iter(
        [
            QueueSnapshot(
                pending=[Job("codev-platform", "chroma", 1.0, meta=JobMeta(target_commit=_TARGET))]
            ),
            QueueSnapshot(),
        ]
    )
    clock = iter((0.0, 0.0, 0.1, 0.2, 0.3))

    proof = wait_for_all_reindex_manifests(
        "codev-platform",
        _TARGET,
        _RUNTIME,
        path=path,
        queue_snapshot=lambda: next(snapshots),
        timeout_sec=10.0,
        monotonic=lambda: next(clock),
        sleep=lambda _seconds: None,
    )

    assert proof.target_commit == _TARGET
