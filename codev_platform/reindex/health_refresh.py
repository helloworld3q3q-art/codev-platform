"""有界执行、恢复并轻量聚合 health 子进程。"""
from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .attempt_process import (
    AttemptProcessBackend,
    AttemptProcessStartError,
    Deadline,
    ExecutionHandle,
    RecoveryReport,
    RecoveryState,
    TerminationReport,
    validate_death_proof_for_handle,
)

_MAX_TEXT_BYTES = 4096
_MAX_ARGUMENTS = 128
_MAX_COMMAND_BYTES = 24 * 1024
_HEALTH_WARN_EXIT_CODE = 2


def _bounded_text(value: object, field: str) -> str:
    if type(value) is not str or value != value.strip() or not value:
        raise ValueError(f"{field} 必须是非空规范字符串")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise ValueError(f"{field} 必须是有效 UTF-8") from None
    if len(encoded) > _MAX_TEXT_BYTES or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} 超过长度上限或包含控制字符")
    return value


def _positive_finite(value: object, field: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} 必须是有限正数")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError(f"{field} 必须是有限正数")
    return resolved


def _nonnegative_finite(value: object, field: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} 必须是有限非负数")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError(f"{field} 必须是有限非负数")
    return resolved


def _absolute_path(value: object, field: str) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError(f"{field} 必须是绝对 Path")
    return value


@dataclass(frozen=True, slots=True)
class HealthRefreshCommand:
    """一个 project 的固定 health 命令及受管输出位置。"""

    argv: tuple[str, ...]
    cwd: Path
    bootstrap_log: Path

    def __post_init__(self) -> None:
        if type(self.argv) is not tuple or not self.argv or len(self.argv) > _MAX_ARGUMENTS:
            raise ValueError("health argv 必须是有界非空 tuple")
        total = 0
        for index, argument in enumerate(self.argv):
            total += len(_bounded_text(argument, f"argv[{index}]").encode("utf-8")) + 1
        if total > _MAX_COMMAND_BYTES or not Path(self.argv[0]).is_absolute():
            raise ValueError("health 命令超过上限或可执行文件不是绝对路径")
        _absolute_path(self.cwd, "cwd")
        _absolute_path(self.bootstrap_log, "bootstrap_log")


@dataclass(frozen=True, slots=True)
class HealthOperationEntry:
    """状态适配器耐久保存的完整 health 进程引用。"""

    schema_version: int
    operation_id: str
    project_id: str
    handle: ExecutionHandle
    started_at: float
    timeout_sec: float

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("health operation schema_version 只接受整数 1")
        operation_id = _bounded_text(self.operation_id, "operation_id")
        _bounded_text(self.project_id, "project_id")
        if not isinstance(self.handle, ExecutionHandle):
            raise ValueError("health operation handle 类型无效")
        if self.handle.attempt_id != operation_id:
            raise ValueError("health operation_id 与 handle 不匹配")
        started_at = _nonnegative_finite(self.started_at, "started_at")
        if started_at != self.handle.started_at:
            raise ValueError("health started_at 与 handle 不匹配")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(
            self,
            "timeout_sec",
            _positive_finite(self.timeout_sec, "timeout_sec"),
        )


@dataclass(frozen=True, slots=True)
class HealthRefreshReport:
    """一次 idle flush 的 project 级结果。"""

    attempted_projects: tuple[str, ...]
    succeeded_projects: tuple[str, ...]
    failed_projects: tuple[str, ...]
    containment_confirmed_dead: bool

    def __post_init__(self) -> None:
        groups = (
            self.attempted_projects,
            self.succeeded_projects,
            self.failed_projects,
        )
        if any(type(group) is not tuple for group in groups):
            raise ValueError("health report project 字段必须是 tuple")
        for group in groups:
            for project_id in group:
                _bounded_text(project_id, "project_id")
            if len(group) != len(set(group)):
                raise ValueError("health report project 不得重复")
        attempted, succeeded, failed = map(set, groups)
        if succeeded & failed or succeeded | failed != attempted:
            raise ValueError("health report 成功与失败 project 必须互斥并覆盖已尝试项")
        if type(self.containment_confirmed_dead) is not bool:
            raise ValueError("containment_confirmed_dead 必须是 bool")


class HealthOperationJournalPort(Protocol):
    def load_health(self) -> HealthOperationEntry | None:
        raise NotImplementedError("health operation journal 读取方法")

    def save_health(self, entry: HealthOperationEntry) -> None:
        raise NotImplementedError("health operation journal 保存方法")

    def clear_health(self, *, operation_id: str) -> None:
        raise NotImplementedError("health operation journal 清理方法")


class HealthStatusPort(Protocol):
    def set_health_failed(self, *, failed: bool) -> None:
        raise NotImplementedError("health 状态写入方法")


class HealthRefreshPort(Protocol):
    def request(self, project_id: str) -> None:
        raise NotImplementedError("health refresh 请求方法")

    def flush(self, deadline: Deadline) -> HealthRefreshReport:
        raise NotImplementedError("health refresh 执行方法")

    def recover(self, deadline: Deadline) -> None:
        raise NotImplementedError("health refresh 恢复方法")


class HealthRefreshFatalError(RuntimeError):
    """health 进程或耐久状态存在歧义，调用方必须停止后续工作。"""


class ContainedHealthRefresher:
    """去重请求，并用同一 containment 后端同步执行 health。"""

    def __init__(
        self,
        *,
        process_backend: AttemptProcessBackend,
        journal: HealthOperationJournalPort,
        status: HealthStatusPort,
        command_factory: Callable[[str], HealthRefreshCommand],
        timeout_sec: float,
        kill_grace_sec: float,
        poll_interval_sec: float,
    ) -> None:
        if not callable(command_factory):
            raise ValueError("command_factory 必须可调用")
        self._process = process_backend
        self._journal = journal
        self._status = status
        self._command_factory = command_factory
        self._timeout_sec = _positive_finite(timeout_sec, "timeout_sec")
        self._kill_grace_sec = _nonnegative_finite(kill_grace_sec, "kill_grace_sec")
        self._poll_interval_sec = _positive_finite(
            poll_interval_sec,
            "poll_interval_sec",
        )
        self._pending: dict[str, None] = {}

    def request(self, project_id: str) -> None:
        self._pending.setdefault(_bounded_text(project_id, "project_id"), None)

    def flush(self, deadline: Deadline) -> HealthRefreshReport:
        self._validate_deadline(deadline)
        projects = tuple(self._pending)
        self._pending.clear()
        attempted: list[str] = []
        succeeded: list[str] = []
        failed: list[str] = []
        for index, project_id in enumerate(projects):
            if deadline.expired():
                self._restore_pending(projects[index:])
                break
            attempted.append(project_id)
            try:
                success = self._refresh_project(project_id, deadline)
            except HealthRefreshFatalError:
                self._restore_pending(projects[index + 1 :])
                raise
            (succeeded if success else failed).append(project_id)
        if attempted and not failed and len(attempted) == len(projects):
            self._set_failed(False)
        return HealthRefreshReport(
            tuple(attempted),
            tuple(succeeded),
            tuple(failed),
            True,
        )

    def recover(self, deadline: Deadline) -> None:
        self._validate_deadline(deadline)
        entry = self._load_entry()
        if entry is None:
            return
        try:
            report = self._process.recover_handle(entry.handle, deadline)
        except Exception as error:  # noqa: BLE001 - 恢复歧义统一升级为停止信号
            self._propagate_memory_error(error)
            self._fatal_with_failed("health containment 恢复失败", error)
        self._finish_recovery(entry, report, deadline)

    def _refresh_project(self, project_id: str, deadline: Deadline) -> bool:
        prepared = self._prepare_entry(project_id, deadline)
        if prepared is None:
            return False
        entry, operation_deadline = prepared
        if not self._save_before_activate(entry, deadline):
            return False
        if not self._activate_entry(entry, operation_deadline, deadline):
            return False
        process_rc = self._poll_until(entry.handle, operation_deadline)
        self._require_terminated(entry.handle, deadline)
        # health CLI: 0=全绿，2=仅告警且基础设施仍可用；仅 1 才是关键失败。
        success = type(process_rc) is int and process_rc in (0, _HEALTH_WARN_EXIT_CODE)
        if not success:
            self._set_failed(True)
        self._clear_entry(entry)
        return success

    def _prepare_entry(
        self,
        project_id: str,
        deadline: Deadline,
    ) -> tuple[HealthOperationEntry, Deadline] | None:
        if self._load_entry() is not None:
            raise HealthRefreshFatalError("旧 health operation 尚未恢复")
        try:
            command = self._command_factory(project_id)
        except Exception as error:  # noqa: BLE001 - 命令构造失败是 project health 失败
            self._propagate_memory_error(error)
            self._set_failed(True)
            return None
        if not isinstance(command, HealthRefreshCommand):
            self._set_failed(True)
            return None
        now = time.monotonic()
        budget = min(self._timeout_sec, max(0.0, deadline.expires_at - now))
        if budget <= 0:
            self._set_failed(True)
            self.request(project_id)
            return None
        operation_deadline = Deadline(now + budget)
        operation_id = f"health-{uuid4().hex}"
        try:
            handle = self._process.prepare(
                attempt_id=operation_id,
                argv=command.argv,
                cwd=command.cwd,
                bootstrap_log=command.bootstrap_log,
                deadline=operation_deadline,
            )
        except AttemptProcessStartError as error:
            self._handle_prepare_error(error, operation_id, deadline)
            return None
        except Exception as error:  # noqa: BLE001 - prepare 前失败没有可运行目标
            self._propagate_memory_error(error)
            self._set_failed(True)
            return None
        if not isinstance(handle, ExecutionHandle) or handle.attempt_id != operation_id:
            self._fatal_with_failed("health prepare 返回的 handle 无法确认")
        entry = HealthOperationEntry(
            1,
            operation_id,
            project_id,
            handle,
            handle.started_at,
            budget,
        )
        return entry, operation_deadline

    def _activate_entry(
        self,
        entry: HealthOperationEntry,
        operation_deadline: Deadline,
        cleanup_deadline: Deadline,
    ) -> bool:
        try:
            self._process.activate(entry.handle, operation_deadline)
        except AttemptProcessStartError as error:
            self._finish_activation_error(entry, error, cleanup_deadline)
            return False
        except Exception as error:  # noqa: BLE001 - 已持久化 handle 后统一终止
            self._propagate_memory_error(error)
            self._finish_failed_handle(entry, cleanup_deadline)
            return False
        return True

    def _handle_prepare_error(
        self,
        error: AttemptProcessStartError,
        operation_id: str,
        deadline: Deadline,
    ) -> None:
        handle = error.handle
        if handle is None:
            self._set_failed(True)
            return
        if handle.attempt_id != operation_id:
            self._fatal_with_failed("health prepare 失败携带了不匹配的 handle", error)
        if error.death_proof is None:
            self._require_terminated(handle, deadline)
        self._set_failed(True)

    def _save_before_activate(
        self,
        entry: HealthOperationEntry,
        deadline: Deadline,
    ) -> bool:
        try:
            self._journal.save_health(entry)
            return True
        except Exception as error:  # noqa: BLE001 - 保存失败后必须先收口 blocked 进程
            self._propagate_memory_error(error)
            self._require_terminated(entry.handle, deadline)
            self._set_failed(True)
            persisted = self._load_entry()
            if persisted is not None:
                if persisted != entry:
                    raise HealthRefreshFatalError(
                        "health 保存结果与现有记录冲突"
                    ) from error
                self._clear_entry(entry)
            return False

    def _finish_activation_error(
        self,
        entry: HealthOperationEntry,
        error: AttemptProcessStartError,
        deadline: Deadline,
    ) -> None:
        if error.handle != entry.handle:
            self._fatal_with_failed("health 激活失败携带了不匹配的 handle", error)
        if error.death_proof is None:
            self._require_terminated(entry.handle, deadline)
        self._set_failed(True)
        self._clear_entry(entry)

    def _finish_failed_handle(
        self,
        entry: HealthOperationEntry,
        deadline: Deadline,
    ) -> None:
        self._require_terminated(entry.handle, deadline)
        self._set_failed(True)
        self._clear_entry(entry)

    def _poll_until(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> int | None:
        while True:
            try:
                process_rc = self._process.poll(handle)
            except Exception as error:  # noqa: BLE001 - 探测失败仍必须终止完整 containment
                self._propagate_memory_error(error)
                return None
            if process_rc is not None:
                return process_rc if type(process_rc) is int else None
            remaining = deadline.expires_at - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(self._poll_interval_sec, remaining))

    def _require_terminated(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> TerminationReport:
        try:
            report = self._process.terminate(
                handle,
                grace_sec=self._kill_grace_sec,
                deadline=deadline,
            )
        except Exception as error:  # noqa: BLE001 - 无报告等价于死亡不明
            self._propagate_memory_error(error)
            self._fatal_with_failed("health containment 终止失败，死亡不明", error)
        if not isinstance(report, TerminationReport) or not report.confirmed_dead:
            self._fatal_with_failed("health containment 死亡无法确认")
        try:
            validate_death_proof_for_handle(handle, report.death_proof)
        except ValueError as error:
            self._fatal_with_failed("health containment 死亡证明与 handle 不匹配", error)
        return report

    def _finish_recovery(
        self,
        entry: HealthOperationEntry,
        report: RecoveryReport,
        deadline: Deadline,
    ) -> None:
        if not isinstance(report, RecoveryReport):
            self._fatal_with_failed("health 恢复报告类型无效")
        if report.state is RecoveryState.ACTIVE:
            if report.handle != entry.handle:
                self._fatal_with_failed("health 活动恢复 handle 不匹配")
            self._require_terminated(entry.handle, deadline)
        elif report.state is RecoveryState.CONFIRMED_DEAD:
            if report.handle != entry.handle:
                self._fatal_with_failed("health 死亡恢复 handle 不匹配")
        else:
            self._fatal_with_failed("health 恢复后死亡仍无法确认")
        self._set_failed(True)
        self.request(entry.project_id)
        self._clear_entry(entry)

    def _load_entry(self) -> HealthOperationEntry | None:
        try:
            entry = self._journal.load_health()
        except Exception as error:  # noqa: BLE001 - 状态读取失败禁止覆盖
            self._propagate_memory_error(error)
            raise HealthRefreshFatalError("health operation journal 无法读取") from error
        if entry is not None and not isinstance(entry, HealthOperationEntry):
            raise HealthRefreshFatalError("health operation journal 类型无效")
        return entry

    def _clear_entry(self, entry: HealthOperationEntry) -> None:
        try:
            self._journal.clear_health(operation_id=entry.operation_id)
        except Exception as error:  # noqa: BLE001 - 清理失败保留记录供重启恢复
            self._propagate_memory_error(error)
            raise HealthRefreshFatalError("health operation journal 无法清理") from error

    def _set_failed(self, failed: bool) -> None:
        try:
            self._status.set_health_failed(failed=failed)
        except Exception as error:  # noqa: BLE001 - 状态无法落盘时禁止宣告 idle
            self._propagate_memory_error(error)
            raise HealthRefreshFatalError("health 状态无法耐久更新") from error

    def _fatal_with_failed(
        self,
        note: str,
        cause: Exception | None = None,
    ) -> None:
        self._set_failed(True)
        if cause is None:
            raise HealthRefreshFatalError(note)
        raise HealthRefreshFatalError(note) from cause

    def _restore_pending(self, projects: tuple[str, ...]) -> None:
        self._pending = dict.fromkeys((*projects, *self._pending))

    @staticmethod
    def _validate_deadline(deadline: Deadline) -> None:
        if not isinstance(deadline, Deadline):
            raise ValueError("health deadline 类型无效")

    @staticmethod
    def _propagate_memory_error(error: Exception) -> None:
        if isinstance(error, MemoryError):
            raise error


__all__ = [
    "ContainedHealthRefresher",
    "HealthOperationEntry",
    "HealthOperationJournalPort",
    "HealthRefreshCommand",
    "HealthRefreshFatalError",
    "HealthRefreshPort",
    "HealthRefreshReport",
    "HealthStatusPort",
]
