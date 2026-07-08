from __future__ import annotations

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


def test_redacts_common_secret_shapes() -> None:
    output = "\n".join(
        [
            "Authorization: Bearer secret-token-123",
            "Authorization: token github-header-secret",
            "api_key=sk-secretsecretsecret",
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
    assert "sk-secretsecretsecret" not in redacted
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
    assert "oversized line truncated" in written
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
