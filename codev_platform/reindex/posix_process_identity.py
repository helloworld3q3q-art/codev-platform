"""Linux `/proc` 进程身份、进程组原生引用与有界快照叶子。"""

from __future__ import annotations

import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path

from codev_platform.reindex.attempt_process import build_process_identity

_BOOT_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z"
)
_POSIX_REF_RE = re.compile(
    r"posix-pgrp:v1:(?P<boot>[0-9a-f-]{36}):(?P<pgid>[1-9][0-9]*):(?P<sid>[1-9][0-9]*)\Z"
)
_MAX_PROC_STAT_BYTES = 16 * 1024
_DEFAULT_MAX_PROCESSES = 131072


@dataclass(frozen=True, slots=True)
class LinuxProcessInfo:
    """从 `/proc/<pid>/stat` 提取的稳定进程字段。"""

    pid: int
    ppid: int
    pgid: int
    session_id: int
    state: str
    start_ticks: int

    @property
    def live(self) -> bool:
        return self.state != "Z"


@dataclass(frozen=True, slots=True)
class LinuxProcessScan:
    processes: tuple[LinuxProcessInfo, ...]
    complete: bool


def _positive_int(value: str, field_name: str, *, allow_zero: bool = False) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError(f"{field_name} 不是十进制整数")
    number = int(value)
    if number < 0 or (not allow_zero and number == 0):
        raise ValueError(f"{field_name} 超出允许范围")
    return number


def parse_linux_stat(raw: bytes, *, expected_pid: int) -> LinuxProcessInfo:
    """按最右侧 `) ` 分隔 comm，避免进程名中的括号混淆字段。"""
    if type(raw) is not bytes or not 0 < len(raw) <= _MAX_PROC_STAT_BYTES:
        raise ValueError("proc stat 大小无效")
    delimiter = raw.rfind(b") ")
    opening = raw.find(b" (")
    if opening <= 0 or delimiter <= opening:
        raise ValueError("proc stat comm 边界无效")
    try:
        pid_text = raw[:opening].decode("ascii")
        fields = raw[delimiter + 2 :].split()
    except UnicodeError:
        raise ValueError("proc stat 数字字段不是 ASCII") from None
    pid = _positive_int(pid_text, "pid")
    if pid != expected_pid or len(fields) < 20:
        raise ValueError("proc stat PID 或字段数无效")
    try:
        state = fields[0].decode("ascii")
        ppid = _positive_int(fields[1].decode("ascii"), "ppid", allow_zero=True)
        pgid = _positive_int(fields[2].decode("ascii"), "pgid", allow_zero=True)
        session_id = _positive_int(fields[3].decode("ascii"), "session", allow_zero=True)
        start_ticks = _positive_int(fields[19].decode("ascii"), "starttime")
    except UnicodeError:
        raise ValueError("proc stat 数字字段不是 ASCII") from None
    if len(state) != 1:
        raise ValueError("proc stat 状态字段无效")
    return LinuxProcessInfo(pid, ppid, pgid, session_id, state, start_ticks)


def linux_birth_marker(boot_id: str, start_ticks: int) -> str:
    if _BOOT_ID_RE.fullmatch(boot_id) is None or start_ticks <= 0:
        raise ValueError("Linux 出生标记输入无效")
    return f"linux:v1:{boot_id}:{start_ticks}"


def encode_posix_native_ref(boot_id: str, pgid: int, session_id: int) -> str:
    if _BOOT_ID_RE.fullmatch(boot_id) is None or pgid <= 0 or session_id <= 0:
        raise ValueError("POSIX 原生引用输入无效")
    return f"posix-pgrp:v1:{boot_id}:{pgid}:{session_id}"


def parse_posix_native_ref(native_ref: str) -> tuple[str, int, int]:
    match = _POSIX_REF_RE.fullmatch(native_ref)
    if match is None or _BOOT_ID_RE.fullmatch(match["boot"]) is None:
        raise ValueError("POSIX 原生引用无效")
    return match["boot"], int(match["pgid"]), int(match["sid"])


class LinuxProcessTable:
    """有界读取 Linux boot_id 与 proc stat，并提供 PID 复用安全信号。"""

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id"),
        max_processes: int = _DEFAULT_MAX_PROCESSES,
    ) -> None:
        if max_processes <= 0:
            raise ValueError("max_processes 必须为正整数")
        self.proc_root = Path(proc_root)
        self.boot_id_path = Path(boot_id_path)
        self.max_processes = max_processes
        self.boot_id = self._read_boot_id()

    def _read_boot_id(self) -> str:
        raw = self.boot_id_path.read_bytes()
        if len(raw) > 128:
            raise RuntimeError("Linux boot_id 超过长度上限")
        try:
            value = raw.decode("ascii").strip()
        except UnicodeError:
            raise RuntimeError("Linux boot_id 不是 ASCII") from None
        if _BOOT_ID_RE.fullmatch(value) is None:
            raise RuntimeError("Linux boot_id 格式无效")
        return value

    def read(self, pid: int) -> LinuxProcessInfo | None:
        if type(pid) is not int or pid <= 0:
            raise ValueError("pid 必须为正整数")
        path = self.proc_root / str(pid) / "stat"
        try:
            with path.open("rb") as stream:
                raw = stream.read(_MAX_PROC_STAT_BYTES + 1)
        except (FileNotFoundError, ProcessLookupError):
            return None
        return parse_linux_stat(raw, expected_pid=pid)

    def scan(self) -> LinuxProcessScan:
        processes: list[LinuxProcessInfo] = []
        complete = True
        seen = 0
        try:
            entries = self.proc_root.iterdir()
            for entry in entries:
                if not entry.name.isascii() or not entry.name.isdigit():
                    continue
                seen += 1
                if seen > self.max_processes:
                    complete = False
                    break
                try:
                    info = self.read(int(entry.name))
                except (OSError, ValueError):
                    complete = False
                    continue
                if info is not None:
                    processes.append(info)
        except OSError:
            return LinuxProcessScan((), False)
        return LinuxProcessScan(tuple(processes), complete)

    def build_identity(self, info: LinuxProcessInfo, native_ref: str) -> str:
        return self.build_identity_from_values(
            pid=info.pid,
            native_ref=native_ref,
            boot_id=self.boot_id,
            start_ticks=info.start_ticks,
        )

    @staticmethod
    def build_identity_from_values(
        *,
        pid: int,
        native_ref: str,
        boot_id: str,
        start_ticks: int,
    ) -> str:
        return build_process_identity(
            pid=pid,
            native_ref=native_ref,
            birth_marker=linux_birth_marker(boot_id, start_ticks),
        )

    def same_process(self, info: LinuxProcessInfo) -> bool:
        current = self.read(info.pid)
        return current is not None and current.start_ticks == info.start_ticks

    def signal_same(self, info: LinuxProcessInfo, sig: int) -> bool:
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            return False
        try:
            descriptor = os.pidfd_open(info.pid)
        except (OSError, ProcessLookupError):
            return False
        try:
            if not self.same_process(info):
                return False
            signal.pidfd_send_signal(descriptor, sig)
            return True
        except (OSError, ProcessLookupError):
            return False
        finally:
            os.close(descriptor)


__all__ = [
    "LinuxProcessInfo",
    "LinuxProcessScan",
    "LinuxProcessTable",
    "encode_posix_native_ref",
    "linux_birth_marker",
    "parse_linux_stat",
    "parse_posix_native_ref",
]
