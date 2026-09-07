"""Windows Job 后端使用的安全 Win32 原生适配器。

原子 JobList 启动要求 Windows 10 或 Windows Server 2016 及以上。
"""
from __future__ import annotations

import ctypes
import subprocess
from collections.abc import Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codev_platform.reindex.attempt_process import Deadline
from codev_platform.reindex.windows_job_win32 import (
    _CREATE_NO_WINDOW,
    _CREATE_SUSPENDED,
    _ERROR_ALREADY_EXISTS,
    _ERROR_FILE_NOT_FOUND,
    _ERROR_INSUFFICIENT_BUFFER,
    _ERROR_INVALID_PARAMETER,
    _EXTENDED_STARTUPINFO_PRESENT,
    _FILE_APPEND_DATA,
    _FILE_ATTRIBUTE_NORMAL,
    _FILE_SHARE_ALL,
    _GENERIC_READ,
    _HANDLE_FLAG_INHERIT,
    _INVALID_HANDLE_VALUE,
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
    _JOB_OBJECT_LIMIT_BREAKAWAY_OK,
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    _JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
    _JOB_OBJECT_QUERY,
    _JOB_OBJECT_TERMINATE,
    _OPEN_ALWAYS,
    _OPEN_EXISTING,
    _PROCESS_QUERY_LIMITED_INFORMATION,
    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
    _PROC_THREAD_ATTRIBUTE_JOB_LIST,
    _STARTF_USESTDHANDLES,
    _SYNCHRONIZE,
    _WAIT_FAILED,
    _WAIT_OBJECT_0,
    _WAIT_TIMEOUT,
    _JobBasicAccountingInformation,
    _JobExtendedLimitInformation,
    _Kernel32,
    _ProcessInformation,
    _SecurityAttributes,
    _StartupInfoExW,
)


@dataclass(frozen=True, slots=True)
class NativeSuspendedProcess:
    """CreateProcessW 返回且尚未恢复的根进程资源。"""

    pid: int
    process_handle: int
    thread_handle: int
    birth_marker: str


class WindowsJobNativeError(RuntimeError):
    """不携带命令、环境或文件内容的 Win32 失败。"""

    __slots__ = ("operation", "winerror")

    def __init__(self, operation: str, winerror: int) -> None:
        self.operation = operation
        self.winerror = int(winerror)
        super().__init__(f"Windows 原生操作失败：{operation}，错误码 {self.winerror}")


class WindowsJobNativePort(Protocol):
    """供状态机注入和单测替换的窄原生端口。"""

    def create_job(self, name: str) -> int: ...
    def configure_kill_on_close(self, job_handle: int) -> None: ...
    def is_handle_inheritable(self, handle: int) -> bool: ...
    def create_suspended(
        self,
        argv: Sequence[str],
        cwd: Path,
        bootstrap_log: Path,
        deadline: Deadline,
        job_handle: int,
    ) -> NativeSuspendedProcess: ...
    def create_readiness_probe(
        self,
        deadline: Deadline,
        job_handle: int,
    ) -> NativeSuspendedProcess: ...
    def assign_process(self, job_handle: int, process_handle: int) -> None: ...
    def verify_process_in_job(self, job_handle: int, process_handle: int) -> None: ...
    def resume_primary_thread(self, thread_handle: int) -> None: ...
    def poll_process(self, process_handle: int) -> int | None: ...
    def query_active_processes(self, job_handle: int) -> int: ...
    def terminate_job(self, job_handle: int, exit_code: int) -> None: ...
    def terminate_process(self, process_handle: int, exit_code: int) -> None: ...
    def open_job(self, name: str) -> int | None: ...
    def open_process(self, pid: int) -> int | None: ...
    def process_birth_marker(self, process_handle: int) -> str: ...
    def close_handle(self, handle: int) -> None: ...


class CtypesWindowsJobNative:
    """只封装有文档依据的 Kernel32 Job/进程/句柄调用。"""

    def __init__(self) -> None:
        self._api = _Kernel32()

    def create_job(self, name: str) -> int:
        ctypes.set_last_error(0)
        handle = self._api.create_job(None, name)
        error = ctypes.get_last_error()
        if not handle:
            raise WindowsJobNativeError("CreateJobObjectW", error)
        if error == _ERROR_ALREADY_EXISTS:
            self._close_quietly(int(handle))
            raise WindowsJobNativeError("CreateJobObjectW 名称冲突", error)
        return int(handle)

    def configure_kill_on_close(self, job_handle: int) -> None:
        limits = _JobExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        self._check(
            self._api.set_job(
                job_handle,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ),
            "SetInformationJobObject",
        )
        verified = _JobExtendedLimitInformation()
        self._query_job(job_handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, verified)
        flags = int(verified.BasicLimitInformation.LimitFlags)
        forbidden = _JOB_OBJECT_LIMIT_BREAKAWAY_OK | _JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
        if not flags & _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE or flags & forbidden:
            raise WindowsJobNativeError("校验 Job 限制", 0)

    def is_handle_inheritable(self, handle: int) -> bool:
        flags = wintypes.DWORD()
        self._check(
            self._api.get_handle_information(handle, ctypes.byref(flags)),
            "GetHandleInformation",
        )
        return bool(flags.value & _HANDLE_FLAG_INHERIT)

    def create_suspended(
        self,
        argv: Sequence[str],
        cwd: Path,
        bootstrap_log: Path,
        deadline: Deadline,
        job_handle: int,
    ) -> NativeSuspendedProcess:
        if deadline.expired():
            raise WindowsJobNativeError("CreateProcessW 启动预算耗尽", _WAIT_TIMEOUT)
        stdin_handle, log_handle = self._open_stdio(bootstrap_log)
        try:
            process = self._create_process(
                argv,
                cwd,
                stdin_handle,
                log_handle,
                job_handle,
            )
        finally:
            self._close_quietly(stdin_handle)
            self._close_quietly(log_handle)
        return self._describe_suspended(process, deadline)

    def create_readiness_probe(
        self,
        deadline: Deadline,
        job_handle: int,
    ) -> NativeSuspendedProcess:
        """创建固定系统目标；主线程保持 suspended，调用方只能终止。"""
        if deadline.expired():
            raise WindowsJobNativeError("readiness CreateProcessW 预算耗尽", _WAIT_TIMEOUT)
        command, workdir = self._system_probe_command()
        stdin_handle, output_handle = self._open_readiness_stdio()
        process: NativeSuspendedProcess | None = None
        failure: BaseException | None = None
        try:
            raw_process = self._create_process(
                command, workdir, stdin_handle, output_handle, job_handle,
            )
            process = self._describe_suspended(raw_process, deadline)
        except BaseException as error:
            failure = error
        cleanup_errors = self._close_readiness_handles((stdin_handle, output_handle))
        if cleanup_errors and process is not None:
            self._terminate_suspended_quietly(process.process_handle, deadline)
            cleanup_errors.extend(self._close_readiness_handles((
                process.thread_handle,
                process.process_handle,
            )))
        if isinstance(failure, MemoryError):
            raise failure
        if failure is not None and not isinstance(failure, Exception):
            raise failure
        if cleanup_errors:
            raise WindowsJobNativeError("readiness 原生句柄清理", 0) from cleanup_errors[0]
        if failure is not None:
            raise failure
        if process is None:
            raise WindowsJobNativeError("readiness suspended 进程缺失", 0)
        return process

    def _close_readiness_handles(self, handles: Sequence[int]) -> list[BaseException]:
        errors: list[BaseException] = []
        for handle in handles:
            try:
                self.close_handle(handle)
            except BaseException as error:
                errors.append(error)
        return errors

    def _describe_suspended(
        self,
        process: _ProcessInformation,
        deadline: Deadline,
    ) -> NativeSuspendedProcess:
        try:
            marker = self.process_birth_marker(int(process.hProcess))
        except Exception:  # noqa: BLE001 - CreateProcess 已成功，必须回收全部原生资源
            self._terminate_suspended_quietly(int(process.hProcess), deadline)
            self._close_quietly(int(process.hThread))
            self._close_quietly(int(process.hProcess))
            raise
        return NativeSuspendedProcess(
            pid=int(process.dwProcessId),
            process_handle=int(process.hProcess),
            thread_handle=int(process.hThread),
            birth_marker=marker,
        )

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        self._check(
            self._api.assign_job(job_handle, process_handle),
            "AssignProcessToJobObject",
        )

    def verify_process_in_job(self, job_handle: int, process_handle: int) -> None:
        belongs = wintypes.BOOL()
        self._check(
            self._api.is_process_in_job(process_handle, job_handle, ctypes.byref(belongs)),
            "IsProcessInJob",
        )
        if not belongs.value or self.query_active_processes(job_handle) <= 0:
            raise WindowsJobNativeError("校验进程 Job 归属", 0)

    def resume_primary_thread(self, thread_handle: int) -> None:
        previous = int(self._api.resume_thread(thread_handle))
        if previous != 1:
            error = ctypes.get_last_error() if previous == _WAIT_FAILED else 0
            raise WindowsJobNativeError("ResumeThread", error)

    def poll_process(self, process_handle: int) -> int | None:
        state = int(self._api.wait(process_handle, 0))
        if state == _WAIT_TIMEOUT:
            return None
        if state != _WAIT_OBJECT_0:
            raise WindowsJobNativeError("WaitForSingleObject", ctypes.get_last_error())
        exit_code = wintypes.DWORD()
        self._check(
            self._api.get_exit_code(process_handle, ctypes.byref(exit_code)),
            "GetExitCodeProcess",
        )
        return int(exit_code.value)

    def query_active_processes(self, job_handle: int) -> int:
        accounting = _JobBasicAccountingInformation()
        self._query_job(job_handle, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, accounting)
        return int(accounting.ActiveProcesses)

    def terminate_job(self, job_handle: int, exit_code: int) -> None:
        self._check(
            self._api.terminate_job(job_handle, exit_code),
            "TerminateJobObject",
        )

    def terminate_process(self, process_handle: int, exit_code: int) -> None:
        self._check(
            self._api.terminate_process(process_handle, exit_code),
            "TerminateProcess",
        )

    def open_job(self, name: str) -> int | None:
        ctypes.set_last_error(0)
        handle = self._api.open_job(
            _JOB_OBJECT_QUERY | _JOB_OBJECT_TERMINATE,
            False,
            name,
        )
        if handle:
            return int(handle)
        error = ctypes.get_last_error()
        if error == _ERROR_FILE_NOT_FOUND:
            return None
        raise WindowsJobNativeError("OpenJobObjectW", error)

    def open_process(self, pid: int) -> int | None:
        ctypes.set_last_error(0)
        handle = self._api.open_process(
            _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE,
            False,
            pid,
        )
        if handle:
            return int(handle)
        error = ctypes.get_last_error()
        if error == _ERROR_INVALID_PARAMETER:
            return None
        raise WindowsJobNativeError("OpenProcess", error)

    def process_birth_marker(self, process_handle: int) -> str:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        self._check(
            self._api.get_process_times(
                process_handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ),
            "GetProcessTimes",
        )
        value = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return f"windows-filetime:{value}"

    def close_handle(self, handle: int) -> None:
        self._check(self._api.close_handle(handle), "CloseHandle")

    def _system_probe_command(self) -> tuple[tuple[str, ...], Path]:
        buffer = ctypes.create_unicode_buffer(32768)
        length = int(self._api.get_system_directory(buffer, len(buffer)))
        if length <= 0 or length >= len(buffer):
            raise WindowsJobNativeError("GetSystemDirectoryW", ctypes.get_last_error())
        executable = Path(buffer.value) / "cmd.exe"
        if not executable.is_absolute() or not executable.is_file():
            raise WindowsJobNativeError("readiness 系统目标不可用", _ERROR_FILE_NOT_FOUND)
        return (str(executable), "/d", "/c", "exit", "0"), executable.parent

    def _open_readiness_stdio(self) -> tuple[int, int]:
        security = _SecurityAttributes(
            nLength=ctypes.sizeof(_SecurityAttributes),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        stdin_handle = self._api.create_file(
            "NUL",
            _GENERIC_READ,
            _FILE_SHARE_ALL,
            ctypes.byref(security),
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if not stdin_handle or int(stdin_handle) == _INVALID_HANDLE_VALUE:
            raise WindowsJobNativeError("打开 readiness NUL 输入", ctypes.get_last_error())
        try:
            output_handle = self._api.create_file(
                "NUL",
                _FILE_APPEND_DATA,
                _FILE_SHARE_ALL,
                ctypes.byref(security),
                _OPEN_EXISTING,
                _FILE_ATTRIBUTE_NORMAL,
                None,
            )
        except BaseException as error:
            self._raise_readiness_open_failure(int(stdin_handle), error)
            raise
        if not output_handle or int(output_handle) == _INVALID_HANDLE_VALUE:
            error = WindowsJobNativeError("打开 readiness NUL 输出", ctypes.get_last_error())
            self._raise_readiness_open_failure(int(stdin_handle), error)
            raise error
        return int(stdin_handle), int(output_handle)

    def _raise_readiness_open_failure(
        self,
        stdin_handle: int,
        failure: BaseException,
    ) -> None:
        cleanup_errors = self._close_readiness_handles((stdin_handle,))
        for error in (failure, *cleanup_errors):
            if isinstance(error, MemoryError):
                raise error
            if not isinstance(error, Exception):
                raise error
        if cleanup_errors:
            raise WindowsJobNativeError("readiness NUL 输入清理", 0) from cleanup_errors[0]
        raise failure

    def _open_stdio(self, bootstrap_log: Path) -> tuple[int, int]:
        security = _SecurityAttributes(
            nLength=ctypes.sizeof(_SecurityAttributes),
            lpSecurityDescriptor=None,
            bInheritHandle=True,
        )
        log_handle = self._api.create_file(
            str(bootstrap_log),
            _FILE_APPEND_DATA,
            _FILE_SHARE_ALL,
            ctypes.byref(security),
            _OPEN_ALWAYS,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if not log_handle or int(log_handle) == _INVALID_HANDLE_VALUE:
            raise WindowsJobNativeError("打开 bootstrap 日志", ctypes.get_last_error())
        stdin_handle = self._api.create_file(
            "NUL",
            _GENERIC_READ,
            _FILE_SHARE_ALL,
            ctypes.byref(security),
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        if not stdin_handle or int(stdin_handle) == _INVALID_HANDLE_VALUE:
            self._close_quietly(int(log_handle))
            raise WindowsJobNativeError("打开 NUL 标准输入", ctypes.get_last_error())
        return int(stdin_handle), int(log_handle)

    def _create_process(
        self,
        argv: Sequence[str],
        cwd: Path,
        stdin_handle: int,
        log_handle: int,
        job_handle: int,
    ) -> _ProcessInformation:
        handles = (wintypes.HANDLE * 2)(stdin_handle, log_handle)
        jobs = (wintypes.HANDLE * 1)(job_handle)
        attribute_buffer, attribute_list = self._process_attributes(handles, jobs)
        startup = _StartupInfoExW()
        startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoExW)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = stdin_handle
        startup.StartupInfo.hStdOutput = log_handle
        startup.StartupInfo.hStdError = log_handle
        startup.lpAttributeList = attribute_list
        process = _ProcessInformation()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)))
        try:
            created = self._api.create_process(
                str(argv[0]),
                command_line,
                None,
                None,
                True,
                _CREATE_SUSPENDED | _EXTENDED_STARTUPINFO_PRESENT | _CREATE_NO_WINDOW,
                None,
                str(cwd),
                ctypes.byref(startup.StartupInfo),
                ctypes.byref(process),
            )
            self._check(created, "CreateProcessW")
            return process
        finally:
            self._api.delete_attributes(attribute_list)
            del attribute_buffer

    def _process_attributes(
        self,
        handles: ctypes.Array,
        jobs: ctypes.Array,
    ) -> tuple[ctypes.Array, wintypes.LPVOID]:
        size = ctypes.c_size_t()
        ctypes.set_last_error(0)
        self._api.initialize_attributes(None, 2, 0, ctypes.byref(size))
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or size.value <= 0:
            raise WindowsJobNativeError("计算 STARTUPINFOEX 大小", ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        attribute_list = ctypes.cast(buffer, wintypes.LPVOID)
        self._check(
            self._api.initialize_attributes(attribute_list, 2, 0, ctypes.byref(size)),
            "InitializeProcThreadAttributeList",
        )
        try:
            self._check(
                self._api.update_attributes(
                    attribute_list,
                    0,
                    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                    ctypes.cast(handles, wintypes.LPVOID),
                    ctypes.sizeof(handles),
                    None,
                    None,
                ),
                "UpdateProcThreadAttribute",
            )
            self._check(
                self._api.update_attributes(
                    attribute_list,
                    0,
                    _PROC_THREAD_ATTRIBUTE_JOB_LIST,
                    ctypes.cast(jobs, wintypes.LPVOID),
                    ctypes.sizeof(jobs),
                    None,
                    None,
                ),
                "UpdateProcThreadAttribute JobList",
            )
        except WindowsJobNativeError:
            self._api.delete_attributes(attribute_list)
            raise
        return buffer, attribute_list

    def _query_job(self, handle: int, info_class: int, target: ctypes.Structure) -> None:
        self._check(
            self._api.query_job(
                handle,
                info_class,
                ctypes.byref(target),
                ctypes.sizeof(target),
                None,
            ),
            "QueryInformationJobObject",
        )

    def _terminate_suspended_quietly(self, handle: int, deadline: Deadline) -> None:
        try:
            self._api.terminate_process(handle, 125)
            remaining_ms = min(2000, max(0, int(deadline.remaining() * 1000)))
            self._api.wait(handle, remaining_ms)
        except Exception:
            return

    def _close_quietly(self, handle: int) -> None:
        try:
            self._api.close_handle(handle)
        except Exception:
            return

    @staticmethod
    def _check(result: object, operation: str) -> None:
        if not result:
            raise WindowsJobNativeError(operation, ctypes.get_last_error())


__all__ = [
    "CtypesWindowsJobNative",
    "NativeSuspendedProcess",
    "WindowsJobNativeError",
    "WindowsJobNativePort",
]
