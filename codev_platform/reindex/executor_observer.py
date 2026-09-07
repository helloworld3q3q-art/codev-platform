"""在隔离容器内观察真实 executor，并发布耐久完成凭据。"""
from __future__ import annotations

import argparse
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .attempt_completion import (
    AttemptCompletionReceipt,
    make_attempt_completion_receipt,
    write_attempt_completion_receipt,
)
from .attempts import read_attempt_result, read_attempt_spec

_MAX_EXECUTOR_ARGUMENTS = 128
_MAX_EXECUTOR_ARGUMENT_BYTES = 8 * 1024
_MAX_EXECUTOR_COMMAND_BYTES = 24 * 1024


def _argument_size(value: object) -> int:
    if type(value) is not str or not value:
        raise ValueError("executor 参数只接受非空字符串")
    if "\x00" in value:
        raise ValueError("executor 参数不能包含 NUL")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        raise ValueError("executor 参数必须是有效 UTF-8") from None
    if size > _MAX_EXECUTOR_ARGUMENT_BYTES:
        raise ValueError("executor 单个参数超过大小上限")
    return size


def _absolute_command(argv: Sequence[str]) -> tuple[str, ...]:
    if isinstance(argv, (str, bytes)):
        raise ValueError("executor argv 不能为空")
    command: list[str] = []
    total_bytes = 0
    try:
        iterator = iter(argv)
    except TypeError:
        raise ValueError("executor argv 必须是参数序列") from None
    for item in iterator:
        if len(command) >= _MAX_EXECUTOR_ARGUMENTS:
            raise ValueError("executor 参数数量超过上限")
        total_bytes += _argument_size(item) + 1
        if total_bytes > _MAX_EXECUTOR_COMMAND_BYTES:
            raise ValueError("executor 命令总字节超过上限")
        command.append(item)
    if not command:
        raise ValueError("executor argv 不能为空")
    if not Path(command[0]).is_absolute():
        raise ValueError("executor 可执行文件必须使用绝对路径")
    return tuple(command)


def _absolute_path(value: Path, field: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{field} 必须使用绝对路径")
    return path


def observe_executor(
    spec_path: Path,
    result_path: Path,
    receipt_path: Path,
    executor_argv: Sequence[str],
    *,
    clock: Callable[[], float] = time.time,
) -> AttemptCompletionReceipt:
    """等待内部 executor，用真实返回码复核结果后一次性发布凭据。"""
    spec_file = _absolute_path(spec_path, "spec_path")
    result_file = _absolute_path(result_path, "result_path")
    receipt_file = _absolute_path(receipt_path, "receipt_path")
    command = _absolute_command(executor_argv)
    spec = read_attempt_spec(spec_file)
    process = subprocess.Popen(  # noqa: S603 - 可执行文件已限定为显式绝对路径。
        command,
        stdin=subprocess.DEVNULL,
        stdout=None,
        stderr=None,
        close_fds=True,
    )
    process_rc = process.wait(timeout=spec.timeout_sec)
    if type(process_rc) is not int:
        raise ValueError("executor wait 未返回整数返回码")
    result = read_attempt_result(result_file)
    receipt = make_attempt_completion_receipt(
        spec,
        result,
        process_rc=process_rc,
        observed_at=clock(),
    )
    write_attempt_completion_receipt(receipt_file, receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="观察隔离 executor 并发布完成凭据")
    parser.add_argument("--spec", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("executor_argv", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    command = list(args.executor_argv)
    if command and command[0] == "--":
        command.pop(0)
    try:
        observe_executor(
            Path(args.spec),
            Path(args.result),
            Path(args.receipt),
            command,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "observe_executor"]
