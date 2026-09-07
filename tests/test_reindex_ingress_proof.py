"""Webhook 事件与新索引 attempt 的关联证明测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codev_platform.ops.reindex_ingress_proof import (
    ReindexIngressProofError,
    capture_ingress_manifest_baseline,
    wait_for_ingress_manifests,
)
from codev_platform.reindex.queue_ports import Job, JobMeta, QueueSnapshot


_PROJECT = "codev-platform"
_TARGET = "a" * 40
_RUNTIME = "a" * 40
_KINDS = ("chroma", "ingest")


def _database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE index_builds ("
        "project_id TEXT, kind TEXT, status TEXT, git_commit TEXT, target_commit TEXT, "
        "runtime_revision TEXT, result_digest TEXT, process_rc INTEGER, validated_at REAL, "
        "validation_evidence TEXT, attempt_id TEXT, source TEXT, "
        "PRIMARY KEY(project_id, kind))"
    )
    for index, kind in enumerate(("chroma", "codegraph", "ingest", "code_vec")):
        connection.execute(
            "INSERT INTO index_builds VALUES (?, ?, 'ok', ?, ?, ?, ?, 0, 1.0, "
            "'completion_receipt', ?, 'deployment')",
            (_PROJECT, kind, _TARGET, _TARGET, _RUNTIME, str(index + 1) * 64, f"old-{kind}"),
        )
    connection.commit()
    connection.close()


def _publish_webhook_attempts(path: Path, *, source: str = "webhook") -> None:
    connection = sqlite3.connect(path)
    for index, kind in enumerate(_KINDS):
        connection.execute(
            "UPDATE index_builds SET attempt_id = ?, source = ?, result_digest = ? "
            "WHERE project_id = ? AND kind = ?",
            (f"new-{kind}", source, str(index + 7) * 64, _PROJECT, kind),
        )
    connection.commit()
    connection.close()


def test_只有事件后新attempt且来源为webhook才形成证明(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    _database(path)
    baseline = capture_ingress_manifest_baseline(_PROJECT, path=path)
    _publish_webhook_attempts(path)

    proof = wait_for_ingress_manifests(
        _PROJECT,
        _TARGET,
        _RUNTIME,
        _KINDS,
        baseline,
        path=path,
        queue_snapshot=QueueSnapshot,
        timeout_sec=1.0,
    )

    assert proof.kinds == _KINDS
    assert len(proof.evidence_sha256) == 64


def test_历史manifest即使目标提交一致也不能冒充本次事件(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    _database(path)
    baseline = capture_ingress_manifest_baseline(_PROJECT, path=path)
    clock = iter((0.0, 0.0, 0.1))

    with pytest.raises(ReindexIngressProofError, match="本次 Webhook"):
        wait_for_ingress_manifests(
            _PROJECT,
            _TARGET,
            _RUNTIME,
            _KINDS,
            baseline,
            path=path,
            queue_snapshot=QueueSnapshot,
            timeout_sec=0.1,
            monotonic=lambda: next(clock),
            sleep=lambda _seconds: None,
        )


def test_新attempt不是webhook来源时失败关闭(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    _database(path)
    baseline = capture_ingress_manifest_baseline(_PROJECT, path=path)
    _publish_webhook_attempts(path, source="manual")
    clock = iter((0.0, 0.0, 0.1))

    with pytest.raises(ReindexIngressProofError, match="本次 Webhook"):
        wait_for_ingress_manifests(
            _PROJECT,
            _TARGET,
            _RUNTIME,
            _KINDS,
            baseline,
            path=path,
            queue_snapshot=QueueSnapshot,
            timeout_sec=0.1,
            monotonic=lambda: next(clock),
            sleep=lambda _seconds: None,
        )


def test_新attempt完成但目标项目队列未清空时不能通过(tmp_path: Path) -> None:
    path = tmp_path / "manifest.sqlite"
    _database(path)
    baseline = capture_ingress_manifest_baseline(_PROJECT, path=path)
    _publish_webhook_attempts(path)
    snapshots = iter(
        (
            QueueSnapshot(
                pending=[
                    Job(
                        _PROJECT,
                        "chroma",
                        1.0,
                        meta=JobMeta(source="webhook", target_commit=_TARGET),
                    )
                ]
            ),
            QueueSnapshot(),
        )
    )
    clock = iter((0.0, 0.0, 0.1, 0.2, 0.3))

    proof = wait_for_ingress_manifests(
        _PROJECT,
        _TARGET,
        _RUNTIME,
        _KINDS,
        baseline,
        path=path,
        queue_snapshot=lambda: next(snapshots),
        timeout_sec=1.0,
        monotonic=lambda: next(clock),
        sleep=lambda _seconds: None,
    )

    assert proof.kinds == _KINDS
