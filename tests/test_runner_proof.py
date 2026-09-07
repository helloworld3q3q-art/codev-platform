"""runner 完整日志流式证明策略测试。"""
from __future__ import annotations

import pytest

from codev_platform.reindex.runner_proof import RunnerProofScanner


def test_marker_must_be_one_complete_standalone_line() -> None:
    valid = RunnerProofScanner("chroma")
    valid.feed_bytes(b"loading\nproof: chr")
    valid.feed_bytes(b"oma ok\r\nfinished\n")
    assert valid.finish() == ""

    prefixed = RunnerProofScanner("chroma")
    prefixed.feed_bytes(b"diagnostic: proof: chroma okay was not emitted\n")
    assert "marker missing" in prefixed.finish()

    duplicate = RunnerProofScanner("chroma")
    duplicate.feed_bytes(b"proof: chroma ok\n" + b"x" * 300_000 + b"\nproof: chroma ok\n")
    assert "not unique" in duplicate.finish()


@pytest.mark.parametrize(
    ("kind", "warning", "expected"),
    [
        ("codegraph", b"WARN: codegraph sync skipped", "sync skipped"),
        ("ingest", b"WARN: graph ingest failed", "graph ingest warning"),
        ("code_vec", b"WARN: code vector failed", "code vector warning"),
    ],
)
def test_forbidden_pattern_is_found_across_chunks(kind, warning, expected) -> None:
    scanner = RunnerProofScanner(kind)
    marker = f"proof: {kind} ok\n".encode()
    split = len(warning) // 2
    scanner.feed_bytes(marker + b"x" * 100_000 + warning[:split])
    scanner.feed_bytes(warning[split:] + b"\n")

    assert expected in scanner.finish()


def test_oversized_non_marker_line_does_not_fake_proof() -> None:
    scanner = RunnerProofScanner("chroma")
    scanner.feed_bytes(b"prefix " + b"x" * 1_000_000 + b" proof: chroma ok\n")

    assert "marker missing" in scanner.finish()


def test_unknown_kind_without_policy_fails_closed() -> None:
    scanner = RunnerProofScanner("future_kind")
    scanner.feed_bytes(b"proof: future_kind ok\n")

    assert "policy missing" in scanner.finish()
