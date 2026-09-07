"""attempt 完成凭据的绑定、耐久写入与恢复边界测试。"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

from codev_platform.reindex import file_durability
from codev_platform.reindex import attempt_completion as completion_module
from codev_platform.reindex.attempt_completion import (
    AttemptCompletionReceipt,
    attempt_result_digest,
    attempt_spec_digest,
    make_attempt_completion_receipt,
    read_attempt_completion_receipt,
    validate_attempt_completion,
    write_attempt_completion_receipt,
)
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    encode_attempt_result,
    encode_attempt_spec,
    write_attempt_result_atomic,
)

_OID = "a" * 40
_RUNTIME = "b" * 64


def _spec(**changes: object) -> AttemptSpec:
    value = AttemptSpec(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="all",
        input_kind="configured",
        input_payload=CanonicalJsonObject.from_value({"project_id": "demo"}),
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
        timing=(("run", 1.0),),
        log_ref=None,
        proof=CanonicalJsonObject.from_value({"success": True}),
    )
    return dataclasses.replace(value, **changes) if changes else value


def _receipt(
    spec: AttemptSpec | None = None,
    result: AttemptResult | None = None,
    *,
    process_rc: int = 0,
) -> AttemptCompletionReceipt:
    return make_attempt_completion_receipt(
        spec or _spec(),
        result or _result(),
        process_rc=process_rc,
        observed_at=20.0,
    )


def test_digest_严格复用_attempt_codec_规范字节() -> None:
    spec = _spec()
    result = _result()

    assert attempt_spec_digest(spec) == hashlib.sha256(encode_attempt_spec(spec)).hexdigest()
    assert attempt_result_digest(result) == hashlib.sha256(encode_attempt_result(result)).hexdigest()


def test_完成凭据严格往返且不可覆盖(tmp_path: Path) -> None:
    path = tmp_path / "completion.json"
    expected = _receipt()

    write_attempt_completion_receipt(path, expected)
    with pytest.raises(FileExistsError):
        write_attempt_completion_receipt(path, expected)

    assert read_attempt_completion_receipt(path) == expected
    raw = path.read_text(encoding="utf-8")
    assert "claim_token" not in raw
    assert "claim-secret" not in raw


def test_完成凭据复用有界普通文件读取(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "completion.json"
    expected = _receipt()
    write_attempt_completion_receipt(path, expected)
    real_read = file_durability.read_regular_file_bounded
    seen: list[tuple[Path, int]] = []

    def _read(value: Path, *, max_bytes: int) -> bytes:
        seen.append((Path(value), max_bytes))
        return real_read(value, max_bytes=max_bytes)

    monkeypatch.setattr(completion_module, "read_regular_file_bounded", _read)

    assert read_attempt_completion_receipt(path) == expected
    assert seen == [(path, 16 * 1024)]


def test_原始结果落盘但未生成完成凭据时不可恢复(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    receipt_path = tmp_path / "completion.json"
    write_attempt_result_atomic(result_path, _result())

    with pytest.raises(FileNotFoundError):
        read_attempt_completion_receipt(receipt_path)


def test_完成凭据文件同步是发布前线性化屏障(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "completion.json"
    real_fsync = os.fsync
    visible_at_sync: list[bool] = []

    def _record_fsync(fd: int) -> None:
        visible_at_sync.append(path.exists())
        real_fsync(fd)

    monkeypatch.setattr(file_durability.os, "fsync", _record_fsync)

    write_attempt_completion_receipt(path, _receipt())

    assert visible_at_sync[0] is False
    assert path.exists() is True


@pytest.mark.skipif(os.name == "nt", reason="Windows 由 write-through move 提供目录屏障")
def test_完成凭据目录同步失败时不得报告发布成功(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "completion.json"
    calls: list[Path] = []

    def _fail_directory(parent: Path) -> None:
        calls.append(parent)
        raise OSError("注入目录同步失败")

    monkeypatch.setattr(file_durability, "fsync_directory", _fail_directory)

    with pytest.raises(OSError, match="目录同步失败"):
        write_attempt_completion_receipt(path, _receipt())

    assert calls == [tmp_path]
    assert path.exists() is True
    assert read_attempt_completion_receipt(path) == _receipt()


@pytest.mark.parametrize(
    ("spec", "result", "receipt_change"),
    [
        (_spec(fence="other"), _result(), {}),
        (_spec(), _result(note="tampered"), {}),
        (_spec(), _result(), {"process_rc": 1}),
        (_spec(), _result(), {"result_sha256": "d" * 64}),
    ],
)
def test_篡改_spec_result_rc_或摘要都会被拒绝(
    spec: AttemptSpec,
    result: AttemptResult,
    receipt_change: dict[str, object],
) -> None:
    receipt = dataclasses.replace(_receipt(), **receipt_change)

    with pytest.raises(ValueError):
        validate_attempt_completion(spec, result, receipt)


def test_完成凭据拒绝未知字段与无效版本摘要时间和大小(tmp_path: Path) -> None:
    path = tmp_path / "completion.json"
    receipt = _receipt()
    write_attempt_completion_receipt(path, receipt)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["unknown"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        read_attempt_completion_receipt(path)

    invalid_changes = (
        {"schema_version": 2},
        {"spec_sha256": "A" * 64},
        {"observed_at": float("nan")},
        {"process_rc": True},
        {"attempt_id": "x" * 513},
    )
    for changes in invalid_changes:
        with pytest.raises(ValueError):
            dataclasses.replace(receipt, **changes)


def test_非零业务码与有效失败结果可以形成可信凭据() -> None:
    result = _result(
        outcome=AttemptOutcome.FAILED,
        rc=7,
        input_root="",
        proof=CanonicalJsonObject.from_value({"success": False}),
    )
    receipt = _receipt(result=result, process_rc=7)

    assert validate_attempt_completion(_spec(), result, receipt) == 7


def test_完成凭据时间不早于_result_墙钟且恢复时再次校验() -> None:
    result = _result(
        timing=(
            ("started_at", 100.0),
            ("run", 1.0),
            ("finished_at", 120.0),
        ),
    )

    receipt = make_attempt_completion_receipt(
        _spec(),
        result,
        process_rc=0,
        observed_at=90.0,
    )

    assert receipt.observed_at == 120.0
    with pytest.raises(ValueError, match="时间|早于"):
        validate_attempt_completion(
            _spec(),
            result,
            dataclasses.replace(receipt, observed_at=119.0),
        )


@pytest.mark.parametrize(
    "result",
    [
        _result(rc=1),
        _result(proof=CanonicalJsonObject.from_value({"success": False})),
        _result(input_root=""),
    ],
)
def test_成功结果必须同时具备零返回码和成功证明(result: AttemptResult) -> None:
    with pytest.raises(ValueError):
        make_attempt_completion_receipt(
            _spec(),
            result,
            process_rc=result.rc or 0,
            observed_at=20.0,
        )
