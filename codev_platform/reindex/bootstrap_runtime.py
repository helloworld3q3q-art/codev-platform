"""两阶段 bootstrap 后端共享的轻量输入与启动门原语。"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path


def validate_attempt_id(value: object) -> str:
    """验证 attempt_id，避免创建进程后才由 handle 拒绝。"""
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise ValueError("attempt_id 无效")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ValueError("attempt_id 不是有效 UTF-8") from None
    if len(encoded) > 4096:
        raise ValueError("attempt_id 超过长度上限")
    return value


def validate_command(argv: Sequence[str]) -> list[str]:
    """验证并复制只能由绝对路径启动的有界 argv。"""
    if isinstance(argv, (str, bytes)) or not argv or len(argv) > 4096:
        raise ValueError("argv 无效或过长")
    command = list(argv)
    if any(type(item) is not str or not item or "\x00" in item for item in command):
        raise ValueError("argv 元素无效")
    if not Path(command[0]).is_absolute():
        raise ValueError("argv[0] 必须是绝对路径")
    return command


def positive_finite(value: object, field_name: str) -> float:
    """验证有限正浮点配置。"""
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} 必须是有限正数")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError(f"{field_name} 必须是有限正数")
    return resolved


def clock_now(clock: Callable[[], float], field_name: str) -> float:
    """读取并验证非负有限时钟值。"""
    resolved = float(clock())
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError(f"{field_name} 时钟无效")
    return resolved


def close_gate(descriptor: int | None) -> None:
    """幂等关闭父侧启动门描述符。"""
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def release_gate(descriptor: int) -> bool:
    """写入唯一放行令牌并关闭描述符。"""
    written = False
    try:
        written = os.write(descriptor, b"1") == 1
    except OSError:
        pass
    try:
        os.close(descriptor)
    except OSError:
        return False
    return written


def wait_for_false(
    probe: Callable[[], bool | None],
    timeout_sec: float,
    *,
    interval_sec: float = 0.01,
) -> bool:
    """用系统单调时钟有界等待清理探针变为 false。"""
    expires_at = time.monotonic() + max(0.0, timeout_sec)
    while True:
        if probe() is False:
            return True
        remaining = expires_at - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(interval_sec, remaining))


__all__ = [
    "clock_now",
    "close_gate",
    "positive_finite",
    "release_gate",
    "validate_attempt_id",
    "validate_command",
    "wait_for_false",
]
