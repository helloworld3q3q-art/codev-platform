"""统一索引 manifest (Phase 1) 纯单测 —— 用临时 db 路径, 不碰真 data_root。"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from codev_platform import index_manifest as im
from codev_platform.index_manifest import BuildRecord


def _db(tmp_path):
    return tmp_path / "m.sqlite"


def _create_legacy_manifest(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE index_builds (
            project_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            git_commit TEXT,
            builder_version TEXT,
            started_at REAL,
            finished_at REAL,
            status TEXT,
            file_count INTEGER,
            node_count INTEGER,
            edge_count INTEGER,
            chunk_count INTEGER,
            note TEXT,
            PRIMARY KEY (project_id, kind)
        )
        """
    )
    conn.commit()
    return conn


# ----------------------------------------------------------- record / read

def test_record_and_read_roundtrip(tmp_path):
    db = _db(tmp_path)
    im.record_build(BuildRecord(
        "p1", "chroma", git_commit="abc123", started_at=1.0, finished_at=3.5,
        status="ok", chunk_count=42), path=db)
    recs = im.read_manifest(path=db)
    assert len(recs) == 1
    r = recs[0]
    assert (r.project_id, r.kind, r.status) == ("p1", "chroma", "ok")
    assert r.git_commit == "abc123" and r.chunk_count == 42
    assert r.elapsed_sec == 2.5


def test_record_and_read_roundtrip_with_dependency_fields(tmp_path):
    db = _db(tmp_path)
    rec = BuildRecord(
        "p1",
        "code_vec",
        git_commit="abc123",
        target_commit="def456",
        source="webhook",
        pull_policy="ff_only",
        repo_commits_json='{"main":"abc123"}',
        depends_json='[{"kind":"codegraph","status":"ok","git_commit":"abc123","target_commit":"def456"}]',
        status="ok",
    )

    im.record_build(rec, path=db)

    got = im.read_manifest("p1", path=db)[0]
    assert got.target_commit == "def456"
    assert got.source == "webhook"
    assert got.pull_policy == "ff_only"
    assert got.repo_commits_json == '{"main":"abc123"}'
    assert got.depends_json == '[{"kind":"codegraph","status":"ok","git_commit":"abc123","target_commit":"def456"}]'
    assert im.repo_commits(got) == {"main": "abc123"}
    assert im.dependencies(got) == [
        {"kind": "codegraph", "status": "ok", "git_commit": "abc123", "target_commit": "def456"},
    ]


def test_record_and_read_roundtrip_with_isolated_publish_fields(tmp_path):
    db = _db(tmp_path)
    rec = BuildRecord(
        "p1",
        "chroma",
        attempt_id="attempt-1",
        runtime_revision="a" * 64,
        input_trees_json='{"main":"tree-1"}',
        proof_json='{"success":true}',
        log_ref="logs/attempt-1.log",
        result_digest="b" * 64,
        process_rc=0,
        validated_at=3.0,
        validation_evidence="completion_receipt",
    )

    im.record_build(rec, path=db)

    got = im.latest_build("p1", "chroma", path=db)
    assert got is not None
    assert got.attempt_id == "attempt-1"
    assert got.runtime_revision == "a" * 64
    assert got.input_trees_json == '{"main":"tree-1"}'
    assert got.proof_json == '{"success":true}'
    assert got.log_ref == "logs/attempt-1.log"
    assert got.result_digest == "b" * 64
    assert got.process_rc == 0
    assert got.validated_at == 3.0
    assert got.validation_evidence == "completion_receipt"


def test_build_record_keeps_legacy_positional_constructor_order():
    record = BuildRecord(
        "p1",
        "chroma",
        "git",
        "target",
        "source",
        "policy",
        '{"main":"git"}',
        "[]",
        "legacy-builder",
        1.0,
        2.0,
        "failed",
        1,
        2,
        3,
        4,
        "legacy-note",
    )

    assert record.builder_version == "legacy-builder"
    assert record.started_at == 1.0
    assert record.finished_at == 2.0
    assert record.status == "failed"
    assert record.note == "legacy-note"
    assert record.attempt_id is None
    assert record.result_digest is None
    assert record.process_rc is None
    assert record.validated_at is None
    assert record.validation_evidence is None


def test_replace_keeps_latest(tmp_path):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "graph", git_commit="old", status="ok"), path=db)
    im.record_build(BuildRecord("p1", "graph", git_commit="new", status="failed"), path=db)
    recs = im.read_manifest("p1", path=db)
    assert len(recs) == 1  # 同 (project,kind) 只留最新一次
    assert recs[0].git_commit == "new" and recs[0].status == "failed"


def test_read_missing_db_returns_empty(tmp_path):
    assert im.read_manifest(path=tmp_path / "nope.sqlite") == []


def test_filter_by_project(tmp_path):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "chroma"), path=db)
    im.record_build(BuildRecord("p2", "chroma"), path=db)
    assert {r.project_id for r in im.read_manifest("p1", path=db)} == {"p1"}
    assert len(im.read_manifest(path=db)) == 2


def test_读取_manifest_忽略未来_writer新增列(tmp_path):
    db = _db(tmp_path)
    expected = BuildRecord("p1", "chroma", git_commit="abc123")
    im.record_build(expected, path=db)

    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE index_builds ADD COLUMN future_writer_field TEXT")
    conn.execute("UPDATE index_builds SET future_writer_field = 'future-value'")
    conn.commit()
    conn.close()

    assert im.read_manifest("p1", path=db) == [expected]
    assert im.latest_build("p1", "chroma", path=db) == expected


def test_read_manifest_accepts_legacy_table_without_new_columns(tmp_path):
    db = _db(tmp_path)
    conn = _create_legacy_manifest(db)
    conn.execute(
        """
        INSERT INTO index_builds (
            project_id, kind, git_commit, builder_version, started_at, finished_at,
            status, file_count, node_count, edge_count, chunk_count, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("p1", "codegraph", "abc123", "1", 1.0, 2.0, "ok", 1, 2, 3, 4, "legacy"),
    )
    conn.commit()
    conn.close()

    rec = im.read_manifest("p1", path=db)[0]
    assert rec.target_commit is None
    assert rec.source is None
    assert rec.pull_policy is None
    assert rec.repo_commits_json is None
    assert rec.depends_json is None
    assert rec.attempt_id is None
    assert rec.runtime_revision is None
    assert rec.input_trees_json is None
    assert rec.proof_json is None
    assert rec.log_ref is None
    assert rec.result_digest is None
    assert rec.process_rc is None
    assert rec.validated_at is None
    assert rec.validation_evidence is None


def test_record_build_migrates_legacy_table_by_adding_dependency_columns(tmp_path):
    db = _db(tmp_path)
    conn = _create_legacy_manifest(db)
    conn.close()

    expected = BuildRecord(
        "p1",
        "code_vec",
        target_commit="abc123",
        attempt_id="attempt-legacy-migration",
        runtime_revision="a" * 64,
        input_trees_json='{"main":"tree"}',
        proof_json='{"success":true}',
        log_ref="logs/legacy-migration.log",
        result_digest="b" * 64,
        process_rc=0,
        validated_at=4.0,
    )
    im.record_build(expected, path=db)

    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(index_builds)").fetchall()}
    conn.close()
    assert {
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
    } <= cols
    assert im.latest_build("p1", "code_vec", path=db) == expected


def test_concurrent_publish_migrates_legacy_schema_once_without_losing_writes(tmp_path):
    db = _db(tmp_path)
    _create_legacy_manifest(db).close()
    barrier = threading.Barrier(5)
    outcomes = []
    errors = []

    def publish(index):
        record = _publish_record(
            kind=f"kind-{index}",
            attempt_id=f"attempt-{index}",
            result_digest=f"{index + 1:064x}",
        )
        barrier.wait()
        try:
            outcomes.append(im.publish_build(record, path=db))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=publish, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert outcomes == [im.ManifestPublishOutcome.PUBLISHED] * 4
    assert len(im.read_manifest("p1", path=db)) == 4


def _publish_record(**changes):
    record = BuildRecord(
        "p1",
        "chroma",
        git_commit="a" * 40,
        target_commit="a" * 40,
        repo_commits_json=json.dumps({"main": "a" * 40}, sort_keys=True),
        attempt_id="attempt-1",
        runtime_revision="b" * 64,
        input_trees_json=json.dumps({"main": "c" * 40}, sort_keys=True),
        proof_json='{"success":true}',
        log_ref="logs/attempt-1.log",
        result_digest="d" * 64,
        process_rc=0,
        validated_at=3.0,
        validation_evidence="completion_receipt",
        started_at=1.0,
        finished_at=2.0,
        status="ok",
        note="完成",
    )
    return dataclasses.replace(record, **changes) if changes else record


def test_publish_build_same_attempt_requires_digest_and_all_fields_identical(tmp_path):
    db = _db(tmp_path)
    original = _publish_record()

    assert im.publish_build(original, path=db) is im.ManifestPublishOutcome.PUBLISHED
    assert im.publish_build(original, path=db) is im.ManifestPublishOutcome.IDEMPOTENT
    assert im.publish_build(
        _publish_record(note="内容冲突"),
        path=db,
    ) is im.ManifestPublishOutcome.CONFLICT
    assert im.publish_build(
        _publish_record(result_digest="e" * 64),
        path=db,
    ) is im.ManifestPublishOutcome.CONFLICT

    assert im.latest_build("p1", "chroma", path=db) == original


def test_publish_build_跨版本重放安全回填旧行缺失的验证证据(tmp_path):
    db = _db(tmp_path)
    original = _publish_record()
    assert im.publish_build(original, path=db) is im.ManifestPublishOutcome.PUBLISHED
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE index_builds SET validation_evidence = NULL, builder_version = '2' "
        "WHERE project_id = ? AND kind = ?",
        (original.project_id, original.kind),
    )
    conn.commit()
    conn.close()

    assert im.publish_build(original, path=db) is im.ManifestPublishOutcome.IDEMPOTENT
    assert im.latest_build("p1", "chroma", path=db) == original


def test_publish_build_拒绝降级未来_builder_版本且不覆盖(tmp_path):
    db = _db(tmp_path)
    original = _publish_record()
    assert im.publish_build(original, path=db) is im.ManifestPublishOutcome.PUBLISHED
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE index_builds SET builder_version = '99' "
        "WHERE project_id = ? AND kind = ?",
        (original.project_id, original.kind),
    )
    conn.commit()
    conn.close()

    assert im.publish_build(original, path=db) is im.ManifestPublishOutcome.CONFLICT
    stored = im.latest_build("p1", "chroma", path=db)
    assert stored is not None
    assert stored.builder_version == "99"


def test_builder_version_区分验证证据_schema():
    assert im.BUILDER_VERSION == "3"


def test_publish_build_different_attempt_may_replace_after_external_guard(tmp_path):
    db = _db(tmp_path)
    first = _publish_record()
    second = _publish_record(attempt_id="attempt-2", result_digest="e" * 64, note="第二次")

    assert im.publish_build(first, path=db) is im.ManifestPublishOutcome.PUBLISHED
    assert im.publish_build(second, path=db) is im.ManifestPublishOutcome.PUBLISHED

    assert im.latest_build("p1", "chroma", path=db) == second


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"project_id": ""}, "project_id"),
        ({"kind": "   "}, "kind"),
        ({"attempt_id": 7}, "attempt_id"),
        ({"result_digest": True}, "result_digest"),
        ({"process_rc": True}, "process_rc"),
        ({"process_rc": None}, "process_rc"),
        ({"validated_at": float("nan")}, "validated_at"),
        ({"validated_at": None}, "validated_at"),
        ({"validation_evidence": None}, "validation_evidence"),
        ({"validation_evidence": "dependency_block", "process_rc": 0}, "dependency_block"),
    ],
)
def test_publish_build_rejects_invalid_audit_fields_before_opening_database(
    tmp_path,
    changes,
    field,
):
    db = _db(tmp_path)

    with pytest.raises(ValueError, match=field):
        im.publish_build(_publish_record(**changes), path=db)

    assert not db.exists()


def test_schema_initialization_failure_closes_connection(tmp_path, monkeypatch):
    class FailingConnection:
        closed = False

        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("schema busy")

        def close(self):
            self.closed = True

    connection = FailingConnection()
    monkeypatch.setattr(im.sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with pytest.raises(sqlite3.OperationalError, match="schema busy"):
        im.record_build(BuildRecord("p1", "chroma"), path=_db(tmp_path))

    assert connection.closed is True


def test_publish_build_snapshots_code_vec_dependency_in_same_manifest(tmp_path, monkeypatch):
    db = _db(tmp_path)
    commit = "a" * 40
    codegraph = _publish_record(kind="codegraph")
    code_vec = _publish_record(
        kind="code_vec",
        attempt_id="attempt-code-vec",
        result_digest="e" * 64,
    )

    assert im.publish_build(codegraph, path=db) is im.ManifestPublishOutcome.PUBLISHED
    assert im.publish_build(code_vec, path=db) is im.ManifestPublishOutcome.PUBLISHED

    stored = im.latest_build("p1", "code_vec", path=db)
    assert stored is not None
    assert im.dependencies(stored) == [{
        "git_commit": commit,
        "kind": "codegraph",
        "status": "ok",
        "target_commit": commit,
    }]
    monkeypatch.setattr(im, "git_head", lambda _repo: commit)
    row = {item["kind"]: item for item in im.freshness("p1", "/repo", path=db)}["code_vec"]
    assert row["fresh"] is True
    assert row["dependency_status"] == "ok"


def test_elapsed_none_when_missing_times():
    assert BuildRecord("p", "k").elapsed_sec is None


# ----------------------------------------------------------- freshness

def test_freshness_fresh_stale_unknown(tmp_path, monkeypatch):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "chroma", git_commit="aaa", status="ok",
                                finished_at=1.0), path=db)
    im.record_build(BuildRecord("p1", "graph", git_commit="bbb", status="ok"), path=db)
    # 当前 HEAD == aaa -> chroma 对齐, graph 落后
    monkeypatch.setattr(im, "git_head", lambda repo: "aaa")
    f = {d["kind"]: d for d in im.freshness("p1", "/fake/repo", path=db)}
    assert f["chroma"]["fresh"] is True and f["chroma"]["reason"] == "对齐 HEAD"
    assert f["graph"]["fresh"] is False and "落后" in f["graph"]["reason"]
    # 无 repo -> 无法判定
    f2 = {d["kind"]: d for d in im.freshness("p1", None, path=db)}
    assert f2["chroma"]["fresh"] is None


def test_freshness_marks_code_vec_stale_when_dependency_missing(tmp_path, monkeypatch):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "code_vec", git_commit="aaa", status="ok"), path=db)
    monkeypatch.setattr(im, "git_head", lambda repo: "aaa")

    row = im.freshness("p1", "/fake/repo", path=db)[0]
    assert row["kind"] == "code_vec"
    assert row["fresh"] is False
    assert "dependency" in row["reason"]


def test_freshness_marks_code_vec_stale_when_dependency_metadata_missing_even_if_codegraph_is_fresh(
    tmp_path,
    monkeypatch,
):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "codegraph", git_commit="aaa", status="ok"), path=db)
    im.record_build(BuildRecord("p1", "code_vec", git_commit="aaa", status="ok"), path=db)
    monkeypatch.setattr(im, "git_head", lambda repo: "aaa")

    row = {item["kind"]: item for item in im.freshness("p1", "/fake/repo", path=db)}["code_vec"]

    assert row["kind"] == "code_vec"
    assert row["fresh"] is False
    assert "metadata-missing" in row["reason"]


def test_freshness_keeps_code_vec_fresh_when_dependency_metadata_and_codegraph_are_ok(
    tmp_path,
    monkeypatch,
):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "codegraph", git_commit="aaa", status="ok"), path=db)
    im.record_build(BuildRecord(
        "p1",
        "code_vec",
        git_commit="aaa",
        status="ok",
        depends_json='[{"kind":"codegraph","status":"ok","git_commit":"aaa","target_commit":"aaa"}]',
    ), path=db)
    monkeypatch.setattr(im, "git_head", lambda repo: "aaa")

    row = {item["kind"]: item for item in im.freshness("p1", "/fake/repo", path=db)}["code_vec"]

    assert row["fresh"] is True
    assert row["reason"] == "对齐 HEAD"


def test_record_dependency_ok_accepts_descendant_commit(monkeypatch):
    record = BuildRecord(
        "p1",
        "code_vec",
        git_commit="bbb",
        target_commit="aaa",
        depends_json='[{"kind":"codegraph","status":"ok","git_commit":"bbb","target_commit":"aaa"}]',
    )
    by_kind = {"codegraph": BuildRecord("p1", "codegraph", git_commit="bbb", status="ok")}

    monkeypatch.setattr(im, "_commit_covers", lambda repo, target, indexed: target == "aaa" and indexed == "bbb")

    ok, reason = im.record_dependency_ok(record, by_kind, repo="/fake/repo", target_commit="aaa")

    assert ok is True
    assert reason == "ok"


# ----------------------------------------------------------- CLI 注册

def test_index_command_registered():
    from codev_platform import ops
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    ops.register_all(sub)
    assert "index" in sub.choices, "ops.register_all 应注册 index 子命令"


def test_index_status_repo_path_falls_back_to_repo_specs(monkeypatch, tmp_path):
    from codev_platform.ops import index_status

    def fake_specs(pid, cfg=None):
        return [SimpleNamespace(root=tmp_path, is_main=True)]

    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", fake_specs)

    assert index_status._repo_path({}, "p1", {}) == tmp_path
