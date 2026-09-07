"""Windows Job 生产 readiness 的 suspended 探针与资源收敛。"""
from __future__ import annotations

import os
import secrets
from collections.abc import Callable

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ProcessBackendReadinessError,
)
from codev_platform.reindex.windows_job_native import (
    NativeSuspendedProcess,
    WindowsJobNativeError,
    WindowsJobNativePort,
)
from codev_platform.reindex.windows_process_identity import windows_job_name_for_attempt

_PROBE_EXIT_CODE = 125


def create_configured_windows_job(
    native: WindowsJobNativePort,
    attempt_id: str,
) -> tuple[int, str]:
    """创建已验证 KILL_ON_CLOSE 且不可继承的唯一 Job。"""
    job_handle: int | None = None
    try:
        name = windows_job_name_for_attempt(attempt_id)
        job_handle = native.create_job(name)
        native.configure_kill_on_close(job_handle)
        if native.is_handle_inheritable(job_handle):
            raise WindowsJobNativeError("Job 句柄可继承", 0)
        return job_handle, name
    except BaseException as error:
        cleanup_error = _close_one(native, job_handle)
        if isinstance(error, MemoryError):
            raise
        if isinstance(cleanup_error, MemoryError):
            raise cleanup_error from error
        if not isinstance(error, Exception):
            raise
        if cleanup_error is not None:
            raise WindowsJobNativeError("关闭未完成配置的 Job", 0) from None
        raise


def create_attempt_windows_job(
    native: WindowsJobNativePort,
    attempt_id: str,
) -> tuple[int, str]:
    """把 Job 配置失败映射为 prepare 前的结构化错误。"""
    try:
        return create_configured_windows_job(native, attempt_id)
    except MemoryError:
        raise
    except Exception:
        raise AttemptProcessStartError(
            handle=None,
            death_proof=None,
            retryable=True,
            note="Windows Job containment 创建失败",
        ) from None


def assert_windows_job_ready(
    native: WindowsJobNativePort,
    deadline: Deadline,
    *,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
    poll_interval: float,
) -> None:
    """以永不 resume 的系统 suspended 目标证明原子 Job containment。"""
    if not isinstance(deadline, Deadline):
        raise ValueError("Windows Job readiness deadline 无效")
    job_handle: int | None = None
    process: NativeSuspendedProcess | None = None
    confirmed = False
    failure: BaseException | None = None
    try:
        _remaining(deadline, monotonic)
        attempt_id = f"readiness-{os.getpid()}-{secrets.token_hex(16)}"
        job_handle, _name = create_configured_windows_job(native, attempt_id)
        _remaining(deadline, monotonic)
        process = native.create_readiness_probe(deadline, job_handle)
        _remaining(deadline, monotonic)
        native.verify_process_in_job(job_handle, process.process_handle)
        _remaining(deadline, monotonic)
        native.terminate_job(job_handle, _PROBE_EXIT_CODE)
        confirmed = _wait_stopped(
            native,
            job_handle,
            process,
            deadline,
            monotonic=monotonic,
            sleeper=sleeper,
            poll_interval=poll_interval,
        )
        if not confirmed:
            raise ProcessBackendReadinessError("Windows Job readiness 死亡确认预算耗尽")
    except BaseException as error:
        failure = error
    cleanup_errors = _cleanup_probe(
        native,
        job_handle,
        process,
        confirmed=confirmed,
        deadline=deadline,
        monotonic=monotonic,
        sleeper=sleeper,
        poll_interval=poll_interval,
    )
    _raise_failure(failure, cleanup_errors)


def _cleanup_probe(
    native: WindowsJobNativePort,
    job_handle: int | None,
    process: NativeSuspendedProcess | None,
    *,
    confirmed: bool,
    deadline: Deadline,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
    poll_interval: float,
) -> list[BaseException]:
    errors: list[BaseException] = []
    if process is not None and not confirmed:
        for action, arguments in (
            (native.terminate_job, (job_handle, _PROBE_EXIT_CODE)),
            (native.terminate_process, (process.process_handle, _PROBE_EXIT_CODE)),
        ):
            if job_handle is None and action == native.terminate_job:
                continue
            _capture(errors, action, *arguments)
        try:
            stopped = job_handle is not None and _wait_stopped(
                native,
                job_handle,
                process,
                deadline,
                monotonic=monotonic,
                sleeper=sleeper,
                poll_interval=poll_interval,
            )
        except BaseException as error:
            errors.append(error)
        else:
            if not stopped:
                errors.append(RuntimeError("readiness suspended 探针死亡未确认"))
    if process is not None:
        for handle in (process.thread_handle, process.process_handle):
            _capture(errors, native.close_handle, handle)
    if job_handle is not None:
        _capture(errors, native.close_handle, job_handle)
    return errors


def _wait_stopped(
    native: WindowsJobNativePort,
    job_handle: int,
    process: NativeSuspendedProcess,
    deadline: Deadline,
    *,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
    poll_interval: float,
) -> bool:
    while True:
        remaining = deadline.remaining(now=monotonic())
        if remaining <= 0:
            return False
        process_rc = native.poll_process(process.process_handle)
        active = native.query_active_processes(job_handle)
        remaining = deadline.remaining(now=monotonic())
        if process_rc is not None and active == 0:
            return remaining > 0
        if remaining <= 0:
            return False
        sleeper(min(poll_interval, remaining))


def _remaining(deadline: Deadline, monotonic: Callable[[], float]) -> float:
    remaining = deadline.remaining(now=monotonic())
    if remaining <= 0:
        raise ProcessBackendReadinessError("Windows Job readiness 预算已耗尽")
    return remaining


def _capture(errors: list[BaseException], action, *arguments: object) -> None:
    try:
        action(*arguments)
    except BaseException as error:
        errors.append(error)


def _close_one(
    native: WindowsJobNativePort,
    handle: int | None,
) -> BaseException | None:
    if handle is None:
        return None
    try:
        native.close_handle(handle)
    except BaseException as error:
        return error
    return None


def _raise_failure(
    failure: BaseException | None,
    cleanup_errors: list[BaseException],
) -> None:
    errors = ([failure] if failure is not None else []) + cleanup_errors
    for error in errors:
        if isinstance(error, MemoryError):
            raise error
        if not isinstance(error, Exception):
            raise error
    if cleanup_errors:
        raise ProcessBackendReadinessError("Windows Job readiness 清理存在歧义") from None
    if isinstance(failure, ProcessBackendReadinessError):
        raise failure
    if failure is not None:
        raise ProcessBackendReadinessError("Windows Job readiness 探针失败") from None


__all__ = ["assert_windows_job_ready", "create_attempt_windows_job"]
