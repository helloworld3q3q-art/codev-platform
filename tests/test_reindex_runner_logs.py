from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from codev_platform.reindex.runner_logs import (
    TRUNCATION_MARKER,
    RunnerLogBuffer,
    redact_runner_output,
    run_logged_process,
)
from codev_platform.reindex import runner_logs


def test_redacts_common_secret_shapes() -> None:
    output = "\n".join(
        [
            "Authorization: Bearer secret-token-123",
            "Authorization: token github-header-secret",
            "api_key=model-key-sample",
            "GITHUB_TOKEN=github-env-secret",
            "DATABASE_PASSWORD=db-env-secret",
            "AWS_SECRET_ACCESS_KEY=aws-secret-env",
            '{"password":"json-secret","safe":"value"}',
            '{"password":"json secret;with,delimiters&more","safe":"value"}',
            "password='quoted secret;with spaces,and&delimiters'",
            "password: local-pass",
            "dsn=postgresql://alice:db-pass@example.test/db",
            "plain=https://bob:url-pass@example.test/path",
        ]
    )

    redacted = redact_runner_output(output)

    assert "secret-token-123" not in redacted
    assert "github-header-secret" not in redacted
    assert "model-key-sample" not in redacted
    assert "github-env-secret" not in redacted
    assert "db-env-secret" not in redacted
    assert "aws-secret-env" not in redacted
    assert "json-secret" not in redacted
    assert "json secret" not in redacted
    assert "with,delimiters" not in redacted
    assert "delimiters&more" not in redacted
    assert "quoted secret" not in redacted
    assert "with spaces" not in redacted
    assert "local-pass" not in redacted
    assert "alice:db-pass" not in redacted
    assert "bob:url-pass" not in redacted
    assert "Authorization: Bearer <redacted>" in redacted
    assert "Authorization: token <redacted>" in redacted
    assert "api_key=<redacted>" in redacted
    assert "GITHUB_TOKEN=<redacted>" in redacted
    assert "DATABASE_PASSWORD=<redacted>" in redacted
    assert "AWS_SECRET_ACCESS_KEY=<redacted>" in redacted
    assert '"password":"<redacted>"' in redacted
    assert "password='<redacted>'" in redacted
    assert "password: <redacted>" in redacted
    assert "dsn=<redacted>" in redacted
    assert "plain=https://<redacted>@example.test/path" in redacted


def test_runner_log_buffer_caps_output_and_preserves_head_tail(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    buf = RunnerLogBuffer(max_bytes=420)

    buf.feed_text("HEAD: runner started\n")
    buf.feed_text("middle line\n" * 200)
    buf.feed_text("TAIL: proof: code_vec ok token=tail-secret\n")
    buf.write_to(log_path)

    written = log_path.read_text(encoding="utf-8")
    assert len(written.encode("utf-8")) <= 420
    assert "HEAD: runner started" in written
    assert "proof: code_vec ok" in written
    assert TRUNCATION_MARKER.strip() in written
    assert "tail-secret" not in written


def test_runner_log_buffer_truncated_output_stays_valid_utf8(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    buf = RunnerLogBuffer(max_bytes=123)

    buf.feed_text("头" * 200)
    buf.feed_text("\nTAIL: proof: code_vec ok\n")
    buf.write_to(log_path)

    written = log_path.read_text(encoding="utf-8")
    assert len(written.encode("utf-8")) <= 123
    assert "proof: code_vec ok" in written


def test_run_logged_process_writes_bounded_redacted_log(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    rc = run_logged_process(
        [
            sys.executable,
            "-c",
            (
                "import sys;"
                "print('HEAD: runner started');"
                "print('Authorization: Bearer live-secret');"
                "sys.stdout.write('middle line\\n' * 200);"
                "print('TAIL: proof: code_vec ok token=tail-secret')"
            ),
        ],
        timeout=5,
        log_path=log_path,
        max_bytes=460,
    )

    written = log_path.read_text(encoding="utf-8")
    assert rc == 0
    assert len(written.encode("utf-8")) <= 460
    assert "HEAD: runner started" in written
    assert "proof: code_vec ok" in written
    assert "live-secret" not in written
    assert "tail-secret" not in written


@pytest.mark.parametrize(
    "timeout", [None, 0, -1, float("nan"), float("inf"), 1e308, True],
)
def test_run_logged_process_rejects_unbounded_timeout_before_spawn(
    monkeypatch, tmp_path, timeout,
) -> None:
    monkeypatch.setattr(
        runner_logs,
        "popen_tree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得启动进程")),
    )

    with pytest.raises(ValueError, match="timeout"):
        run_logged_process(
            ["fake"],
            timeout=timeout,
            log_path=tmp_path / "runner.log",
        )


def test_run_logged_process_observer_receives_untruncated_stream(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    observed = bytearray()
    middle = "MIDDLE-PROOF-SIGNAL"
    script = (
        "import sys;"
        "sys.stdout.write('A'*300000);"
        f"sys.stdout.write('{middle}');"
        "sys.stdout.write('B'*300000)"
    )

    assert run_logged_process(
        [sys.executable, "-c", script],
        timeout=10,
        log_path=log_path,
        max_bytes=1024,
        output_observer=observed.extend,
    ) == 0

    assert middle.encode() in observed
    assert len(observed) > len(log_path.read_bytes())


def test_run_logged_process_fails_if_reader_cannot_finish(monkeypatch, tmp_path) -> None:
    class _Stdout:
        def read(self, _size: int) -> bytes:
            return b""

        def close(self) -> None:
            return None

    class _Process:
        stdout = _Stdout()

        def wait(self, timeout=None) -> int:
            return 0

    process = _Process()
    killed: list[object] = []
    events: list[str] = []
    reader_timeouts: list[float | None] = []
    monkeypatch.setattr(runner_logs, "popen_tree", lambda *_args, **_kwargs: process)

    def _unfinished_reader(*args) -> bool:
        reader_timeouts.append(args[3] if len(args) > 3 else None)
        return False

    monkeypatch.setattr(runner_logs, "_finish_reader", _unfinished_reader)
    monkeypatch.setattr(
        runner_logs,
        "kill_process_tree",
        lambda target, **_kwargs: (events.append("kill"), killed.append(target)),
    )
    monkeypatch.setattr(
        runner_logs.RunnerLogBuffer,
        "write_to",
        lambda *_args, **_kwargs: events.append("write"),
    )

    with pytest.raises(RuntimeError, match="reader|读取"):
        run_logged_process(
            [sys.executable, "-c", "pass"],
            timeout=5,
            log_path=tmp_path / "runner.log",
        )
    assert killed == [process]
    assert events.index("kill") < events.index("write")
    assert reader_timeouts[0] is not None


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_run_logged_process_fails_if_reader_breaks_after_early_marker(
    monkeypatch, tmp_path, error_type,
) -> None:
    class _Stdout:
        calls = 0

        def read(self, _size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"proof: chroma ok\n"
            raise error_type("pipe broken")

        def close(self) -> None:
            return None

    class _Process:
        stdout = _Stdout()

        def wait(self, timeout=None) -> int:
            return 0

    monkeypatch.setattr(runner_logs, "popen_tree", lambda *_args, **_kwargs: _Process())

    with pytest.raises(RuntimeError, match="读取"):
        run_logged_process(
            [sys.executable, "-c", "pass"],
            timeout=5,
            log_path=tmp_path / "runner.log",
        )


def test_repeated_observer_errors_keep_only_bounded_state() -> None:
    errors: list[Exception] = []

    def _broken_observer(_chunk: bytes) -> None:
        raise RuntimeError("observer failed")

    for _ in range(100):
        runner_logs._notify_observer(_broken_observer, b"chunk", errors)

    assert len(errors) == 1


def test_run_logged_process_does_not_leak_long_secret_suffix(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    secret = "A" * 20_000 + "LEAKME_LONG_SECRET"
    rc = run_logged_process(
        [
            sys.executable,
            "-c",
            (
                "import sys;"
                f"sys.stdout.write('token={secret}\\n');"
                "sys.stdout.write('TAIL: proof: code_vec ok\\n')"
            ),
        ],
        timeout=5,
        log_path=log_path,
        max_bytes=30000,
    )

    written = log_path.read_text(encoding="utf-8")
    assert rc == 0
    assert "LEAKME_LONG_SECRET" not in written
    assert "token=<redacted>" in written
    assert "proof: code_vec ok" in written


def test_run_logged_process_does_not_flush_oversized_quoted_secret(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    rc = run_logged_process(
        [
            sys.executable,
            "-c",
            (
                "import sys;"
                "secret = 'Q' * 100000 + 'LEAKME_OVERSIZED_QUOTED';"
                "sys.stdout.write('password=\"' + secret + '\"\\n');"
                "sys.stdout.write('TAIL: proof: code_vec ok\\n')"
            ),
        ],
        timeout=5,
        log_path=log_path,
        max_bytes=12000,
    )

    written = log_path.read_text(encoding="utf-8")
    assert rc == 0
    assert "LEAKME_OVERSIZED_QUOTED" not in written
    assert "QQQQ" not in written
    assert "超长单行已在脱敏前截断" in written
    assert "proof: code_vec ok" in written


def test_run_logged_process_captures_stderr_and_redacts(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    rc = run_logged_process(
        [
            sys.executable,
            "-c",
            "import sys; print('FAIL: password=stderr-secret', file=sys.stderr); sys.exit(3)",
        ],
        timeout=5,
        log_path=log_path,
    )

    written = log_path.read_text(encoding="utf-8")
    assert rc == 3
    assert "stderr-secret" not in written
    assert "password=<redacted>" in written


def test_timeout_does_not_wait_on_descendant_holding_stdout(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    marker_path = tmp_path / "descendant-survived.txt"
    child_script = (
        "import pathlib, time;"
        "print('child holding stdout', flush=True);"
        "time.sleep(1.2);"
        f"pathlib.Path({str(marker_path)!r}).write_text('alive', encoding='utf-8')"
    )
    script = (
        "import subprocess, sys, time;"
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]);"
        "print('parent waiting', flush=True);"
        "time.sleep(6)"
    )

    start = time.perf_counter()
    with pytest.raises(subprocess.TimeoutExpired):
        run_logged_process([sys.executable, "-c", script], timeout=0.2, log_path=log_path)
    elapsed = time.perf_counter() - start

    assert elapsed < 3
    assert log_path.exists()
    time.sleep(1.5)
    assert not marker_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows 由外层 Job Object 清理已脱离根进程的后代")
def test_normal_parent_exit_still_kills_group_holding_stdout(tmp_path) -> None:
    log_path = tmp_path / "runner.log"
    marker_path = tmp_path / "escaped-after-parent.txt"
    child_script = (
        "import pathlib, time;"
        "time.sleep(1.2);"
        f"pathlib.Path({str(marker_path)!r}).write_text('alive', encoding='utf-8')"
    )
    script = (
        "import subprocess, sys;"
        f"subprocess.Popen([sys.executable, '-c', {child_script!r}]);"
        "print('parent exits', flush=True)"
    )

    with pytest.raises(RuntimeError, match="读取线程"):
        run_logged_process(
            [sys.executable, "-c", script],
            timeout=5,
            cleanup_timeout_sec=0.5,
            log_path=log_path,
        )

    time.sleep(1.4)
    assert not marker_path.exists()


def test_timeout_process_wait_and_reader_cleanup_share_one_bound(monkeypatch, tmp_path) -> None:
    class _Stdout:
        def read(self, _size: int) -> bytes:
            return b""

        def close(self) -> None:
            raise AssertionError("主线程不得依赖关闭 stdout 唤醒 reader")

    class _Process:
        args = ("fake",)
        stdout = _Stdout()

        def __init__(self) -> None:
            self.wait_timeouts: list[float | None] = []

        def wait(self, timeout=None) -> int:
            self.wait_timeouts.append(timeout)
            raise subprocess.TimeoutExpired(self.args, timeout)

    proc = _Process()
    monkeypatch.setattr(runner_logs, "popen_tree", lambda *_args, **_kwargs: proc)
    monkeypatch.setattr(runner_logs, "kill_process_tree", lambda *_args, **_kwargs: None)

    with pytest.raises(subprocess.TimeoutExpired):
        run_logged_process(
            ["fake"],
            timeout=0.01,
            cleanup_timeout_sec=0.02,
            log_path=tmp_path / "runner.log",
        )

    assert proc.wait_timeouts[0] == 0.01
    assert proc.wait_timeouts[1] is not None
    assert 0 <= proc.wait_timeouts[1] <= 0.02


def test_reader_finish_does_not_close_possibly_blocking_stdout() -> None:
    class _Stdout:
        def close(self) -> None:
            raise AssertionError("不得在控制线程关闭可能正被读取的 stdout")

    class _Process:
        stdout = _Stdout()

    class _Reader:
        def __init__(self) -> None:
            self.join_timeouts: list[float | None] = []

        def join(self, timeout=None) -> None:
            self.join_timeouts.append(timeout)

        def is_alive(self) -> bool:
            return True

    class _Stream:
        def finish(self) -> None:
            raise AssertionError("reader 未结束时不得由主线程并发 finish")

    reader = _Reader()

    assert runner_logs._finish_reader(
        _Process(), reader, _Stream(), timeout_sec=0.01,
    ) is False
    assert reader.join_timeouts == [0.01]


def test_timeout_reader_cleanup_error_does_not_replace_timeout(monkeypatch, tmp_path) -> None:
    class _Stdout:
        def read(self, _size: int) -> bytes:
            return b""

    class _Process:
        args = ("fake",)
        stdout = _Stdout()

        def wait(self, timeout=None) -> int:
            raise subprocess.TimeoutExpired(self.args, timeout)

    monkeypatch.setattr(runner_logs, "popen_tree", lambda *_args, **_kwargs: _Process())
    monkeypatch.setattr(runner_logs, "kill_process_tree", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        runner_logs,
        "_finish_reader",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("reader cleanup failed")),
    )

    with pytest.raises(subprocess.TimeoutExpired):
        run_logged_process(
            ["fake"],
            timeout=0.01,
            cleanup_timeout_sec=0.02,
            log_path=tmp_path / "runner.log",
        )


def test_timeout_second_wait_error_does_not_replace_timeout(monkeypatch, tmp_path) -> None:
    class _Stdout:
        def read(self, _size: int) -> bytes:
            return b""

    class _Process:
        args = ("fake",)
        stdout = _Stdout()

        def __init__(self) -> None:
            self.wait_calls = 0

        def wait(self, timeout=None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired(self.args, timeout)
            raise RuntimeError("cleanup wait failed")

    monkeypatch.setattr(runner_logs, "popen_tree", lambda *_args, **_kwargs: _Process())
    monkeypatch.setattr(runner_logs, "kill_process_tree", lambda *_args, **_kwargs: None)

    with pytest.raises(subprocess.TimeoutExpired):
        run_logged_process(
            ["fake"],
            timeout=0.01,
            cleanup_timeout_sec=0.02,
            log_path=tmp_path / "runner.log",
        )


def test_wait_api_error_still_triggers_bounded_cleanup(monkeypatch, tmp_path) -> None:
    class _Stdout:
        def read(self, _size: int) -> bytes:
            return b""

    class _Process:
        stdout = _Stdout()

        def wait(self, timeout=None) -> int:
            raise OverflowError("wait timeout cannot be represented")

    process = _Process()
    killed: list[object] = []
    monkeypatch.setattr(runner_logs, "popen_tree", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        runner_logs,
        "kill_process_tree",
        lambda target, **_kwargs: killed.append(target),
    )

    with pytest.raises(OverflowError):
        run_logged_process(
            ["fake"],
            timeout=1,
            cleanup_timeout_sec=0.02,
            log_path=tmp_path / "runner.log",
        )

    assert killed == [process]


def test_reader_thread_start_failure_cleans_started_process(monkeypatch, tmp_path) -> None:
    class _Stdout:
        def read(self, _size: int) -> bytes:
            return b""

    class _Process:
        stdout = _Stdout()

        def wait(self, timeout=None) -> int:
            return 0

    process = _Process()
    killed: list[object] = []
    monkeypatch.setattr(runner_logs, "popen_tree", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        runner_logs.threading.Thread,
        "start",
        lambda _self: (_ for _ in ()).throw(RuntimeError("thread start failed")),
    )
    monkeypatch.setattr(
        runner_logs,
        "kill_process_tree",
        lambda target, **_kwargs: killed.append(target),
    )

    with pytest.raises(RuntimeError, match="thread start failed"):
        run_logged_process(
            ["fake"],
            timeout=1,
            cleanup_timeout_sec=0.02,
            log_path=tmp_path / "runner.log",
        )

    assert killed == [process]
