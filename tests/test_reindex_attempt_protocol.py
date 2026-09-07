"""重建索引 attempt 协议、严格编解码与恢复日志测试。"""
from __future__ import annotations

import dataclasses
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.reindex import attempts
from codev_platform.reindex import file_durability
from codev_platform.reindex.attempts import (
    AttemptJournalEntry,
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    CleanupReport,
    ConfirmedProcessDeath,
    ValidatedAttemptResult,
    read_attempt_journal,
    read_attempt_result,
    read_attempt_spec,
    validate_attempt_result,
    write_attempt_journal_atomic,
    write_attempt_result_atomic,
    write_attempt_spec_atomic,
)

_OID = "a" * 40
_RUNTIME = "b" * 64


def _payload(project_id: str = "demo") -> CanonicalJsonObject:
    return CanonicalJsonObject.from_value(
        {"project_id": project_id, "repo_targets": {"primary": _OID}},
    )


def _spec(**changes: object) -> AttemptSpec:
    value = AttemptSpec(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="all",
        input_kind="configured",
        input_payload=_payload(),
        target_commit=_OID,
        timeout_sec=30.0,
        runtime_revision=_RUNTIME,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _result(**changes: object) -> AttemptResult:
    value = AttemptResult(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="all",
        input_root="/work/input",
        target_commit=_OID,
        input_commits=(("primary", _OID),),
        input_trees=(("primary", "c" * 40),),
        runtime_revision=_RUNTIME,
        outcome=AttemptOutcome.SUCCEEDED,
        rc=0,
        retryable=False,
        note="",
        timing=(("materialize", 0.25), ("run", 1.5)),
        log_ref="logs/attempt-1.log",
        proof=CanonicalJsonObject.from_value({"success": True, "artifacts": {"count": 2}}),
    )
    return dataclasses.replace(value, **changes) if changes else value


def _journal(**changes: object) -> AttemptJournalEntry:
    value = AttemptJournalEntry(
        schema_version=1,
        owner_token="owner-1",
        claim_token="claim-secret",
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="all",
        spec_path="attempt/spec.json",
        result_path="attempt/result.json",
        pid=1234,
        process_identity="pid:1234:start:9",
        containment_kind="job_object",
        native_ref="job:abcd",
        state="running",
        started_at=10.0,
        timeout_sec=30.0,
    )
    return dataclasses.replace(value, **changes) if changes else value


def _claim(**job_changes: object):
    job = {
        "project_id": "demo",
        "kind": "all",
        "target_commit": _OID,
    }
    job.update(job_changes)
    target_commit = job.pop("target_commit")
    return SimpleNamespace(
        job=SimpleNamespace(**job, meta=SimpleNamespace(target_commit=target_commit)),
        claim_token="claim-secret",
    )


def test_canonical_json_rejects_duplicate_keys_non_finite_non_object_and_oversize() -> None:
    invalid = (
        '{"outer":{"key":1,"key":2}}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '["not-an-object"]',
    )
    for raw in invalid:
        with pytest.raises(ValueError):
            CanonicalJsonObject(raw)

    with pytest.raises(ValueError):
        CanonicalJsonObject('{"value":"too-large"}', max_bytes=8)
    with pytest.raises(ValueError):
        CanonicalJsonObject('{"value":"中"}', max_bytes=16)
    with pytest.raises(ValueError):
        CanonicalJsonObject.from_value({"value": float("nan")})
    with pytest.raises(ValueError):
        CanonicalJsonObject.from_value({1: "dynamic-key"})
    with pytest.raises(ValueError):
        CanonicalJsonObject.from_value({"value": ("dynamic-tuple",)})


def test_canonical_json_sorts_keys_and_has_stable_text() -> None:
    expected = '{"a":{"x":1},"b":2}'
    direct = CanonicalJsonObject('{ "b": 2, "a": {"x": 1} }')
    from_text = CanonicalJsonObject.from_text('{"a":{"x":1},"b":2}')
    from_value = CanonicalJsonObject.from_value({"b": 2, "a": {"x": 1}})

    assert direct == from_text == from_value
    assert direct.text == expected
    assert direct.to_value() == {"a": {"x": 1}, "b": 2}
    with pytest.raises(dataclasses.FrozenInstanceError):
        direct.text = "{}"


def test_attempt_spec_rejects_unknown_input_keys() -> None:
    payloads = (
        {"project_id": "demo", "remote": "origin"},
        {"project_id": "other"},
        {"project_id": "demo", "repo_targets": {"claim_token": "secret"}},
        {"project_id": "demo", "repo_targets": "https://example.invalid/repo"},
        {"project_id": "demo", "repo_targets": {"primary": "C:/workspace"}},
        {"project_id": "demo", "repo_targets": {"primary": "https://example.invalid/repo"}},
    )
    for value in payloads:
        with pytest.raises(ValueError):
            _spec(input_payload=CanonicalJsonObject.from_value(value))
    with pytest.raises(ValueError):
        _spec(input_kind="exact_workspace")


def test_attempt_result_atomic_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    expected = _result()

    write_attempt_result_atomic(path, expected)
    actual = read_attempt_result(path)

    assert actual == expected
    assert type(actual.input_commits) is tuple
    assert type(actual.input_commits[0]) is tuple
    assert type(actual.input_trees) is tuple
    assert type(actual.timing) is tuple
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert type(raw["proof"]) is dict
    assert type(raw["input_commits"]) is list


def test_attempt_result_rejects_partial_or_oversized_json(tmp_path: Path) -> None:
    path = tmp_path / "result.json"
    path.write_bytes(b'{"schema_version":1')
    with pytest.raises(ValueError):
        read_attempt_result(path)

    path.write_bytes(b"{" + b" " * attempts._MAX_ATTEMPT_JSON_BYTES + b"}")
    with pytest.raises(ValueError):
        read_attempt_result(path)


@pytest.mark.parametrize(
    ("spec_changes", "result_changes", "process_rc", "claim_changes"),
    [
        ({}, {"fence": "other"}, 0, {}),
        ({}, {"target_commit": "d" * 40}, 0, {}),
        ({}, {"runtime_revision": "e" * 64}, 0, {}),
        ({}, {"rc": 1, "outcome": AttemptOutcome.FAILED}, 2, {}),
        ({}, {}, 0, {"target_commit": "d" * 40}),
    ],
)
def test_validation_rejects_fence_target_runtime_or_process_rc_mismatch(
    spec_changes: dict[str, object],
    result_changes: dict[str, object],
    process_rc: int,
    claim_changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        validate_attempt_result(
            _spec(**spec_changes),
            _result(**result_changes),
            claim=_claim(**claim_changes),
            process_rc=process_rc,
            validated_at=20.0,
        )


@pytest.mark.parametrize(
    ("input_root", "success"),
    [("/work/input", False), ("", True), ("   ", True)],
)
def test_exit_zero_without_success_proof_is_not_validated(
    input_root: str,
    success: bool,
) -> None:
    missing_success = _result(
        input_root=input_root,
        proof=CanonicalJsonObject.from_value({"success": success}),
    )
    with pytest.raises(ValueError):
        validate_attempt_result(
            _spec(),
            missing_success,
            claim=_claim(),
            process_rc=0,
            validated_at=20.0,
        )


def test_claim_token_is_never_serialized(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    result_path = tmp_path / "result.json"
    write_attempt_spec_atomic(spec_path, _spec())
    write_attempt_result_atomic(result_path, _result())

    assert "claim_token" not in spec_path.read_text(encoding="utf-8")
    assert "claim_token" not in result_path.read_text(encoding="utf-8")
    assert "claim-secret" not in spec_path.read_text(encoding="utf-8")
    assert "claim-secret" not in result_path.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        _result(proof=CanonicalJsonObject.from_value({"nested": {"claim_token": "secret"}}))


def test_models_are_exact_frozen_and_validate_scalar_invariants() -> None:
    models = (
        AttemptSpec,
        AttemptResult,
        ValidatedAttemptResult,
        AttemptJournalEntry,
        ConfirmedProcessDeath,
        CleanupReport,
    )
    for model in models:
        assert model.__dataclass_params__.frozen is True
        assert "__slots__" in model.__dict__
    with pytest.raises(ValueError):
        _spec(schema_version=True)
    with pytest.raises(ValueError):
        _spec(timeout_sec=float("inf"))
    with pytest.raises(ValueError):
        _spec(target_commit="short")
    with pytest.raises(ValueError):
        _result(input_commits=(["primary", _OID],))
    with pytest.raises(ValueError):
        _result(timing=(("run", -1.0),))
    with pytest.raises(ValueError):
        _journal(pid=True)
    with pytest.raises(ValueError):
        ConfirmedProcessDeath("", "job", 1.0, "proof")


def test_journal_strict_round_trip_and_is_only_claim_token_store(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    expected = _journal()
    write_attempt_journal_atomic(path, expected)

    assert read_attempt_journal(path) == expected
    assert json.loads(path.read_text(encoding="utf-8"))["claim_token"] == "claim-secret"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        read_attempt_journal(path)


def test_atomic_writer_preserves_foreign_temp_on_create_collision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "spec.json"
    fixed_hex = "f" * 32
    foreign = tmp_path / f".{path.name}.{fixed_hex}.tmp"
    foreign.write_bytes(b"foreign-temp")
    monkeypatch.setattr(
        file_durability,
        "uuid4",
        lambda: SimpleNamespace(hex=fixed_hex),
    )

    with pytest.raises(FileExistsError):
        write_attempt_spec_atomic(path, _spec())

    assert foreign.read_bytes() == b"foreign-temp"


def test_atomic_writer_fsyncs_parent_and_cleans_owned_temp(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "spec.json"
    seen: list[Path] = []
    monkeypatch.setattr(file_durability, "fsync_directory", seen.append)

    write_attempt_spec_atomic(path, _spec())

    assert seen == ([] if os.name == "nt" else [tmp_path])
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows 使用 write-through move")
def test_fsync_directory_uses_same_descriptor_for_sync_and_close(
    tmp_path: Path,
    monkeypatch,
) -> None:
    opened: list[tuple[Path, int]] = []
    fsynced: list[int] = []
    closed: list[int] = []
    descriptor = 37
    flags = file_durability.os.O_RDONLY | getattr(file_durability.os, "O_DIRECTORY", 0)
    monkeypatch.setattr(
        file_durability.os,
        "open",
        lambda path, got_flags: opened.append((path, got_flags)) or descriptor,
    )
    monkeypatch.setattr(file_durability.os, "fsync", fsynced.append)
    monkeypatch.setattr(file_durability.os, "close", closed.append)

    file_durability.fsync_directory(tmp_path)

    assert opened == [(tmp_path, flags)]
    assert fsynced == [descriptor]
    assert closed == [descriptor]


def test_spec_result_一次写入而_journal_允许原子替换(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    result_path = tmp_path / "result.json"
    journal_path = tmp_path / "journal.json"
    write_attempt_spec_atomic(spec_path, _spec())
    write_attempt_result_atomic(result_path, _result())
    write_attempt_journal_atomic(journal_path, _journal(state="claimed"))

    with pytest.raises(FileExistsError):
        write_attempt_spec_atomic(spec_path, _spec())
    with pytest.raises(FileExistsError):
        write_attempt_result_atomic(result_path, _result(note="second"))
    write_attempt_journal_atomic(journal_path, _journal(state="running"))

    assert read_attempt_journal(journal_path).state == "running"


def test_spec_result_journal_统一复用有界普通文件读取(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = tmp_path / "spec.json"
    result_path = tmp_path / "result.json"
    journal_path = tmp_path / "journal.json"
    write_attempt_spec_atomic(spec_path, _spec())
    write_attempt_result_atomic(result_path, _result())
    write_attempt_journal_atomic(journal_path, _journal())
    real_read = file_durability.read_regular_file_bounded
    seen: list[tuple[Path, int]] = []

    def _read(path: Path, *, max_bytes: int) -> bytes:
        seen.append((Path(path), max_bytes))
        return real_read(path, max_bytes=max_bytes)

    monkeypatch.setattr(attempts, "read_regular_file_bounded", _read)

    assert read_attempt_spec(spec_path) == _spec()
    assert read_attempt_result(result_path) == _result()
    assert read_attempt_journal(journal_path) == _journal()
    assert [path for path, _limit in seen] == [spec_path, result_path, journal_path]
    assert len({limit for _path, limit in seen}) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位测试")
def test_umask_022_下含_claim_token_journal_仍为_0600(tmp_path: Path) -> None:
    path = tmp_path / "journal.json"
    previous = os.umask(0o022)
    try:
        write_attempt_journal_atomic(path, _journal())
    finally:
        os.umask(previous)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_validation_accepts_success_and_rejects_claim_mismatch() -> None:
    spec = _spec()
    result = _result()
    validated = validate_attempt_result(
        spec,
        result,
        claim=_claim(),
        process_rc=0,
        validated_at=20.0,
    )

    assert validated.spec is spec
    assert validated.result is result
    assert validated.claim_token == "claim-secret"
    with pytest.raises(TypeError):
        ValidatedAttemptResult()
    with pytest.raises(TypeError):
        ValidatedAttemptResult(spec, result, "claim-secret", 0, 20.0)
    with pytest.raises(ValueError):
        validate_attempt_result(
            spec,
            result,
            claim=_claim(project_id="other"),
            process_rc=0,
            validated_at=20.0,
        )


def test_spec_and_journal_codecs_reject_wrong_outer_fields(tmp_path: Path) -> None:
    spec_path = tmp_path / "spec.json"
    write_attempt_spec_atomic(spec_path, _spec())
    assert read_attempt_spec(spec_path) == _spec()
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    payload.pop("fence")
    spec_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        read_attempt_spec(spec_path)

    journal_path = tmp_path / "journal.json"
    write_attempt_journal_atomic(journal_path, _journal())
    payload = json.loads(journal_path.read_text(encoding="utf-8"))
    payload["pid"] = True
    journal_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        read_attempt_journal(journal_path)
