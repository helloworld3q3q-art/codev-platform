"""为重建 runner 子进程提供有界且脱敏的输出日志。"""

from __future__ import annotations

import codecs
import math
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from codev_platform.core.process_tree import kill_process_tree, popen_tree
from codev_platform.core.runtime_artifact_io import write_runtime_artifact_bytes

DEFAULT_RUNNER_LOG_MAX_BYTES = 512 * 1024
MAX_RUNNER_TIMEOUT_SEC = 24 * 60 * 60
TRUNCATION_MARKER = "\n...<runner 日志已截断：仅保留脱敏后的头尾>...\n"

_READ_CHUNK_BYTES = 8192
_MAX_PENDING_LINE_CHARS = 64 * 1024
_OVERSIZED_LINE_MARKER = "\n...<runner 超长单行已在脱敏前截断>...\n"
_READER_JOIN_SEC = 1.0
_DEFAULT_CLEANUP_TIMEOUT_SEC = 2.0
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
    """从 runner 标准输出与错误输出中移除常见凭据形态。"""
    if not text:
        return ""
    text = _URL_CREDENTIAL_RE.sub(r"\1<redacted>@", text)
    text = _AUTH_HEADER_RE.sub(r"\1<redacted>", text)
    text = _DOUBLE_QUOTED_KEY_VALUE_RE.sub(r'\g<prefix>"<redacted>"', text)
    text = _SINGLE_QUOTED_KEY_VALUE_RE.sub(r"\g<prefix>'<redacted>'", text)
    text = _UNQUOTED_KEY_VALUE_RE.sub(r"\g<prefix><redacted>", text)
    return _OPENAI_KEY_RE.sub("<redacted>", text)


class RunnerLogBuffer:
    """在保留首尾信息的同时，对脱敏后的 runner 输出施加容量上限。"""

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
        if not write_runtime_artifact_bytes(path, self.render_bytes()):
            raise OSError("runner 日志无法安全提交")

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


def _bounded_timeout(value: float, field: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} 必须是有限非负数")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError(f"{field} 必须是有限非负数")
    return timeout


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _positive_timeout(value: float, field: str) -> float:
    timeout = _bounded_timeout(value, field)
    if timeout <= 0 or timeout > MAX_RUNNER_TIMEOUT_SEC:
        raise ValueError(f"{field} 必须在 0 到 24 小时之间")
    return timeout


def _finish_reader(
    _proc: subprocess.Popen,
    reader: threading.Thread,
    stream: _RedactingStream,
    timeout_sec: float = _READER_JOIN_SEC,
) -> bool:
    """只做有界 join；不得从控制线程关闭 reader 正在读取的流。"""
    reader.join(timeout=_bounded_timeout(timeout_sec, "reader timeout"))
    if not reader.is_alive():
        stream.finish()
        return True
    return False


def _wait_after_kill(proc: subprocess.Popen, deadline: float) -> None:
    try:
        proc.wait(timeout=_remaining(deadline))
    except Exception:  # noqa: BLE001 - 清理异常不得覆盖原始 timeout
        return


def _finish_reader_quietly(
    proc: subprocess.Popen,
    reader: threading.Thread,
    stream: _RedactingStream,
    timeout_sec: float,
) -> None:
    try:
        _finish_reader(proc, reader, stream, timeout_sec)
    except Exception:  # noqa: BLE001 - 清理异常不得覆盖原始 timeout
        return


def _write_cleanup_log_quietly(buffer: RunnerLogBuffer, log_path: Path) -> None:
    try:
        buffer.write_to(log_path)
    except Exception:  # noqa: BLE001 - 日志失败不得覆盖原始 timeout
        return


def _cleanup_process(
    proc: subprocess.Popen,
    reader: threading.Thread,
    stream: _RedactingStream,
    buffer: RunnerLogBuffer,
    log_path: Path,
    deadline: float,
) -> None:
    try:
        kill_process_tree(proc, timeout=_remaining(deadline))
    except Exception:  # noqa: BLE001 - 清理异常不得覆盖原始 timeout
        pass
    _wait_after_kill(proc, deadline)
    _finish_reader_quietly(proc, reader, stream, _remaining(deadline))
    _write_cleanup_log_quietly(buffer, log_path)


def _notify_observer(
    observer: Callable[[bytes], None] | None,
    chunk: bytes,
    errors: list[Exception],
) -> None:
    if observer is None or errors:
        return
    try:
        observer(chunk)
    except Exception as exc:  # noqa: BLE001 - 继续排空管道，收尾后失败关闭
        if not errors:
            errors.append(exc)


def run_logged_process(
    cmd: Sequence[str],
    *,
    timeout: float,
    log_path: Path,
    max_bytes: int = DEFAULT_RUNNER_LOG_MAX_BYTES,
    env: Mapping[str, str] | None = None,
    output_observer: Callable[[bytes], None] | None = None,
    cleanup_timeout_sec: float = _DEFAULT_CLEANUP_TIMEOUT_SEC,
) -> int:
    """运行子进程，并持续排空输出到有界脱敏日志。"""
    run_timeout = _positive_timeout(timeout, "timeout")
    cleanup_timeout = _bounded_timeout(cleanup_timeout_sec, "cleanup_timeout_sec")
    buffer = RunnerLogBuffer(max_bytes=max_bytes)
    stream = _RedactingStream(buffer)
    observer_errors: list[Exception] = []
    reader_errors: list[Exception] = []
    proc = popen_tree(  # noqa: S603 - cmd 仅由可信本地 CLI 参数构造
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=dict(env) if env is not None else None,
    )

    def _drain_stdout() -> None:
        try:
            if proc.stdout is None:
                return
            while True:
                chunk = proc.stdout.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                _notify_observer(output_observer, chunk, observer_errors)
                stream.feed_bytes(chunk)
        except Exception as exc:  # noqa: BLE001 - 任意读取/处理异常都必须阻断成功证明
            reader_errors.append(exc)
        finally:
            stream.finish()

    reader = threading.Thread(target=_drain_stdout, name="reindex-runner-log", daemon=True)
    try:
        reader.start()
    except BaseException:
        cleanup_deadline = time.monotonic() + cleanup_timeout
        _cleanup_process(
            proc,
            reader,
            stream,
            buffer,
            log_path,
            cleanup_deadline,
        )
        raise
    try:
        rc = proc.wait(timeout=run_timeout)
    except subprocess.TimeoutExpired:
        cleanup_deadline = time.monotonic() + cleanup_timeout
        _cleanup_process(
            proc,
            reader,
            stream,
            buffer,
            log_path,
            cleanup_deadline,
        )
        raise
    except BaseException:
        cleanup_deadline = time.monotonic() + cleanup_timeout
        _cleanup_process(
            proc,
            reader,
            stream,
            buffer,
            log_path,
            cleanup_deadline,
        )
        raise

    cleanup_deadline = time.monotonic() + cleanup_timeout
    first_join = min(_READER_JOIN_SEC, _remaining(cleanup_deadline))
    try:
        reader_finished = _finish_reader(proc, reader, stream, first_join)
    except BaseException:
        _cleanup_process(
            proc,
            reader,
            stream,
            buffer,
            log_path,
            cleanup_deadline,
        )
        raise
    if not reader_finished:
        _cleanup_process(
            proc,
            reader,
            stream,
            buffer,
            log_path,
            cleanup_deadline,
        )
        raise RuntimeError("runner 输出读取线程未结束")
    buffer.write_to(log_path)
    if reader_errors:
        raise RuntimeError("runner 输出读取失败")
    if observer_errors:
        raise RuntimeError("runner 完整输出观察器失败")
    return rc
