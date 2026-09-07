"""Windows Job 原生适配器使用的 Win32 ABI 声明。

`PROC_THREAD_ATTRIBUTE_JOB_LIST` 要求 Windows 10 或 Windows Server 2016 及以上。
本模块只声明常量、结构体和 Kernel32 函数签名，不承载资源生命周期策略。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_CREATE_SUSPENDED = 0x00000004
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_NO_WINDOW = 0x08000000
_STARTF_USESTDHANDLES = 0x00000100
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_QUERY = 0x0004
_JOB_OBJECT_TERMINATE = 0x0008
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_HANDLE_FLAG_INHERIT = 0x00000001
_FILE_APPEND_DATA = 0x00000004
_GENERIC_READ = 0x80000000
_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_OPEN_EXISTING = 3
_OPEN_ALWAYS = 4
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_FILE_NOT_FOUND = 2
_ERROR_INVALID_PARAMETER = 87
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_ALREADY_EXISTS = 183
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoExW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfoW),
        ("lpAttributeList", wintypes.LPVOID),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


def _bind(dll: object, name: str, argtypes: list[object], restype: object):
    function = getattr(dll, name)
    function.argtypes = argtypes
    function.restype = restype
    return function


class _Kernel32:
    """集中声明 ABI；调用语义与资源回收由上层原生适配器负责。"""

    def __init__(self) -> None:
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            raise OSError("Windows Job 原生适配器仅支持 Windows")
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        self.create_job = _bind(
            dll,
            "CreateJobObjectW",
            [ctypes.POINTER(_SecurityAttributes), wintypes.LPCWSTR],
            wintypes.HANDLE,
        )
        self.open_job = _bind(
            dll,
            "OpenJobObjectW",
            [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR],
            wintypes.HANDLE,
        )
        self.set_job = _bind(
            dll,
            "SetInformationJobObject",
            [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD],
            wintypes.BOOL,
        )
        self.query_job = _bind(
            dll,
            "QueryInformationJobObject",
            [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.LPVOID,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ],
            wintypes.BOOL,
        )
        self.assign_job = _bind(
            dll,
            "AssignProcessToJobObject",
            [wintypes.HANDLE, wintypes.HANDLE],
            wintypes.BOOL,
        )
        self.is_process_in_job = _bind(
            dll,
            "IsProcessInJob",
            [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)],
            wintypes.BOOL,
        )
        self.terminate_job = _bind(
            dll,
            "TerminateJobObject",
            [wintypes.HANDLE, wintypes.UINT],
            wintypes.BOOL,
        )
        self.create_file = _bind(
            dll,
            "CreateFileW",
            [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(_SecurityAttributes),
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ],
            wintypes.HANDLE,
        )
        self.get_system_directory = _bind(
            dll,
            "GetSystemDirectoryW",
            [wintypes.LPWSTR, wintypes.UINT],
            wintypes.UINT,
        )
        self.initialize_attributes = _bind(
            dll,
            "InitializeProcThreadAttributeList",
            [
                wintypes.LPVOID,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_size_t),
            ],
            wintypes.BOOL,
        )
        self.update_attributes = _bind(
            dll,
            "UpdateProcThreadAttribute",
            [
                wintypes.LPVOID,
                wintypes.DWORD,
                ctypes.c_size_t,
                wintypes.LPVOID,
                ctypes.c_size_t,
                wintypes.LPVOID,
                ctypes.POINTER(ctypes.c_size_t),
            ],
            wintypes.BOOL,
        )
        self.delete_attributes = _bind(
            dll,
            "DeleteProcThreadAttributeList",
            [wintypes.LPVOID],
            None,
        )
        self.create_process = _bind(
            dll,
            "CreateProcessW",
            [
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                ctypes.POINTER(_SecurityAttributes),
                ctypes.POINTER(_SecurityAttributes),
                wintypes.BOOL,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.LPCWSTR,
                ctypes.POINTER(_StartupInfoW),
                ctypes.POINTER(_ProcessInformation),
            ],
            wintypes.BOOL,
        )
        self.resume_thread = _bind(
            dll,
            "ResumeThread",
            [wintypes.HANDLE],
            wintypes.DWORD,
        )
        self.get_process_times = _bind(
            dll,
            "GetProcessTimes",
            [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
            ],
            wintypes.BOOL,
        )
        self.wait = _bind(
            dll,
            "WaitForSingleObject",
            [wintypes.HANDLE, wintypes.DWORD],
            wintypes.DWORD,
        )
        self.get_exit_code = _bind(
            dll,
            "GetExitCodeProcess",
            [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        )
        self.terminate_process = _bind(
            dll,
            "TerminateProcess",
            [wintypes.HANDLE, wintypes.UINT],
            wintypes.BOOL,
        )
        self.open_process = _bind(
            dll,
            "OpenProcess",
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
            wintypes.HANDLE,
        )
        self.get_handle_information = _bind(
            dll,
            "GetHandleInformation",
            [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)],
            wintypes.BOOL,
        )
        self.close_handle = _bind(
            dll,
            "CloseHandle",
            [wintypes.HANDLE],
            wintypes.BOOL,
        )
