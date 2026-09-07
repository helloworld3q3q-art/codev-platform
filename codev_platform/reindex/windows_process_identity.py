"""Windows Job 的持久引用与进程出生身份叶子。"""
from __future__ import annotations

import re
from hashlib import sha256
from dataclasses import dataclass

from codev_platform.reindex.attempt_process import (
    ExecutionHandle,
    ProcessReference,
    build_process_identity,
    validate_process_identity,
)
from codev_platform.reindex.attempts import CanonicalJsonObject

WINDOWS_JOB_KIND = "windows_job_v1"
_SCHEMA_VERSION = 1
_MAX_BYTES = 2048
_JOB_NAME_RE = re.compile(r"Local\\codev-reindex-[A-Za-z0-9-]{8,96}\Z")
_BIRTH_MARKER_RE = re.compile(r"windows-filetime:[1-9][0-9]{0,24}\Z")


@dataclass(frozen=True, slots=True)
class WindowsJobReferenceData:
    """跨进程只保存可复核值，不保存本进程句柄数值。"""

    job_name: str
    pid: int
    birth_marker: str

    def __post_init__(self) -> None:
        validate_windows_job_name(self.job_name)
        if (
            type(self.pid) is not int
            or self.pid <= 0
            or type(self.birth_marker) is not str
            or _BIRTH_MARKER_RE.fullmatch(self.birth_marker) is None
        ):
            raise ValueError("Windows Job 持久引用值无效")


def validate_windows_job_name(value: str) -> str:
    """统一校验 Job 名称命名空间和有界字符集。"""
    if type(value) is not str or _JOB_NAME_RE.fullmatch(value) is None:
        raise ValueError("Windows Job 名称无效")
    return value


def windows_job_name_for_attempt(attempt_id: str) -> str:
    """从持久 attempt_id 确定性派生不可注入的本地 Job 名。"""
    if type(attempt_id) is not str or not attempt_id.strip() or "\x00" in attempt_id:
        raise ValueError("attempt_id 无效")
    try:
        encoded = attempt_id.encode("utf-8")
    except UnicodeError:
        raise ValueError("attempt_id 不是有效 UTF-8") from None
    if len(encoded) > 4096:
        raise ValueError("attempt_id 超过长度上限")
    return f"Local\\codev-reindex-{sha256(encoded).hexdigest()}"


def encode_windows_job_reference(data: WindowsJobReferenceData) -> str:
    """编码为严格、有界、版本化的规范 JSON。"""
    if not isinstance(data, WindowsJobReferenceData):
        raise ValueError("Windows Job 持久引用类型无效")
    return CanonicalJsonObject.from_value(
        {
            "schema_version": _SCHEMA_VERSION,
            "job_name": data.job_name,
            "pid": data.pid,
            "birth_marker": data.birth_marker,
        },
        max_bytes=_MAX_BYTES,
    ).text


def decode_windows_job_reference(value: str) -> WindowsJobReferenceData:
    """拒绝重复键、未知字段、旧版本和非法出生标记。"""
    try:
        payload = CanonicalJsonObject.from_text(value, max_bytes=_MAX_BYTES).to_value()
    except ValueError:
        raise ValueError("Windows Job native_ref 无效") from None
    if set(payload) != {"schema_version", "job_name", "pid", "birth_marker"}:
        raise ValueError("Windows Job native_ref 字段无效")
    if payload["schema_version"] != _SCHEMA_VERSION:
        raise ValueError("Windows Job native_ref 版本无效")
    return WindowsJobReferenceData(
        job_name=payload["job_name"],
        pid=payload["pid"],
        birth_marker=payload["birth_marker"],
    )


def build_windows_execution_handle(
    *,
    attempt_id: str,
    started_at: float,
    job_name: str,
    pid: int,
    birth_marker: str,
) -> ExecutionHandle:
    """从刚创建的内核对象一次性构造完整公共句柄。"""
    data = WindowsJobReferenceData(job_name, pid, birth_marker)
    native_ref = encode_windows_job_reference(data)
    identity = build_process_identity(
        pid=pid,
        native_ref=native_ref,
        birth_marker=birth_marker,
    )
    return ExecutionHandle(
        attempt_id=attempt_id,
        pid=pid,
        process_identity=identity,
        containment_kind=WINDOWS_JOB_KIND,
        native_ref=native_ref,
        started_at=started_at,
    )


def validate_windows_execution_handle(handle: ExecutionHandle) -> WindowsJobReferenceData:
    """复核公共句柄、持久引用和 FILETIME 出生标记三方一致。"""
    if not isinstance(handle, ExecutionHandle) or handle.containment_kind != WINDOWS_JOB_KIND:
        raise ValueError("ExecutionHandle 不是 Windows Job")
    data = decode_windows_job_reference(handle.native_ref)
    _validate_identity(handle.process_identity, handle.native_ref, data)
    if handle.pid != data.pid:
        raise ValueError("ExecutionHandle PID 与 native_ref 不一致")
    return data


def validate_windows_process_reference(
    reference: ProcessReference,
) -> WindowsJobReferenceData:
    """复核管理面最小引用，不接受其他 containment 类型。"""
    if not isinstance(reference, ProcessReference) or reference.containment_kind != WINDOWS_JOB_KIND:
        raise ValueError("ProcessReference 不是 Windows Job")
    data = decode_windows_job_reference(reference.native_ref)
    _validate_identity(reference.process_identity, reference.native_ref, data)
    return data


def _validate_identity(
    process_identity: str,
    native_ref: str,
    data: WindowsJobReferenceData,
) -> None:
    validate_process_identity(
        process_identity,
        pid=data.pid,
        native_ref=native_ref,
        birth_marker=data.birth_marker,
    )


__all__ = [
    "WINDOWS_JOB_KIND",
    "WindowsJobReferenceData",
    "build_windows_execution_handle",
    "decode_windows_job_reference",
    "encode_windows_job_reference",
    "validate_windows_execution_handle",
    "validate_windows_job_name",
    "validate_windows_process_reference",
    "windows_job_name_for_attempt",
]
