"""对 runner 完整输出流做内存有界、失败关闭的成功证明扫描。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_READ_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class _ProofPolicy:
    marker: bytes
    forbidden: tuple[tuple[bytes, str], ...] = ()


_ENSURE_LINK = (
    b"WARN: ensure codegraph link failed",
    "ensure-link failed",
)
_POLICIES = {
    "chroma": _ProofPolicy(b"proof: chroma ok"),
    "codegraph": _ProofPolicy(
        b"proof: codegraph ok",
        (
            _ENSURE_LINK,
            (b"SKIP: 'codegraph' CLI not found", "codegraph CLI missing"),
            (b"WARN: codegraph sync skipped", "sync skipped"),
            (b"FAIL: codegraph sync", "sync failed"),
        ),
    ),
    "ingest": _ProofPolicy(
        b"proof: ingest ok",
        (_ENSURE_LINK, (b"WARN: graph ingest failed", "graph ingest warning")),
    ),
    "code_vec": _ProofPolicy(
        b"proof: code_vec ok",
        (
            _ENSURE_LINK,
            (b"WARN: code vector failed", "code vector warning"),
            (b"code vector      -- skipped", "code vector skipped"),
        ),
    ),
}


def proof_policy_kinds() -> tuple[str, ...]:
    """返回成功证明策略的确定顺序，供生产契约完整性门禁使用。"""
    return tuple(_POLICIES)


class RunnerProofScanner:
    """流式策略：marker 必须是唯一完整行，禁用模式跨块也能识别。"""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._policy = _POLICIES.get(kind)
        self._marker_count = 0
        self._matched_forbidden: set[bytes] = set()
        self._pattern_tail = b""
        self._line = bytearray()
        self._line_overflow = False
        self._finished = False
        self._result = ""
        self.bytes_seen = 0

    def feed_bytes(self, data: bytes) -> None:
        if self._finished or not data:
            return
        if type(data) is not bytes:
            raise TypeError("runner proof 输入必须是 bytes")
        self.bytes_seen += len(data)
        if self._policy is None:
            return
        self._scan_forbidden(data)
        self._scan_lines(data)

    def _scan_forbidden(self, data: bytes) -> None:
        patterns = tuple(item[0] for item in self._policy.forbidden)
        if not patterns:
            return
        window = self._pattern_tail + data
        self._matched_forbidden.update(pattern for pattern in patterns if pattern in window)
        keep = max(len(pattern) for pattern in patterns) - 1
        self._pattern_tail = window[-keep:] if keep > 0 else b""

    def _scan_lines(self, data: bytes) -> None:
        cursor = 0
        while cursor < len(data):
            newline = data.find(b"\n", cursor)
            if newline < 0:
                self._extend_line(data[cursor:])
                return
            self._extend_line(data[cursor:newline])
            self._finish_line()
            cursor = newline + 1

    def _extend_line(self, segment: bytes) -> None:
        if self._line_overflow or not segment:
            return
        limit = len(self._policy.marker) + 1  # 额外容纳 CRLF 的 \r
        if len(self._line) + len(segment) > limit:
            self._line.clear()
            self._line_overflow = True
            return
        self._line.extend(segment)

    def _finish_line(self) -> None:
        if not self._line_overflow and bytes(self._line).removesuffix(b"\r") == self._policy.marker:
            self._marker_count += 1
        self._line.clear()
        self._line_overflow = False

    def _feed_fallback_file(self, path: Path | None) -> None:
        if self.bytes_seen or path is None or not path.is_file():
            return
        with path.open("rb") as stream:
            while chunk := stream.read(_READ_CHUNK_BYTES):
                self.feed_bytes(chunk)

    def finish(self, fallback_path: Path | None = None) -> str:
        if self._finished:
            return self._result
        self._feed_fallback_file(fallback_path)
        if self._policy is None:
            self._result = f"{self._kind} proof failed: proof policy missing"
            self._finished = True
            return self._result
        if self._line or self._line_overflow:
            self._finish_line()
        self._result = self._proof_failure()
        self._finished = True
        return self._result

    def _proof_failure(self) -> str:
        for pattern, reason in self._policy.forbidden:
            if pattern in self._matched_forbidden:
                return f"{self._kind} proof failed: {reason}"
        if self._marker_count == 0:
            return f"{self._kind} proof failed: proof marker missing"
        if self._marker_count != 1:
            return f"{self._kind} proof failed: proof marker not unique"
        return ""


__all__ = ["RunnerProofScanner", "proof_policy_kinds"]
