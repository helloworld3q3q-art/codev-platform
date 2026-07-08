"""Bounded, redacted logging for reindex runner subprocess output."""
from __future__ import annotations

import codecs
import os
import re
import signal
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path

DEFAULT_RUNNER_LOG_MAX_BYTES = 512 * 1024
TRUNCATION_MARKER = "\n...<runner log truncated: kept head/tail, redacted>...\n"

_READ_CHUNK_BYTES = 8192
_MAX_PENDING_LINE_CHARS = 64 * 1024
_OVERSIZED_LINE_MARKER = "\n...<runner log oversized line truncated before redaction>...\n"
_READER_JOIN_SEC = 1.0
_READER_CLOSE_JOIN_SEC = 0.5
_SENSITIVE_KEY_PATTERN = (
    r"[A-Za-z0-9_.-]{0,96}(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|"
    r"password|passwd|pwd|dsn)[A-Za-z0-9_.-]{0,96}"
)
_AUTH_HEADER_RE = re.compile(
    r"(?im)\b(Authorization\s*:\s*(?:(?:Bearer|Basic|Token|ApiKey)\s+)?)[^\s\r\n]+"
)
_DOUBLE_QUOTED_KEY_VALUE_RE = re.compile(
    rf"(?i)(?P<prefix>['\"]?{_SENSITIVE_KEY_PATTERN}['\"]?\s*[:=]\s*)"
    r'"(?:\\.|[^"\\])*"'
)
_SINGLE_QUOTED_KEY_VALUE_RE = re.compile(
    rf"(?i)(?P<prefix>['\"]?{_SENSITIVE_KEY_PATTERN}['\"]?\s*[:=]\s*)"
    r"'(?:\\.|[^'\\])*'"
)
_UNQUOTED_KEY_VALUE_RE = re.compile(
    rf"(?i)\b(?P<prefix>{_SENSITIVE_KEY_PATTERN}\s*[:=]\s*)[^'\"\s,;&]+"
)
_URL_CREDENTIAL_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def redact_runner_output(text: str) -> str:
    """Remove common credential shapes from runner stdout/stderr text."""
    if not text:
        return ""
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    text = _AUTH_HEADER_RE.sub(r"\1<redacted>", text)
    text = _DOUBLE_QUOTED_KEY_VALUE_RE.sub(r'\g<prefix>"<redacted>"', text)
    text = _SINGLE_QUOTED_KEY_VALUE_RE.sub(r"\g<prefix>'<redacted>'", text)
    text = _UNQUOTED_KEY_VALUE_RE.sub(r"\g<prefix><redacted>", text)
    return _OPENAI_KEY_RE.sub("<redacted>", text)


class RunnerLogBuffer:
    """Keep redacted runner output bounded while preserving both head and tail."""

    def __init__(self, max_bytes: int = DEFAULT_RUNNER_LOG_MAX_BYTES) -> None:
        self._max_bytes = max(0, int(max_bytes))
        self._full = bytearray()
        self._head = bytearray()
        self._tail = bytearray()
        self._truncated = False
        self._lock = threading.RLock()

    def feed_text(self, text: str) -> None:
        if not text or self._max_bytes <= 0:
            return
        data = redact_runner_output(text).encode("utf-8", errors="replace")
        with self._lock:
            self._feed_bytes(data)

    def render_bytes(self) -> bytes:
        with self._lock:
            if self._max_bytes <= 0:
                return b""
            if not self._truncated:
                return bytes(self._full)
            marker = self._marker_bytes()
            if len(marker) >= self._max_bytes:
                return marker[: self._max_bytes]
            head = _clip_utf8_prefix(bytes(self._head), self._head_limit())
            tail = _clip_utf8_suffix(bytes(self._tail), self._tail_limit())
            return head + marker + tail

    def write_to(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.render_bytes())

    def _feed_bytes(self, data: bytes) -> None:
        if not data:
            return
        if not self._truncated:
            self._full.extend(data)
            if len(self._full) <= self._max_bytes:
                return
            self._promote_to_truncated()
            return

        tail_limit = self._tail_limit()
        if tail_limit <= 0:
            return
        self._tail.extend(data)
        overflow = len(self._tail) - tail_limit
        if overflow > 0:
            del self._tail[:overflow]

    def _promote_to_truncated(self) -> None:
        head_limit = self._head_limit()
        tail_limit = self._tail_limit()
        snapshot = bytes(self._full)
        self._head = bytearray(snapshot[:head_limit])
        self._tail = bytearray(snapshot[-tail_limit:] if tail_limit > 0 else b"")
        self._full.clear()
        self._truncated = True

    def _marker_bytes(self) -> bytes:
        return TRUNCATION_MARKER.encode("utf-8")

    def _body_budget(self) -> int:
        return max(0, self._max_bytes - len(self._marker_bytes()))

    def _head_limit(self) -> int:
        return self._body_budget() // 2

    def _tail_limit(self) -> int:
        return self._body_budget() - self._head_limit()


def _clip_utf8_prefix(data: bytes, limit: int) -> bytes:
    if limit <= 0:
        return b""
    return data[:limit].decode("utf-8", errors="ignore").encode("utf-8")


def _clip_utf8_suffix(data: bytes, limit: int) -> bytes:
    if limit <= 0:
        return b""
    return data[-limit:].decode("utf-8", errors="ignore").encode("utf-8")


class _RedactingStream:
    def __init__(self, buffer: RunnerLogBuffer) -> None:
        self._buffer = buffer
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending = ""
        self._discarding_oversized_line = False
        self._finished = False
        self._lock = threading.RLock()

    def feed_bytes(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._feed_text(self._decoder.decode(data))

    def finish(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._feed_text(self._decoder.decode(b"", final=True))
            if self._pending and not self._discarding_oversized_line:
                self._buffer.feed_text(self._pending)
            self._pending = ""
            self._discarding_oversized_line = False
            self._finished = True

    def _feed_text(self, text: str) -> None:
        cursor = 0
        while cursor < len(text):
            if self._discarding_oversized_line:
                newline = text.find("\n", cursor)
                if newline < 0:
                    return
                self._discarding_oversized_line = False
                cursor = newline + 1
                continue

            newline = text.find("\n", cursor)
            if newline < 0:
                self._pending += text[cursor:]
                if len(self._pending) > _MAX_PENDING_LINE_CHARS:
                    self._flush_oversized_pending()
                return

            self._pending += text[cursor : newline + 1]
            self._flush_pending()
            cursor = newline + 1

    def _flush_pending(self) -> None:
        if self._pending:
            self._buffer.feed_text(self._pending)
            self._pending = ""

    def _flush_oversized_pending(self) -> None:
        self._pending = ""
        self._buffer.feed_text(_OVERSIZED_LINE_MARKER)
        self._discarding_oversized_line = True


def _popen_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}


def _kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(  # noqa: S603,S607 - fixed Windows tool, PID is from Popen.
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
        if proc.poll() is None:
            proc.kill()
        return

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        proc.kill()


def _close_stdout(proc: subprocess.Popen) -> None:
    if proc.stdout is None:
        return
    try:
        proc.stdout.close()
    except OSError:
        return


def _finish_reader(proc: subprocess.Popen, reader: threading.Thread, stream: _RedactingStream) -> None:
    reader.join(timeout=_READER_JOIN_SEC)
    if reader.is_alive():
        _close_stdout(proc)
        reader.join(timeout=_READER_CLOSE_JOIN_SEC)
    if not reader.is_alive():
        stream.finish()


def run_logged_process(
    cmd: Sequence[str],
    *,
    timeout: float | None,
    log_path: Path,
    max_bytes: int = DEFAULT_RUNNER_LOG_MAX_BYTES,
) -> int:
    """Run a subprocess while draining stdout/stderr into a bounded redacted log."""
    buffer = RunnerLogBuffer(max_bytes=max_bytes)
    stream = _RedactingStream(buffer)
    proc = subprocess.Popen(  # noqa: S603 - cmd is constructed from trusted local CLI parts.
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        **_popen_kwargs(),
    )

    def _drain_stdout() -> None:
        try:
            if proc.stdout is None:
                return
            while True:
                chunk = proc.stdout.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                stream.feed_bytes(chunk)
        except (OSError, ValueError):
            pass
        finally:
            stream.finish()

    reader = threading.Thread(target=_drain_stdout, name="reindex-runner-log", daemon=True)
    reader.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        proc.wait()
        _finish_reader(proc, reader, stream)
        buffer.write_to(log_path)
        raise

    _finish_reader(proc, reader, stream)
    buffer.write_to(log_path)
    return rc
