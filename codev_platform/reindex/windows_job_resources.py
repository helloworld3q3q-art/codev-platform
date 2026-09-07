"""Windows Job 进程资源的进程内所有权表。"""
from __future__ import annotations

import threading
from dataclasses import dataclass

from codev_platform.reindex.attempt_process import ExecutionHandle
from codev_platform.reindex.windows_job_native import (
    NativeSuspendedProcess,
    WindowsJobNativePort,
)
from codev_platform.reindex.windows_process_identity import (
    build_windows_execution_handle,
    validate_windows_execution_handle,
)


def propagate_memory_error(error: Exception) -> None:
    """普通异常可映射，内存耗尽保持原始传播语义。"""
    if isinstance(error, MemoryError):
        raise error


def _run_cleanup(action, *arguments: int) -> None:
    """普通清理失败不能阻断其余句柄回收；内存耗尽仍向上传播。"""
    try:
        action(*arguments)
    except Exception as error:  # noqa: BLE001 - 事务回收必须继续处理其余资源
        propagate_memory_error(error)
        return


@dataclass(slots=True)
class OwnedWindowsJob:
    """只保存当前父进程真正拥有的原生句柄。"""

    handle: ExecutionHandle
    job_handle: int
    process_handle: int | None
    thread_handle: int | None
    assigned: bool | None


def build_owned_windows_job(
    *,
    attempt_id: str,
    started_at: float,
    job_name: str,
    job_handle: int,
    process: NativeSuspendedProcess,
) -> OwnedWindowsJob:
    """把已创建 suspended 进程一次性转成可登记资源记录。"""
    handle = build_windows_execution_handle(
        attempt_id=attempt_id,
        started_at=started_at,
        job_name=job_name,
        pid=process.pid,
        birth_marker=process.birth_marker,
    )
    return OwnedWindowsJob(
        handle=handle,
        job_handle=job_handle,
        process_handle=process.process_handle,
        thread_handle=process.thread_handle,
        assigned=False,
    )


def discard_unidentified_suspended_process(
    native: WindowsJobNativePort,
    *,
    job_handle: int,
    process: NativeSuspendedProcess,
    exit_code: int,
) -> None:
    """身份构造失败时围栏并终止未运行根进程，再关闭全部父侧句柄。"""
    actions = (
        (native.assign_process, (job_handle, process.process_handle)),
        (native.terminate_job, (job_handle, exit_code)),
        (native.terminate_process, (process.process_handle, exit_code)),
    )
    for action, arguments in actions:
        _run_cleanup(action, *arguments)
    for handle in (process.thread_handle, process.process_handle, job_handle):
        _run_cleanup(native.close_handle, handle)


class WindowsJobResourceTable:
    """原子登记资源，并只释放当前记录自身拥有的句柄。"""

    def __init__(self, native: WindowsJobNativePort) -> None:
        self._native = native
        self._records: dict[str, OwnedWindowsJob] = {}
        self._lock = threading.RLock()

    def remember(self, record: OwnedWindowsJob) -> None:
        with self._lock:
            if record.handle.native_ref in self._records:
                raise ValueError("Windows Job native_ref 重复")
            self._records[record.handle.native_ref] = record

    def require(self, handle: ExecutionHandle) -> OwnedWindowsJob:
        validate_windows_execution_handle(handle)
        record = self.find(handle.native_ref)
        if record is None or record.handle != handle:
            raise ValueError("Windows Job 内存句柄不存在或不匹配")
        return record

    def find(self, native_ref: str) -> OwnedWindowsJob | None:
        with self._lock:
            return self._records.get(native_ref)

    def find_attempt(self, attempt_id: str) -> OwnedWindowsJob | None:
        with self._lock:
            return next(
                (
                    record
                    for record in self._records.values()
                    if record.handle.attempt_id == attempt_id
                ),
                None,
            )

    def close_thread(self, record: OwnedWindowsJob) -> None:
        if record.thread_handle is None:
            return
        try:
            self._native.close_handle(record.thread_handle)
            record.thread_handle = None
        except MemoryError:
            raise
        except Exception:  # noqa: BLE001 - 后续 Job 终止仍必须执行
            return

    def abort(self, record: OwnedWindowsJob, *, exit_code: int) -> None:
        """终止有效记录并原子撤销可能已经完成的登记。"""
        _run_cleanup(self._native.terminate_job, record.job_handle, exit_code)
        if record.process_handle is not None:
            _run_cleanup(
                self._native.terminate_process,
                record.process_handle,
                exit_code,
            )
        self.release(record)

    def release(self, record: OwnedWindowsJob) -> None:
        with self._lock:
            if self._records.get(record.handle.native_ref) is record:
                self._records.pop(record.handle.native_ref)
        for native_handle in (
            record.thread_handle,
            record.process_handle,
            record.job_handle,
        ):
            if native_handle is not None:
                self._close_quietly(native_handle)
        record.thread_handle = None
        record.process_handle = None

    def close_handle(self, handle: int) -> None:
        """关闭后端临时持有、尚未登记到记录的单个句柄。"""
        self._close_quietly(handle)

    def _close_quietly(self, handle: int) -> None:
        _run_cleanup(self._native.close_handle, handle)


__all__ = [
    "OwnedWindowsJob",
    "WindowsJobResourceTable",
    "build_owned_windows_job",
    "discard_unidentified_suspended_process",
    "propagate_memory_error",
]
