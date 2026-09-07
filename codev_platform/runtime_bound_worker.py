"""以根目录 descriptor 启动受限 POSIX worker，避免命名根替换竞态。"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from codev_platform.runtime_deadline import RuntimeDeadlineExceeded, bounded_runtime_timeout
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError
from codev_platform.runtime_worker_pidfd import (
    RuntimeWorkerPidfdError,
    open_current_pidfd,
)
from codev_platform.runtime_worker_scope import RuntimeWorkerScopeError, run_in_worker_scope


_MAX_MESSAGE_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SEC = 120.0
_BOOTSTRAP_PATH = Path(__file__).resolve(strict=True).with_name("runtime_bound_worker_bootstrap.py")
_SOURCE_ROOT = _BOOTSTRAP_PATH.parent.parent
_WorkerOperation = Callable[[dict[str, object], int], dict[str, object]]


class RuntimeBoundWorkerError(RuntimeError):
    """受管根 worker 无法安全启动、通信或完成已注册操作。"""


def run_bound_operation(
    root: BoundRuntimeRoot,
    operation: str,
    payload: dict[str, object],
    *,
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
) -> dict[str, object]:
    """让已注册操作在根 fd 对应 cwd 内运行；调用方必须在返回后复验根。"""
    _require_parent_request(root, operation, payload, timeout_sec)
    try:
        effective_timeout = bounded_runtime_timeout(float(timeout_sec))
    except RuntimeDeadlineExceeded as error:
        raise RuntimeBoundWorkerError("受管根 worker 已耗尽运行时总截止") from error
    descriptor: int | None = None
    parent_pidfd: int | None = None
    try:
        parent_pidfd = _open_parent_pidfd()
        descriptor = root._duplicate_root_fd()
        completed = run_in_worker_scope(
            root_descriptor=descriptor,
            parent_pidfd=parent_pidfd,
            operation=operation,
            payload=_encode_message(payload),
            timeout=effective_timeout,
            bootstrap_path=_BOOTSTRAP_PATH,
            source_root=_SOURCE_ROOT,
        )
    except (OSError, subprocess.SubprocessError, RuntimeWorkerScopeError) as error:
        raise RuntimeBoundWorkerError("受管根 worker 无法安全执行") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)
        if parent_pidfd is not None:
            _close_quietly(parent_pidfd)
    return _decode_parent_response(completed)


def _require_parent_request(
    root: BoundRuntimeRoot,
    operation: str,
    payload: dict[str, object],
    timeout_sec: float,
) -> None:
    if not sys.platform.startswith("linux"):
        raise RuntimeBoundWorkerError("受管根 worker 仅支持 Linux")
    if type(root) is not BoundRuntimeRoot:
        raise RuntimeBoundWorkerError("受管根 worker 的根租约类型无效")
    if operation not in _OPERATIONS:
        raise RuntimeBoundWorkerError("受管根 worker 操作未注册")
    if type(payload) is not dict:
        raise RuntimeBoundWorkerError("受管根 worker 请求必须是对象")
    if type(timeout_sec) not in {int, float} or not 0 < timeout_sec <= 3600:
        raise RuntimeBoundWorkerError("受管根 worker 超时参数无效")
    try:
        root.verify_visible()
    except RuntimeRootBindingError as error:
        raise RuntimeBoundWorkerError("受管根 worker 的根租约已失效") from error
    _encode_message(payload)


def _open_parent_pidfd() -> int:
    """以 pidfd 绑定原调用进程，拒绝退回可被 fork 复制的 pipe。"""
    try:
        return open_current_pidfd()
    except RuntimeWorkerPidfdError as error:
        raise RuntimeBoundWorkerError("受管根 worker 缺少可用 pidfd") from error


def _decode_parent_response(completed: subprocess.CompletedProcess[bytes]) -> dict[str, object]:
    if completed.returncode != 0:
        raise RuntimeBoundWorkerError("受管根 worker 操作失败")
    response = _decode_message(completed.stdout)
    if set(response) != {"ok", "result"} or response.get("ok") is not True:
        raise RuntimeBoundWorkerError("受管根 worker 响应无效")
    result = response["result"]
    if type(result) is not dict:
        raise RuntimeBoundWorkerError("受管根 worker 结果无效")
    return result


def _encode_message(value: object) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise RuntimeBoundWorkerError("受管根 worker 请求不可编码") from error
    if len(payload) > _MAX_MESSAGE_BYTES:
        raise RuntimeBoundWorkerError("受管根 worker 请求超过安全上限")
    return payload


def _decode_message(payload: bytes) -> dict[str, object]:
    if type(payload) is not bytes or not payload or len(payload) > _MAX_MESSAGE_BYTES:
        raise RuntimeBoundWorkerError("受管根 worker 消息无效")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeBoundWorkerError("受管根 worker 消息无效") from error
    if type(value) is not dict:
        raise RuntimeBoundWorkerError("受管根 worker 消息必须是对象")
    return value


def _root_identity(payload: dict[str, object], _root_descriptor: int) -> dict[str, object]:
    if payload:
        raise RuntimeBoundWorkerError("根身份操作不接受请求字段")
    metadata = os.stat(Path("."), follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeBoundWorkerError("worker 当前目录不是根目录")
    return {"device": int(metadata.st_dev), "inode": int(metadata.st_ino)}


def _base_build(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    from codev_platform.runtime_bound_worker_base import build_base

    return build_base(payload, root_descriptor)


def _base_verify(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    from codev_platform.runtime_bound_worker_base import verify_base

    return verify_base(payload, root_descriptor)


def _release_prepare(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    from codev_platform.runtime_bound_worker_release import prepare_release

    return prepare_release(payload, root_descriptor)


def _release_stage(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    from codev_platform.runtime_bound_worker_release import stage_release

    return stage_release(payload, root_descriptor)


def _release_read_base(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    from codev_platform.runtime_bound_worker_release import read_release_base_id

    return read_release_base_id(payload, root_descriptor)


def _release_verify(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    from codev_platform.runtime_bound_worker_release import verify_release

    return verify_release(payload, root_descriptor)


_OPERATIONS: dict[str, _WorkerOperation] = {
    "base-build": _base_build,
    "base-verify": _base_verify,
    "release-prepare": _release_prepare,
    "release-read-base": _release_read_base,
    "release-stage": _release_stage,
    "release-verify": _release_verify,
    "root-identity": _root_identity,
}


def _scope_child_main(root_fd: int, operation: str) -> int:
    """在已证明的 systemd scope 内执行固定注册操作。"""
    descriptor: int | None = root_fd
    try:
        _unblock_worker_termination_signals()
        if operation not in _OPERATIONS:
            raise RuntimeBoundWorkerError("受管根 worker 操作未注册")
        if type(descriptor) is not int or descriptor < 0:
            raise RuntimeBoundWorkerError("受管根 worker descriptor 无效")
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeBoundWorkerError("受管根 worker descriptor 不是目录")
        os.fchdir(descriptor)
        os.set_inheritable(descriptor, False)
        request = _decode_message(sys.stdin.buffer.read(_MAX_MESSAGE_BYTES + 1))
        result = _OPERATIONS[operation](request, descriptor)
        sys.stdout.buffer.write(_encode_message({"ok": True, "result": result}))
        sys.stdout.buffer.flush()
        return 0
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return 1
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def _unblock_worker_termination_signals() -> None:
    """不让 scope 外调用方遗留的信号屏蔽延迟 manager 的常规终止。"""
    if not hasattr(signal, "pthread_sigmask"):
        raise RuntimeBoundWorkerError("受管根 worker 无法控制 Linux 终止信号")
    try:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGHUP, signal.SIGTERM})
    except (OSError, ValueError) as error:
        raise RuntimeBoundWorkerError("受管根 worker 无法解除继承终止信号屏蔽") from error


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


__all__ = ["RuntimeBoundWorkerError", "run_bound_operation"]
