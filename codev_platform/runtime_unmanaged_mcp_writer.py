"""部署停写时证明不存在固定 systemd unit 外的 MCP HTTP 守护进程。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.systemd_process_identity import (
    SystemdProcessIdentityError,
    cgroup_belongs_to_systemd_unit,
)
from codev_platform.mcp_systemd_unit_registry import mcp_systemd_unit_for_kind


_MAX_PROCESS_BYTES = 16 * 1024
_DEFAULT_MAX_PROCESSES = 131072
_MODULE_KINDS = {
    "codev_platform.chroma.daemon_entry": "chroma",
    "codev_platform.chroma.server": "chroma",
    "codev_platform.agent.memory_mcp": "agent_memory",
    "codev_platform.graph.mcp_server": "graph",
}
_HTTP_FLAG_REQUIRED = frozenset({"chroma", "graph"})
_PYTHON_OPTIONS_WITH_VALUE = frozenset({"-W", "-X", "--check-hash-based-pycs"})


class UnmanagedMCPWriterError(RuntimeError):
    """无法证明所有 MCP HTTP 写端点均属于固定 unit。"""


@dataclass(frozen=True, slots=True)
class _ProcessSnapshot:
    arguments: tuple[str, ...]
    cgroup: bytes


def assert_no_unmanaged_mcp_writers(
    *,
    proc_root: Path = Path("/proc"),
    max_processes: int = _DEFAULT_MAX_PROCESSES,
) -> None:
    """有界扫描精确官方启动形态，身份漂移或读取异常均失败关闭。"""
    root = Path(proc_root)
    if not root.is_absolute() or type(max_processes) is not int or max_processes <= 0:
        raise UnmanagedMCPWriterError("MCP 写端点进程证明配置无效")
    try:
        entries = list(root.iterdir())
    except OSError as error:
        raise UnmanagedMCPWriterError("无法扫描 MCP 写端点进程") from error
    count = 0
    for entry in entries:
        if not entry.name.isascii() or not entry.name.isdigit():
            continue
        count += 1
        if count > max_processes:
            raise UnmanagedMCPWriterError("MCP 写端点进程扫描不完整")
        _assert_process_managed(entry)


def _assert_process_managed(entry: Path) -> None:
    try:
        snapshot = _read_stable_snapshot(entry)
    except MemoryError:
        raise
    except UnmanagedMCPWriterError:
        raise
    except OSError as error:
        raise UnmanagedMCPWriterError("MCP 写端点进程扫描不完整") from error
    if snapshot is None:
        return
    kind = _mcp_writer_kind(snapshot.arguments)
    if kind is None:
        return
    unit = mcp_systemd_unit_for_kind(kind)
    try:
        managed = cgroup_belongs_to_systemd_unit(snapshot.cgroup, unit)
    except SystemdProcessIdentityError as error:
        raise UnmanagedMCPWriterError("MCP 写端点 cgroup 无法证明") from error
    if not managed:
        raise UnmanagedMCPWriterError("发现 systemd 外 MCP 写端点，拒绝部署停写")


def _read_stable_snapshot(entry: Path) -> _ProcessSnapshot | None:
    """双读 starttime 和 cmdline，避免 PID 复用或 exec 跨身份拼接假证据。"""
    try:
        identity_before = _process_identity(_read_bounded(entry / "stat", _MAX_PROCESS_BYTES))
        arguments_before = _arguments(_read_bounded(entry / "cmdline", _MAX_PROCESS_BYTES))
        cgroup = _read_bounded(entry / "cgroup", _MAX_PROCESS_BYTES)
        arguments_after = _arguments(_read_bounded(entry / "cmdline", _MAX_PROCESS_BYTES))
        identity_after = _process_identity(_read_bounded(entry / "stat", _MAX_PROCESS_BYTES))
    except FileNotFoundError:
        return None
    if identity_before[0] != entry.name or identity_after[0] != entry.name:
        raise UnmanagedMCPWriterError("MCP 写端点 proc 目录与进程身份不一致")
    if identity_before != identity_after or arguments_before != arguments_after:
        raise UnmanagedMCPWriterError("MCP 写端点进程扫描期间身份漂移")
    return _ProcessSnapshot(arguments_before, cgroup)


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb", buffering=0) as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise UnmanagedMCPWriterError("MCP 写端点进程原像超过上限")
    return raw


def _process_identity(raw: bytes) -> tuple[str, str]:
    try:
        text = raw.decode("ascii", "strict").strip()
    except UnicodeDecodeError:
        raise UnmanagedMCPWriterError("MCP 写端点进程身份格式无效") from None
    separator = text.find(" ")
    command_end = text.rfind(")")
    if (
        separator <= 0
        or text[separator + 1 : separator + 2] != "("
        or command_end <= separator
        or text[command_end + 1 : command_end + 2] != " "
        or command_end + 2 > len(text)
    ):
        raise UnmanagedMCPWriterError("MCP 写端点进程身份格式无效")
    pid = text[:separator]
    fields = text[command_end + 2 :].split()
    if not pid.isascii() or not pid.isdigit() or len(fields) <= 19:
        raise UnmanagedMCPWriterError("MCP 写端点进程身份格式无效")
    start_time = fields[19]
    if not start_time.isascii() or not start_time.isdigit():
        raise UnmanagedMCPWriterError("MCP 写端点进程身份格式无效")
    return pid, start_time


def _arguments(raw: bytes) -> tuple[str, ...]:
    if not raw:
        return ()
    if raw[-1:] != b"\0":
        raise UnmanagedMCPWriterError("MCP 写端点进程命令行格式无效")
    return tuple(part.decode("utf-8", "surrogateescape") for part in raw[:-1].split(b"\0"))


def _mcp_writer_kind(arguments: tuple[str, ...]) -> str | None:
    parsed = _python_module(arguments)
    if parsed is None:
        return None
    module, remainder = parsed
    kind = _MODULE_KINDS.get(module)
    if kind is None or (kind in _HTTP_FLAG_REQUIRED and "--http" not in remainder):
        return None
    return kind


def _python_module(arguments: tuple[str, ...]) -> tuple[str, tuple[str, ...]] | None:
    if not arguments or not _is_python_executable(arguments[0]):
        return None
    index = 1
    while index < len(arguments):
        argument = arguments[index]
        if argument == "-m":
            if index + 1 >= len(arguments):
                return None
            return arguments[index + 1], arguments[index + 2 :]
        if argument in {"-c", "--"} or not argument.startswith("-"):
            return None
        if argument in _PYTHON_OPTIONS_WITH_VALUE:
            index += 2
            continue
        index += 1
    return None


def _is_python_executable(value: str) -> bool:
    name = Path(value).name.lower()
    base = name[:-4] if name.endswith(".exe") else name
    if base in {"py", "python"}:
        return True
    if not base.startswith("python"):
        return False
    version = base.removeprefix("python").replace(".", "")
    return bool(version) and version.isdigit()


__all__ = ["UnmanagedMCPWriterError", "assert_no_unmanaged_mcp_writers"]
