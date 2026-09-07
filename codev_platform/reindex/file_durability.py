"""协议文件在 POSIX/Windows 上的安全写入、耐久发布与删除策略。"""
from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

_MOVEFILE_REPLACE_EXISTING = 0x1
_MOVEFILE_WRITE_THROUGH = 0x8
_MISSING_WINERRORS = {2, 3}
_EXISTS_WINERRORS = {80, 183}
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
_FILE_SHARE_DELETE = 0x4
_FILE_SHARE_READ = 0x1
_FILE_TYPE_DISK = 0x1
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_CREATE_NEW = 0x1
_FILE_ATTRIBUTE_NORMAL = 0x80
_OPEN_EXISTING = 0x3
_READ_CHUNK_BYTES = 64 * 1024


def _bounded_limit(max_bytes: int) -> int:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("协议文件大小上限必须是正整数")
    return max_bytes


def _read_descriptor_bounded(descriptor: int, max_bytes: int) -> bytes:
    data = bytearray()
    while len(data) <= max_bytes:
        capacity = min(_READ_CHUNK_BYTES, max_bytes + 1 - len(data))
        try:
            chunk = os.read(descriptor, capacity)
        except InterruptedError:
            continue
        except BlockingIOError:
            raise ValueError("协议路径不是可同步读取的普通文件") from None
        if not chunk:
            break
        data.extend(chunk)
    if len(data) > max_bytes:
        raise ValueError("协议文件超过大小上限")
    return bytes(data)


def _required_posix_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise OSError(errno.ENOTSUP, f"当前平台缺少安全读取标志 {name}")
    return value


def _read_posix_regular_file(path: Path, max_bytes: int) -> bytes:
    flags = (
        os.O_RDONLY
        | _required_posix_flag("O_NOFOLLOW")
        | _required_posix_flag("O_CLOEXEC")
        | _required_posix_flag("O_NONBLOCK")
        | getattr(os, "O_BINARY", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("协议路径不能是符号链接") from None
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("协议路径必须是普通文件")
        return _read_descriptor_bounded(descriptor, max_bytes)
    finally:
        os.close(descriptor)


def _windows_path_text(path: Path) -> str:
    text = os.fspath(path)
    normalized = text.replace("/", "\\")
    if normalized.startswith(("\\\\.\\", "\\\\?\\")):
        raise ValueError("协议路径不能使用 Windows 设备命名空间")
    return text


def _read_windows_regular_file(path: Path, max_bytes: int) -> bytes:
    import ctypes
    from ctypes import wintypes

    class _FileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("access_time", wintypes.FILETIME),
            ("write_time", wintypes.FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    file_info = kernel32.GetFileInformationByHandle
    file_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInformation)]
    file_info.restype = wintypes.BOOL
    file_type = kernel32.GetFileType
    file_type.argtypes = [wintypes.HANDLE]
    file_type.restype = wintypes.DWORD
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    read_file.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    flags = (
        _FILE_FLAG_BACKUP_SEMANTICS
        | _FILE_FLAG_OPEN_REPARSE_POINT
        | _FILE_FLAG_SEQUENTIAL_SCAN
    )
    handle = create_file(
        _windows_path_text(path),
        _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        flags,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if file_type(handle) != _FILE_TYPE_DISK:
            raise ValueError("协议路径必须是普通文件且不能是 reparse point")
        information = _FileInformation()
        if not file_info(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        unsafe = _FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT
        if information.attributes & unsafe:
            raise ValueError("协议路径必须是普通文件且不能是 reparse point")
        size = (information.size_high << 32) | information.size_low
        if size > max_bytes:
            raise ValueError("协议文件超过大小上限")
        return _read_windows_handle_bounded(
            handle,
            max_bytes,
            read_file=read_file,
        )
    finally:
        close_handle(handle)


def _read_windows_handle_bounded(
    handle: object,
    max_bytes: int,
    *,
    read_file: Callable[..., int],
) -> bytes:
    import ctypes
    from ctypes import wintypes

    data = bytearray()
    while len(data) <= max_bytes:
        capacity = min(_READ_CHUNK_BYTES, max_bytes + 1 - len(data))
        buffer = ctypes.create_string_buffer(capacity)
        count = wintypes.DWORD()
        if not read_file(handle, buffer, capacity, ctypes.byref(count), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if count.value == 0:
            break
        data.extend(buffer.raw[: count.value])
    if len(data) > max_bytes:
        raise ValueError("协议文件超过大小上限")
    return bytes(data)


def read_regular_file_bounded(path: Path, *, max_bytes: int) -> bytes:
    """不跟随最终链接，只从普通文件句柄读取有限字节。"""
    limit = _bounded_limit(max_bytes)
    target = Path(path)
    if os.name == "nt":
        return _read_windows_regular_file(target, limit)
    return _read_posix_regular_file(target, limit)


def _secure_temp_flags() -> int:
    return (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise OSError("协议文件写入未取得进展")
        offset += written


def _windows_current_user_sid() -> str:
    """读取当前进程令牌用户 SID，避免把管理员组误当成当前用户。"""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_process = kernel32.GetCurrentProcess
    get_process.argtypes = []
    get_process.restype = wintypes.HANDLE
    open_token = advapi32.OpenProcessToken
    open_token.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    open_token.restype = wintypes.BOOL
    get_token = advapi32.GetTokenInformation
    get_token.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_token.restype = wintypes.BOOL
    sid_to_text = advapi32.ConvertSidToStringSidW
    sid_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    sid_to_text.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    token = wintypes.HANDLE()
    if not open_token(get_process(), 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        required = wintypes.DWORD()
        get_token(token, 1, None, 0, ctypes.byref(required))
        if ctypes.get_last_error() != 122 or required.value <= 0:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(required.value)
        if not get_token(token, 1, buffer, required, ctypes.byref(required)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(buffer, ctypes.POINTER(_SidAndAttributes)).contents.sid
        text = wintypes.LPWSTR()
        if not sid_to_text(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return text.value
        finally:
            local_free(ctypes.cast(text, ctypes.c_void_p))
    finally:
        close_handle(token)


def _open_windows_owner_only(path: Path) -> int:
    """用 protected DACL 原子创建文件，并把原生句柄交给 CRT fd。"""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    convert.restype = wintypes.BOOL
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("security_descriptor", ctypes.c_void_p),
            ("inherit_handle", wintypes.BOOL),
        ]

    descriptor = ctypes.c_void_p()
    sddl = f"D:P(A;;FA;;;{_windows_current_user_sid()})"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes),
            descriptor,
            False,
        )
        handle = create_file(
            _windows_path_text(path),
            _GENERIC_WRITE,
            0,
            ctypes.byref(attributes),
            _CREATE_NEW,
            _FILE_ATTRIBUTE_NORMAL,
            None,
        )
        create_error = (
            ctypes.get_last_error()
            if handle == ctypes.c_void_p(-1).value
            else 0
        )
    finally:
        local_free(descriptor)
    if handle == ctypes.c_void_p(-1).value:
        if create_error in _EXISTS_WINERRORS:
            raise FileExistsError(errno.EEXIST, "临时文件已存在", str(path))
        raise ctypes.WinError(create_error)
    flags = os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    try:
        return msvcrt.open_osfhandle(handle, flags)
    except BaseException:
        close_handle(handle)
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _open_secure_temp(path: Path) -> int:
    if os.name == "nt":
        return _open_windows_owner_only(path)
    descriptor = os.open(path, _secure_temp_flags(), 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    except BaseException:
        os.close(descriptor)
        _remove_owned_temp(path)
        raise
    return descriptor


def _remove_owned_temp(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _write_secure_temp(target: Path, data: bytes) -> Path:
    if type(data) is not bytes:
        raise TypeError("协议文件内容必须是 bytes")
    if os.name == "nt":
        _windows_path_text(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    owned = False
    try:
        descriptor = _open_secure_temp(temp)
        owned = True
        _write_all(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        return temp
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if owned:
            _remove_owned_temp(temp)
        raise


def fsync_directory(path: Path) -> None:
    """同步 POSIX 目录项；Windows 状态变更由 write-through rename 保证。"""
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _move_file(source: Path, target: Path, *, replace: bool) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file.restype = wintypes.BOOL
    flags = _MOVEFILE_WRITE_THROUGH
    if replace:
        flags |= _MOVEFILE_REPLACE_EXISTING
    source_text = _windows_path_text(source)
    target_text = _windows_path_text(target)
    if not move_file(source_text, target_text, flags):
        code = ctypes.get_last_error()
        if not replace and code in _EXISTS_WINERRORS:
            raise FileExistsError(errno.EEXIST, "目标已存在", str(target))
        raise ctypes.WinError(code)


def _move_file_write_through(source: Path, target: Path) -> None:
    _move_file(source, target, replace=True)


def _move_file_write_through_once(source: Path, target: Path) -> None:
    _move_file(source, target, replace=False)


def durable_replace(source: Path, target: Path) -> None:
    """原子替换目标，并等待对应平台的目录项持久化屏障。"""
    if os.name == "nt":
        _move_file_write_through(source, target)
        return
    os.replace(source, target)
    fsync_directory(target.parent)


def _publish_replace(source: Path, target: Path) -> None:
    durable_replace(source, target)


def _publish_once(source: Path, target: Path) -> None:
    if os.name == "nt":
        _move_file_write_through_once(source, target)
        return
    os.link(source, target, follow_symlinks=False)
    try:
        source.unlink()
    finally:
        fsync_directory(target.parent)


def durable_write_once(path: Path, data: bytes) -> None:
    """完整同步临时文件后，一次性发布到尚不存在的目标。"""
    target = Path(path)
    temp = _write_secure_temp(target, data)
    try:
        _publish_once(temp, target)
    finally:
        _remove_owned_temp(temp)


def durable_write_replace(path: Path, data: bytes) -> None:
    """完整同步临时文件后，原子替换目标并同步目录元数据。"""
    target = Path(path)
    temp = _write_secure_temp(target, data)
    try:
        _publish_replace(temp, target)
    finally:
        _remove_owned_temp(temp)


def durable_unlink(path: Path) -> bool:
    """耐久移除状态名；Windows 先 write-through 改名再清理墓碑。"""
    if os.name != "nt":
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        fsync_directory(path.parent)
        return True
    tombstone = path.with_name(f".{path.name}.{uuid4().hex}.deleted")
    try:
        _move_file_write_through(path, tombstone)
    except OSError as exc:
        if getattr(exc, "winerror", None) in _MISSING_WINERRORS:
            return False
        raise
    try:
        tombstone.unlink()
    except OSError:
        pass
    return True


__all__ = [
    "durable_replace",
    "durable_unlink",
    "durable_write_once",
    "durable_write_replace",
    "fsync_directory",
    "read_regular_file_bounded",
]
