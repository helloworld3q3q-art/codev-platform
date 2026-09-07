"""隔离 attempt 结果发布器的边界、幂等与故障测试。"""

from __future__ import annotations

import ast
import dataclasses
import json
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform import index_manifest as manifest
from codev_platform.reindex import result_publisher as publisher_module
from codev_platform.reindex.attempt_completion import attempt_result_digest
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    validate_attempt_result,
)
from codev_platform.reindex.result_publisher import PublishReceipt, ResultPublisher

_COMMIT = "a" * 40
_TREE = "b" * 40
_RUNTIME = "c" * 64


def _validated(
    *,
    kind: str = "chroma",
    attempt_id: str = "attempt-1",
    outcome: AttemptOutcome = AttemptOutcome.SUCCEEDED,
    note: str = "完成",
    claim_token: str = "claim-secret",
    fence: str = "fence-secret",
    log_ref: str | None = None,
    proof_detail: str | None = None,
):
    spec = AttemptSpec(
        schema_version=1,
        attempt_id=attempt_id,
        fence=fence,
        project_id="demo",
        kind=kind,
        input_kind="configured",
        input_payload=CanonicalJsonObject.from_value(
            {
                "project_id": "demo",
                "repo_targets": {"primary": _COMMIT},
            }
        ),
        target_commit=_COMMIT,
        timeout_sec=30.0,
        runtime_revision=_RUNTIME,
    )
    succeeded = outcome is AttemptOutcome.SUCCEEDED
    process_rc = 0 if succeeded else 1
    proof = {"success": succeeded, "count": 2}
    if proof_detail is not None:
        proof["detail"] = proof_detail
    result = AttemptResult(
        schema_version=1,
        attempt_id=attempt_id,
        fence=fence,
        project_id="demo",
        kind=kind,
        input_root="/work/input",
        target_commit=_COMMIT,
        input_commits=(("main", _COMMIT),),
        input_trees=(("main", _TREE),),
        runtime_revision=_RUNTIME,
        outcome=outcome,
        rc=process_rc,
        retryable=outcome in {AttemptOutcome.RETRYABLE, AttemptOutcome.TIMED_OUT},
        note=note,
        timing=(("started_at", 10.0), ("runner", 1.5), ("finished_at", 12.0)),
        log_ref=log_ref or f"logs/{attempt_id}.log",
        proof=CanonicalJsonObject.from_value(proof),
    )
    claim = SimpleNamespace(
        claim_token=claim_token,
        job=SimpleNamespace(
            project_id="demo",
            kind=kind,
            meta=SimpleNamespace(target_commit=_COMMIT),
        ),
    )
    return validate_attempt_result(
        spec,
        result,
        claim=claim,
        process_rc=process_rc,
        validated_at=13.0,
    )


def _publisher(tmp_path: Path) -> ResultPublisher:
    return ResultPublisher(manifest_path=tmp_path / "manifest.sqlite")


def test_result_publisher_module_does_not_import_queue_layer():
    source = Path(publisher_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        name.name for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names
    }
    imported_modules.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    imported_modules.update(
        name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for name in node.names
    )

    assert not any(
        part == "queue" or part.startswith("queue_")
        for module in imported_modules
        for part in module.split(".")
    )


def test_publisher_type_boundary_rejects_raw_attempt_result(tmp_path):
    validated = _validated()

    with pytest.raises(TypeError, match="ValidatedAttemptResult"):
        _publisher(tmp_path).publish(validated.result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fence", "wrong-fence"),
        ("target_commit", "d" * 40),
        ("runtime_revision", "e" * 64),
    ],
)
def test_wrong_fence_target_or_runtime_never_writes_manifest(tmp_path, field, value):
    validated = _validated()
    changed = dataclasses.replace(validated.result, **{field: value})
    object.__setattr__(validated, "result", changed)

    receipt = _publisher(tmp_path).publish(validated)

    assert receipt.published is False
    assert "身份" in receipt.note
    assert manifest.read_manifest(path=tmp_path / "manifest.sqlite") == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_id", "wrong-attempt"),
        ("project_id", "wrong-project"),
        ("kind", "wrong-kind"),
    ],
)
def test_other_identity_mismatch_never_writes_manifest(tmp_path, field, value):
    validated = _validated()
    changed = dataclasses.replace(validated.result, **{field: value})
    object.__setattr__(validated, "result", changed)

    receipt = _publisher(tmp_path).publish(validated)

    assert receipt.published is False
    assert "身份" in receipt.note
    assert manifest.read_manifest(path=tmp_path / "manifest.sqlite") == []


def test_nonterminal_outcomes_never_write_manifest(tmp_path):
    publisher = _publisher(tmp_path)

    for outcome in (
        AttemptOutcome.RETRYABLE,
        AttemptOutcome.TIMED_OUT,
        AttemptOutcome.QUARANTINED,
        AttemptOutcome.SUPERSEDED,
    ):
        receipt = publisher.publish(_validated(outcome=outcome))
        assert receipt.published is False
        assert "不发布" in receipt.note

    assert manifest.read_manifest(path=tmp_path / "manifest.sqlite") == []


def test_failed_terminal_result_is_published(tmp_path):
    publisher = _publisher(tmp_path)

    receipt = publisher.publish(_validated(outcome=AttemptOutcome.FAILED))

    assert receipt.published is True
    stored = manifest.latest_build("demo", "chroma", path=tmp_path / "manifest.sqlite")
    assert stored is not None
    assert stored.status == "failed"


def test_same_attempt_publish_is_idempotent(tmp_path):
    publisher = _publisher(tmp_path)
    validated = _validated()

    first = publisher.publish(validated)
    second = publisher.publish(validated)

    assert first.published is True
    assert second.published is True
    assert first.result_digest == attempt_result_digest(validated.result)
    assert second.result_digest == first.result_digest
    assert "幂等" in second.note
    assert len(manifest.read_manifest(path=tmp_path / "manifest.sqlite")) == 1


def test_same_attempt_different_result_conflicts_without_overwrite(tmp_path):
    publisher = _publisher(tmp_path)
    first = _validated(note="第一次")
    conflict = _validated(note="冲突内容")

    assert publisher.publish(first).published is True
    receipt = publisher.publish(conflict)

    assert receipt.published is False
    assert "冲突" in receipt.note
    stored = manifest.latest_build("demo", "chroma", path=tmp_path / "manifest.sqlite")
    assert stored is not None
    assert stored.note == "第一次"
    assert stored.result_digest == attempt_result_digest(first.result)


def test_same_result_digest_but_different_validation_fact_conflicts(tmp_path):
    publisher = _publisher(tmp_path)
    first = _validated()
    conflict = _validated()
    object.__setattr__(conflict, "validated_at", 14.0)

    assert publisher.publish(first).published is True
    receipt = publisher.publish(conflict)

    assert receipt.published is False
    assert receipt.result_digest == attempt_result_digest(first.result)
    assert "冲突" in receipt.note
    stored = manifest.latest_build("demo", "chroma", path=tmp_path / "manifest.sqlite")
    assert stored is not None
    assert stored.validated_at == 13.0


def test_concurrent_same_attempt_conflict_has_one_winner(tmp_path):
    publisher = _publisher(tmp_path)
    values = (_validated(note="并发甲"), _validated(note="并发乙"))
    barrier = threading.Barrier(3)
    receipts = []

    def publish(value):
        barrier.wait()
        receipts.append(publisher.publish(value))

    threads = [threading.Thread(target=publish, args=(value,)) for value in values]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(receipt.published for receipt in receipts) == 1
    assert sum("冲突" in receipt.note for receipt in receipts) == 1
    stored = manifest.latest_build("demo", "chroma", path=tmp_path / "manifest.sqlite")
    assert stored is not None
    assert stored.note in {"并发甲", "并发乙"}


def test_manifest_write_failure_returns_unpublished_receipt(tmp_path, monkeypatch):
    def fail_write(*_args, **_kwargs):
        raise sqlite3.OperationalError("包含 fence-secret 与 claim-secret 的底层错误")

    monkeypatch.setattr("codev_platform.reindex.result_publisher.publish_build", fail_write)

    receipt = _publisher(tmp_path).publish(_validated())

    assert receipt.published is False
    assert receipt.result_digest == attempt_result_digest(_validated().result)
    assert receipt.note == "清单写入失败"
    assert "fence" not in receipt.note
    assert "claim" not in receipt.note


def test_manifest_adapter_runtime_error_returns_unpublished_receipt(tmp_path, monkeypatch):
    def fail_write(*_args, **_kwargs):
        raise RuntimeError("包含 fence-secret 与 claim-secret 的适配器错误")

    monkeypatch.setattr("codev_platform.reindex.result_publisher.publish_build", fail_write)

    receipt = _publisher(tmp_path).publish(_validated())

    assert receipt.published is False
    assert receipt.note == "清单写入失败"


def test_manifest_adapter_memory_error_is_not_hidden(tmp_path, monkeypatch):
    def fail_write(*_args, **_kwargs):
        raise MemoryError

    monkeypatch.setattr("codev_platform.reindex.result_publisher.publish_build", fail_write)

    with pytest.raises(MemoryError):
        _publisher(tmp_path).publish(_validated())


@pytest.mark.parametrize(
    "validated",
    [
        _validated(note="错误包含 fence-secret"),
        _validated(note="错误包含 claim-secret"),
        _validated(log_ref="logs/fence-secret.log"),
        _validated(proof_detail="claim-secret"),
        _validated(claim_token="机密令牌", proof_detail="机密令牌"),
        _validated(fence="围栏秘密", proof_detail="围栏秘密"),
    ],
)
def test_sensitive_fence_or_claim_value_never_reaches_manifest(tmp_path, validated):
    db = tmp_path / "manifest.sqlite"

    receipt = ResultPublisher(manifest_path=db).publish(validated)

    assert receipt.published is False
    assert receipt.result_digest == attempt_result_digest(validated.result)
    assert receipt.note == "结果包含不可发布的敏感内容"
    assert "fence-secret" not in receipt.note
    assert "claim-secret" not in receipt.note
    assert not db.exists()


def test_manifest_busy_respects_injected_timeout_and_stays_unpublished(tmp_path):
    db = tmp_path / "manifest.sqlite"
    manifest.record_build(manifest.BuildRecord("demo", "seed"), path=db)
    blocker = sqlite3.connect(db, timeout=0.0)
    blocker.execute("BEGIN IMMEDIATE")
    started_at = time.monotonic()
    try:
        receipt = ResultPublisher(
            manifest_path=db,
            busy_timeout_sec=0.05,
        ).publish(_validated())
    finally:
        elapsed = time.monotonic() - started_at
        blocker.rollback()
        blocker.close()

    assert receipt.published is False
    assert receipt.note == "清单写入失败"
    assert elapsed < 0.8
    assert manifest.latest_build("demo", "chroma", path=db) is None


@pytest.mark.parametrize(
    "busy_timeout_sec",
    [True, 1, -0.01, float("nan"), float("inf"), 30.01],
)
def test_manifest_busy_timeout_rejects_unbounded_or_ambiguous_values(
    tmp_path,
    busy_timeout_sec,
):
    with pytest.raises(ValueError, match="busy_timeout_sec"):
        ResultPublisher(
            manifest_path=tmp_path / "manifest.sqlite",
            busy_timeout_sec=busy_timeout_sec,
        )


def test_unknown_manifest_outcome_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "codev_platform.reindex.result_publisher.publish_build",
        lambda *_args, **_kwargs: object(),
    )

    receipt = _publisher(tmp_path).publish(_validated())

    assert receipt.published is False
    assert receipt.result_digest == attempt_result_digest(_validated().result)
    assert receipt.note == "清单返回未知发布结果"


def test_manifest_contains_attempt_runtime_input_proof_and_digest(tmp_path):
    db = tmp_path / "manifest.sqlite"
    validated = _validated(claim_token="claim-never-store")

    receipt = ResultPublisher(manifest_path=db).publish(validated)

    assert receipt.published is True
    stored = manifest.latest_build("demo", "chroma", path=db)
    assert stored is not None
    assert stored.attempt_id == "attempt-1"
    assert stored.runtime_revision == _RUNTIME
    assert json.loads(stored.repo_commits_json or "null") == {"main": _COMMIT}
    assert json.loads(stored.input_trees_json or "null") == {"main": _TREE}
    assert json.loads(stored.proof_json or "null") == {"count": 2, "success": True}
    assert stored.log_ref == "logs/attempt-1.log"
    assert stored.result_digest == attempt_result_digest(validated.result)
    assert stored.process_rc == 0
    assert stored.validated_at == 13.0
    assert stored.started_at == 10.0
    assert stored.finished_at == 12.0
    raw_database = db.read_bytes()
    assert b"claim-never-store" not in raw_database
    assert b"fence-secret" not in raw_database
    assert not hasattr(receipt, "claim_token")
    assert not hasattr(receipt, "fence")


def test_receipt_rejects_published_state_without_result_digest():
    with pytest.raises(ValueError, match="result_digest"):
        PublishReceipt(True, "attempt-1", None, "错误组合")


@pytest.mark.parametrize("result_digest", ["A" * 64, "a" * 63, True])
def test_receipt_rejects_noncanonical_result_digest(result_digest):
    with pytest.raises(ValueError, match="result_digest"):
        PublishReceipt(False, "attempt-1", result_digest, "错误摘要")


def test_result_digest_failure_returns_explicit_unpublished_receipt(tmp_path):
    validated = _validated()
    broken = dataclasses.replace(validated.result)
    object.__setattr__(broken, "note", object())
    object.__setattr__(validated, "result", broken)

    receipt = _publisher(tmp_path).publish(validated)

    assert receipt.published is False
    assert receipt.result_digest is None
    assert receipt.note == "结果无法计算规范摘要"
    assert not (tmp_path / "manifest.sqlite").exists()


def test_code_vec_publish_preserves_dependency_freshness(tmp_path, monkeypatch):
    db = tmp_path / "manifest.sqlite"
    publisher = ResultPublisher(manifest_path=db)

    assert publisher.publish(_validated(kind="codegraph", attempt_id="attempt-graph")).published
    assert publisher.publish(_validated(kind="code_vec", attempt_id="attempt-vec")).published

    vector = manifest.latest_build("demo", "code_vec", path=db)
    assert vector is not None
    assert manifest.dependencies(vector) == [
        {
            "git_commit": _COMMIT,
            "kind": "codegraph",
            "status": "ok",
            "target_commit": _COMMIT,
        }
    ]
    monkeypatch.setattr(manifest, "git_head", lambda _repo: _COMMIT)
    rows = {row["kind"]: row for row in manifest.freshness("demo", "/repo", path=db)}
    assert rows["code_vec"]["fresh"] is True
    assert rows["code_vec"]["dependency_status"] == "ok"


def test_code_vec_idempotent_replay_keeps_first_dependency_snapshot(tmp_path):
    db = tmp_path / "manifest.sqlite"
    publisher = ResultPublisher(manifest_path=db)
    vector = _validated(kind="code_vec", attempt_id="attempt-vec")

    assert publisher.publish(_validated(kind="codegraph", attempt_id="graph-ok")).published
    assert publisher.publish(vector).published
    assert publisher.publish(
        _validated(
            kind="codegraph",
            attempt_id="graph-failed",
            outcome=AttemptOutcome.FAILED,
        ),
    ).published

    replay = publisher.publish(vector)

    assert replay.published is True
    assert "幂等" in replay.note
    stored = manifest.latest_build("demo", "code_vec", path=db)
    assert stored is not None
    assert manifest.dependencies(stored)[0]["status"] == "ok"


def test_code_vec_new_attempt_snapshots_latest_failed_dependency(tmp_path):
    db = tmp_path / "manifest.sqlite"
    publisher = ResultPublisher(manifest_path=db)

    assert publisher.publish(
        _validated(
            kind="codegraph",
            attempt_id="graph-failed",
            outcome=AttemptOutcome.FAILED,
        ),
    ).published
    assert publisher.publish(
        _validated(kind="code_vec", attempt_id="attempt-vec"),
    ).published

    stored = manifest.latest_build("demo", "code_vec", path=db)
    assert stored is not None
    assert manifest.dependencies(stored)[0]["status"] == "failed"
