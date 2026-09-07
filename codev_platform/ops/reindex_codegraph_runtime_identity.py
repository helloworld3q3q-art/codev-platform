"""CodeGraph systemd 实例身份与有界稳定窗口证明。"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite


_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
_SYSTEMCTL_TIMEOUT_SEC = 5.0
_DEFAULT_SAMPLE_COUNT = 11
_DEFAULT_INTERVAL_SEC = 0.5
_IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")


class CodegraphRuntimeIdentityError(RuntimeError):
    """CodeGraph 运行实例无法唯一绑定或稳定复证。"""


@dataclass(frozen=True, slots=True)
class CodegraphRuntimeIdentity:
    """一次 systemd invocation 及其单调重启计数快照。"""

    invocation_id: str
    restart_count: int

    def __post_init__(self) -> None:
        if (
            type(self.invocation_id) is not str
            or _IDENTIFIER.fullmatch(self.invocation_id) is None
            or type(self.restart_count) is not int
            or self.restart_count < 0
        ):
            raise CodegraphRuntimeIdentityError("CodeGraph 运行实例身份无效")


CommandRunner = Callable[..., object]
IdentityReader = Callable[[], CodegraphRuntimeIdentity]
Sleeper = Callable[[float], None]


def read_codegraph_runtime_identity(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
) -> CodegraphRuntimeIdentity:
    """严格读取活动 unit 的 InvocationID 与 NRestarts，缺字段即失败关闭。"""
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise CodegraphRuntimeIdentityError("当前平台无法证明 CodeGraph 运行实例")
    run = _default_command_runner if command_runner is None else command_runner
    if not callable(run):
        raise CodegraphRuntimeIdentityError("CodeGraph 实例身份适配器不可用")
    fields = ("ActiveState", "SubState", "Restart", "InvocationID", "NRestarts")
    try:
        result = run(
            (
                "systemctl",
                "show",
                _CODEGRAPH_UNIT,
                *(f"--property={field}" for field in fields),
            ),
            timeout_sec=_SYSTEMCTL_TIMEOUT_SEC,
        )
        values = _parse_properties(result, fields)
        if (
            values["ActiveState"] != "active"
            or values["SubState"] != "running"
            or values["Restart"] != "always"
        ):
            raise CodegraphRuntimeIdentityError("CodeGraph 运行实例状态不符合基线")
        restart_count = _parse_restart_count(values["NRestarts"])
        return CodegraphRuntimeIdentity(values["InvocationID"], restart_count)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except CodegraphRuntimeIdentityError:
        raise
    except Exception as error:
        raise CodegraphRuntimeIdentityError("CodeGraph 运行实例身份无法读取") from error


def prove_codegraph_runtime_stable(
    expected: CodegraphRuntimeIdentity,
    *,
    identity_reader: IdentityReader | None = None,
    sleeper: Sleeper | None = None,
    sample_count: int = _DEFAULT_SAMPLE_COUNT,
    interval_sec: float = _DEFAULT_INTERVAL_SEC,
) -> None:
    """连续复证同一 InvocationID 与 NRestarts，拒绝重启漂移和短暂健康。"""
    if type(expected) is not CodegraphRuntimeIdentity:
        raise CodegraphRuntimeIdentityError("CodeGraph 期望运行实例无效")
    read_identity = read_codegraph_runtime_identity if identity_reader is None else identity_reader
    sleep = time.sleep if sleeper is None else sleeper
    if not callable(read_identity) or not callable(sleep):
        raise CodegraphRuntimeIdentityError("CodeGraph 稳定窗口适配器不可用")
    if type(sample_count) is not int or not 2 <= sample_count <= 20:
        raise CodegraphRuntimeIdentityError("CodeGraph 稳定窗口采样数无效")
    if (
        type(interval_sec) not in (int, float)
        or not isfinite(float(interval_sec))
        or not 0 < float(interval_sec) <= 5.0
    ):
        raise CodegraphRuntimeIdentityError("CodeGraph 稳定窗口间隔无效")
    try:
        for index in range(sample_count):
            if read_identity() != expected:
                raise CodegraphRuntimeIdentityError("CodeGraph 实例在稳定窗口内发生漂移")
            if index + 1 < sample_count:
                sleep(float(interval_sec))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except CodegraphRuntimeIdentityError:
        raise
    except Exception as error:
        raise CodegraphRuntimeIdentityError("CodeGraph 稳定窗口无法证明") from error


def _parse_properties(result: object, fields: tuple[str, ...]) -> dict[str, str]:
    if getattr(result, "returncode", None) != 0 or type(getattr(result, "stdout", None)) is not str:
        raise CodegraphRuntimeIdentityError("CodeGraph systemd 状态不可用")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in fields or key in values or "\x00" in value:
            raise CodegraphRuntimeIdentityError("CodeGraph systemd 状态格式无效")
        values[key] = value
    if set(values) != set(fields):
        raise CodegraphRuntimeIdentityError("CodeGraph systemd 状态格式无效")
    return values


def _parse_restart_count(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        raise CodegraphRuntimeIdentityError("CodeGraph 重启计数无效")
    return int(value)


def _default_command_runner(command: tuple[str, ...], *, timeout_sec: float) -> object:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )


__all__ = [
    "CodegraphRuntimeIdentity",
    "CodegraphRuntimeIdentityError",
    "prove_codegraph_runtime_stable",
    "read_codegraph_runtime_identity",
]
