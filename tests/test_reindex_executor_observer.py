"""隔离 executor 观察器的进程句柄与完成凭据测试。"""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from codev_platform.reindex import executor_observer
from codev_platform.reindex.attempt_completion import (
    read_attempt_completion_receipt,
    validate_attempt_completion,
)
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    AttemptResult,
    AttemptSpec,
    CanonicalJsonObject,
    write_attempt_result_atomic,
    write_attempt_spec_atomic,
)

_OID = "a" * 40
_RUNTIME = "b" * 64


def _spec() -> AttemptSpec:
    return AttemptSpec(
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


def _paths(tmp_path: Path, result: AttemptResult) -> tuple[Path, Path, Path]:
    spec_path = tmp_path / "spec.json"
    result_path = tmp_path / "result.json"
    receipt_path = tmp_path / "completion.json"
    write_attempt_spec_atomic(spec_path, _spec())
    write_attempt_result_atomic(result_path, result)
    return spec_path, result_path, receipt_path


class _Process:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode
        self.wait_calls = 0
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        self.wait_timeouts.append(timeout)
        return self.returncode


def test_观察器默认不创建输出管道并直接等待内部进程(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, result_path, receipt_path = _paths(tmp_path, _result())
    process = _Process(0)
    captured: dict[str, object] = {}

    def _popen(argv: tuple[str, ...], **kwargs: object) -> _Process:
        captured["argv"] = argv
        captured.update(kwargs)
        return process

    monkeypatch.setattr(executor_observer.subprocess, "Popen", _popen)
    executable = str(Path(sys.executable).resolve())

    receipt = executor_observer.observe_executor(
        spec_path,
        result_path,
        receipt_path,
        (executable, "-m", "codev_platform.reindex.executor"),
        clock=lambda: 20.0,
    )

    assert captured["stdin"] is subprocess.DEVNULL
    assert captured.get("stdout") is None
    assert captured.get("stderr") is None
    assert subprocess.PIPE not in captured.values()
    assert process.wait_calls == 1
    assert process.wait_timeouts == [30.0]
    assert validate_attempt_completion(_spec(), _result(), receipt) == 0


def test_观察器等待内部进程使用_spec_有限预算且超时不写凭据(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, result_path, receipt_path = _paths(tmp_path, _result())

    class _TimeoutProcess:
        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("executor", timeout)

    monkeypatch.setattr(
        executor_observer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _TimeoutProcess(),
    )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        executor_observer.observe_executor(
            spec_path,
            result_path,
            receipt_path,
            (str(Path(sys.executable).resolve()), "-c", "pass"),
        )

    assert raised.value.timeout == 30.0
    assert receipt_path.exists() is False


def test_观察器接受非零业务码的有效失败结果(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = _result(
        outcome=AttemptOutcome.FAILED,
        rc=7,
        input_root="",
        proof=CanonicalJsonObject.from_value({"success": False}),
    )
    spec_path, result_path, receipt_path = _paths(tmp_path, failed)
    monkeypatch.setattr(
        executor_observer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _Process(7),
    )

    receipt = executor_observer.observe_executor(
        spec_path,
        result_path,
        receipt_path,
        (str(Path(sys.executable).resolve()), "-c", "raise SystemExit(7)"),
        clock=lambda: 20.0,
    )

    assert receipt.process_rc == 7
    assert read_attempt_completion_receipt(receipt_path) == receipt


def test_wall_clock_回拨时凭据仍不早于_executor_完成时间(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _result(
        timing=(
            ("started_at", 100.0),
            ("run", 1.0),
            ("finished_at", 120.0),
        ),
    )
    spec_path, result_path, receipt_path = _paths(tmp_path, result)
    process = _Process(0)
    monkeypatch.setattr(
        executor_observer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )

    receipt = executor_observer.observe_executor(
        spec_path,
        result_path,
        receipt_path,
        (str(Path(sys.executable).resolve()), "-c", "pass"),
        clock=lambda: 90.0,
    )

    assert receipt.observed_at == 120.0
    assert receipt.process_rc == 0
    assert process.wait_timeouts == [30.0]


@pytest.mark.parametrize(
    ("result", "process_rc"),
    [
        (_result(), 1),
        (_result(rc=1), 0),
        (_result(rc=None), 0),
        (_result(proof=CanonicalJsonObject.from_value({"success": False})), 0),
    ],
)
def test_返回码或成功证明不匹配时绝不生成凭据(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: AttemptResult,
    process_rc: int,
) -> None:
    spec_path, result_path, receipt_path = _paths(tmp_path, result)
    monkeypatch.setattr(
        executor_observer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _Process(process_rc),
    )

    with pytest.raises(ValueError):
        executor_observer.observe_executor(
            spec_path,
            result_path,
            receipt_path,
            (str(Path(sys.executable).resolve()), "-c", "pass"),
            clock=lambda: 20.0,
        )

    assert receipt_path.exists() is False


def test_内部进程未写结果时绝不生成凭据(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path = tmp_path / "spec.json"
    result_path = tmp_path / "missing-result.json"
    receipt_path = tmp_path / "completion.json"
    write_attempt_spec_atomic(spec_path, _spec())
    monkeypatch.setattr(
        executor_observer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _Process(1),
    )

    with pytest.raises(FileNotFoundError):
        executor_observer.observe_executor(
            spec_path,
            result_path,
            receipt_path,
            (str(Path(sys.executable).resolve()), "-c", "pass"),
        )

    assert receipt_path.exists() is False


def test_观察器拒绝相对可执行文件且不启动进程(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, result_path, receipt_path = _paths(tmp_path, _result())

    def _unexpected(*_args: object, **_kwargs: object) -> _Process:
        raise AssertionError("不应启动相对路径可执行文件")

    monkeypatch.setattr(executor_observer.subprocess, "Popen", _unexpected)

    with pytest.raises(ValueError, match="绝对路径"):
        executor_observer.observe_executor(
            spec_path,
            result_path,
            receipt_path,
            ("python", "-c", "pass"),
        )


@pytest.mark.parametrize(
    "suffix",
    [
        ("contains\x00nul",),
        ("x" * (1024 * 1024),),
        tuple("x" for _ in range(1024)),
    ],
)
def test_观察器拒绝含_nul_或无界命令且不启动进程(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: tuple[str, ...],
) -> None:
    spec_path, result_path, receipt_path = _paths(tmp_path, _result())

    def _unexpected(*_args: object, **_kwargs: object) -> _Process:
        raise AssertionError("非法命令不应启动进程")

    monkeypatch.setattr(executor_observer.subprocess, "Popen", _unexpected)

    with pytest.raises(ValueError, match="命令|参数|NUL|上限"):
        executor_observer.observe_executor(
            spec_path,
            result_path,
            receipt_path,
            (str(Path(sys.executable).resolve()), *suffix),
        )


def test_cli_有效失败结果仍返回观察成功(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = _result(
        outcome=AttemptOutcome.FAILED,
        rc=3,
        input_root="",
        proof=CanonicalJsonObject.from_value({"success": False}),
    )
    spec_path, result_path, receipt_path = _paths(tmp_path, failed)
    monkeypatch.setattr(
        executor_observer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _Process(3),
    )

    rc = executor_observer.main([
        "--spec", str(spec_path),
        "--result", str(result_path),
        "--receipt", str(receipt_path),
        "--",
        str(Path(sys.executable).resolve()),
        "-c",
        "raise SystemExit(3)",
    ])

    assert rc == 0
    assert receipt_path.exists() is True
