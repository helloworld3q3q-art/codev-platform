"""以 Windows Job Object 围栏单次 reindex attempt。"""
from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError, Deadline, ExecutionHandle, ProcessReference,
    RecoveryReport, RecoveryState, TerminationReport, execution_handle_from_journal,
)
from codev_platform.reindex.attempts import AttemptJournalEntry, ConfirmedProcessDeath
from codev_platform.reindex.bootstrap_runtime import clock_now
from codev_platform.reindex.windows_job_readiness import (
    assert_windows_job_ready,
    create_attempt_windows_job,
)
from codev_platform.reindex.windows_job_native import (
    WindowsJobNativeError, WindowsJobNativePort,
)
from codev_platform.reindex.windows_job_resources import (
    OwnedWindowsJob,
    WindowsJobResourceTable,
    build_owned_windows_job,
    discard_unidentified_suspended_process,
    propagate_memory_error,
)
from codev_platform.reindex.windows_process_evidence import (
    build_windows_death_proof, build_windows_recovery_report,
    build_windows_termination_report,
)
from codev_platform.reindex.windows_process_identity import (
    WINDOWS_JOB_KIND, WindowsJobReferenceData, validate_windows_execution_handle,
    validate_windows_process_reference,
    windows_job_name_for_attempt,
)

_TERMINATE_EXIT_CODE = 124


class WindowsJobAttemptProcessBackend:
    """只编排 Job 生命周期；所有 Win32 调用委托给窄原生端口。"""

    def __init__(
        self,
        *,
        native: WindowsJobNativePort,
        monotonic_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        poll_interval_sec: float = 0.02,
    ) -> None:
        if not callable(monotonic_clock) or not callable(wall_clock) or not callable(sleeper):
            raise ValueError("Windows Job 时钟依赖无效")
        interval = float(poll_interval_sec)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Windows Job 轮询间隔无效")
        self._native = native
        self._monotonic_clock = monotonic_clock
        self._wall_clock = wall_clock
        self._sleeper = sleeper
        self._poll_interval_sec = interval
        self._resources = WindowsJobResourceTable(native)

    def assert_ready(self, deadline: Deadline) -> None:
        assert_windows_job_ready(
            self._native,
            deadline,
            monotonic=self._monotonic_now,
            sleeper=self._sleeper,
            poll_interval=self._poll_interval_sec,
        )

    def prepare(
        self,
        *,
        attempt_id: str,
        argv: Sequence[str],
        cwd: Path,
        bootstrap_log: Path,
        deadline: Deadline,
    ) -> ExecutionHandle:
        command, workdir, log_path = self._validate_prepare(
            attempt_id,
            argv,
            cwd,
            bootstrap_log,
            deadline,
        )
        if deadline.expired(now=self._monotonic_now()):
            raise self._prepare_error("Windows Job 准备预算已耗尽")
        started_at = self._wall_now()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        job_handle, job_name = create_attempt_windows_job(self._native, attempt_id)
        try:
            process = self._native.create_suspended(
                command,
                workdir,
                log_path,
                deadline,
                job_handle,
            )
        except Exception as error:  # noqa: BLE001 - Job 已创建，必须先关闭再映射错误
            self._resources.close_handle(job_handle)
            propagate_memory_error(error)
            raise self._prepare_error("Windows Job 挂起进程创建失败") from None
        try:
            record = build_owned_windows_job(
                attempt_id=attempt_id,
                started_at=started_at,
                job_name=job_name,
                job_handle=job_handle,
                process=process,
            )
        except Exception as error:  # noqa: BLE001 - suspended 资源必须事务回收
            discard_unidentified_suspended_process(
                self._native,
                job_handle=job_handle,
                process=process,
                exit_code=_TERMINATE_EXIT_CODE,
            )
            propagate_memory_error(error)
            raise self._prepare_error(
                "Windows Job 进程身份构造失败",
                retryable=False,
            ) from None
        try:
            self._resources.remember(record)
        except Exception as error:  # noqa: BLE001 - 登记可能部分成功，必须撤销并回收
            self._resources.abort(record, exit_code=_TERMINATE_EXIT_CODE)
            propagate_memory_error(error)
            raise AttemptProcessStartError(
                handle=record.handle,
                death_proof=None,
                retryable=False,
                note="Windows Job 资源登记失败",
            ) from None
        try:
            self._native.verify_process_in_job(job_handle, process.process_handle)
            record.assigned = True
        except Exception as error:  # noqa: BLE001 - 归属验证歧义必须保留 handle 并失败关闭
            record.assigned = None
            self._resources.close_thread(record)
            self._raise_partial_start(
                record,
                deadline,
                "Windows Job 原子归属验证失败",
                cause=error,
            )
        return record.handle

    def activate(self, handle: ExecutionHandle, deadline: Deadline) -> None:
        if not isinstance(deadline, Deadline):
            raise ValueError("Windows Job deadline 无效")
        record = self._resources.require(handle)
        if record.assigned is not True:
            self._raise_partial_start(record, deadline, "Windows Job 尚未完成分配")
        if record.thread_handle is None:
            return
        if deadline.expired(now=self._monotonic_now()):
            self._raise_partial_start(record, deadline, "Windows Job 激活预算已耗尽")
        try:
            self._native.resume_primary_thread(record.thread_handle)
            self._native.close_handle(record.thread_handle)
            record.thread_handle = None
        except Exception as error:  # noqa: BLE001 - 激活失败必须转为带 handle 的结构化错误
            self._raise_partial_start(
                record,
                deadline,
                "Windows Job 恢复主线程失败",
                cause=error,
            )

    def poll(self, handle: ExecutionHandle) -> int | None:
        record = self._resources.require(handle)
        if record.process_handle is None:
            try:
                return None if self._native.query_active_processes(record.job_handle) > 0 else 0
            except WindowsJobNativeError:
                return None
        return self._native.poll_process(record.process_handle)

    def terminate(
        self,
        handle: ExecutionHandle,
        *,
        grace_sec: float,
        deadline: Deadline,
    ) -> TerminationReport:
        grace = self._grace(grace_sec)
        requested = self._wall_now()
        record = self._resources.require(handle)
        grace_end = min(deadline.expires_at, self._monotonic_now() + grace)
        proof = self._wait_record_death(record, grace_end)
        if proof is not None:
            return build_windows_termination_report(
                requested_at=requested,
                finished_at=self._wall_now(),
                graceful=True,
                forced=False,
                proof=proof,
                note="Windows Job 在宽限期内自然退出",
            )
        force_error = self._force_record(record)
        proof = self._wait_record_death(record, deadline.expires_at)
        note = "Windows Job 强制终止后已确认退出"
        if proof is None:
            note = "Windows Job 强制终止后死亡未确认"
            if force_error:
                note = "Windows Job 强制终止结果有歧义且死亡未确认"
        return build_windows_termination_report(
            requested_at=requested,
            finished_at=self._wall_now(),
            graceful=False,
            forced=True,
            proof=proof,
            note=note,
        )

    def recover(
        self,
        journal: AttemptJournalEntry,
        deadline: Deadline,
    ) -> RecoveryReport:
        try:
            handle = execution_handle_from_journal(journal)
        except ValueError:
            return build_windows_recovery_report(RecoveryState.UNCONFIRMED, note="journal 进程字段不完整")
        if handle is None:
            existing = self._resources.find_attempt(journal.attempt_id)
            if existing is not None:
                proof = self._wait_record_death(existing, self._monotonic_now())
                if proof is not None:
                    return build_windows_recovery_report(
                        RecoveryState.CONFIRMED_DEAD,
                        handle=existing.handle,
                        proof=proof,
                    )
                return build_windows_recovery_report(RecoveryState.ACTIVE, handle=existing.handle)
            return self._recover_unbound_claim(journal.attempt_id, deadline)
        return self.recover_handle(handle, deadline)

    def recover_handle(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> RecoveryReport:
        try:
            data = validate_windows_execution_handle(handle)
            if data.job_name != windows_job_name_for_attempt(handle.attempt_id):
                raise ValueError("attempt 与 Windows Job 不匹配")
        except ValueError:
            return build_windows_recovery_report(RecoveryState.UNCONFIRMED, note="Windows Job 句柄身份有歧义")
        existing = self._resources.find(handle.native_ref)
        if existing is None:
            return self._recover_native_handle(handle, data, deadline)
        if existing.handle != handle:
            return build_windows_recovery_report(RecoveryState.UNCONFIRMED, note="Windows Job 内存句柄冲突")
        proof = self._wait_record_death(existing, self._monotonic_now())
        if proof is None:
            return build_windows_recovery_report(RecoveryState.ACTIVE, handle=handle)
        return build_windows_recovery_report(
            RecoveryState.CONFIRMED_DEAD, handle=handle, proof=proof
        )

    def _recover_unbound_claim(
        self,
        attempt_id: str,
        deadline: Deadline,
    ) -> RecoveryReport:
        """收口 prepare→持久化窗口；未发布的挂起进程不得进入活动态。"""
        try:
            name = windows_job_name_for_attempt(attempt_id)
            job_handle = self._native.open_job(name)
        except (ValueError, WindowsJobNativeError):
            return build_windows_recovery_report(RecoveryState.UNCONFIRMED, note="确定性 Windows Job 无法查询")
        if job_handle is None:
            return build_windows_recovery_report(
                RecoveryState.NEVER_STARTED,
                note="确定性 Windows Job 不存在，目标进程未被激活",
            )
        try:
            self._native.terminate_job(job_handle, _TERMINATE_EXIT_CODE)
            while self._native.query_active_processes(job_handle) != 0:
                if self._monotonic_now() >= deadline.expires_at:
                    return build_windows_recovery_report(
                        RecoveryState.UNCONFIRMED,
                        note="未发布 Windows Job 在截止前未清空",
                    )
                self._sleep_until(deadline.expires_at)
        except WindowsJobNativeError:
            return build_windows_recovery_report(
                RecoveryState.UNCONFIRMED,
                note="未发布 Windows Job 终止或状态查询失败",
            )
        finally:
            self._resources.close_handle(job_handle)
        return build_windows_recovery_report(
            RecoveryState.NEVER_STARTED,
            note="未发布 Windows Job 已清空，目标进程未被激活",
        )

    def confirm_dead(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        data = validate_windows_execution_handle(handle)
        record = self._resources.find(handle.native_ref)
        if record is not None:
            return self._wait_record_death(record, deadline.expires_at)
        return self._confirm_subject_dead(handle, data, deadline)

    def confirm_reference_dead(
        self,
        reference: ProcessReference,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        data = validate_windows_process_reference(reference)
        record = self._resources.find(reference.native_ref)
        if record is not None:
            return self._wait_record_death(record, deadline.expires_at)
        return self._confirm_subject_dead(reference, data, deadline)

    def _confirm_subject_dead(
        self,
        subject: ExecutionHandle | ProcessReference,
        data: WindowsJobReferenceData,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        try:
            job_handle = self._native.open_job(data.job_name)
        except WindowsJobNativeError:
            return None
        if job_handle is not None:
            return self._wait_open_job_zero(subject, job_handle, deadline)
        return self._wait_closed_job_identity(subject, data, deadline)

    def _recover_native_handle(
        self,
        handle: ExecutionHandle,
        data: WindowsJobReferenceData,
        deadline: Deadline,
    ) -> RecoveryReport:
        try:
            job_handle = self._native.open_job(data.job_name)
        except WindowsJobNativeError:
            return build_windows_recovery_report(RecoveryState.UNCONFIRMED, note="Windows Job 无法重新打开")
        if job_handle is None:
            proof = self._wait_closed_job_identity(handle, data, deadline)
            if proof:
                return build_windows_recovery_report(
                    RecoveryState.CONFIRMED_DEAD,
                    handle=handle,
                    proof=proof,
                )
            return build_windows_recovery_report(RecoveryState.UNCONFIRMED, note="KILL_ON_CLOSE 后根进程死亡未确认")
        process_handle = self._open_matching_process(data)
        record = OwnedWindowsJob(
            handle=handle,
            job_handle=job_handle,
            process_handle=process_handle,
            thread_handle=None,
            assigned=True,
        )
        try:
            self._resources.remember(record)
        except ValueError:
            for native_handle in (process_handle, job_handle):
                if native_handle is not None:
                    self._resources.close_handle(native_handle)
            return build_windows_recovery_report(
                RecoveryState.UNCONFIRMED,
                note="Windows Job 恢复资源登记冲突",
            )
        proof = self._wait_record_death(record, self._monotonic_now())
        if proof is not None:
            return build_windows_recovery_report(
                RecoveryState.CONFIRMED_DEAD,
                handle=handle,
                proof=proof,
            )
        return build_windows_recovery_report(RecoveryState.ACTIVE, handle=handle)

    def _wait_open_job_zero(
        self,
        subject: ExecutionHandle | ProcessReference,
        job_handle: int,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        try:
            while True:
                if self._native.query_active_processes(job_handle) == 0:
                    return build_windows_death_proof(
                        subject,
                        confirmed_at=self._wall_now(),
                        evidence="Windows Job ActiveProcesses=0",
                    )
                if self._monotonic_now() >= deadline.expires_at:
                    return None
                self._sleep_until(deadline.expires_at)
        except WindowsJobNativeError:
            return None
        finally:
            self._resources.close_handle(job_handle)

    def _wait_closed_job_identity(
        self,
        subject: ExecutionHandle | ProcessReference,
        data: WindowsJobReferenceData,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        while True:
            state = self._probe_process_identity(data)
            if state == "absent":
                return build_windows_death_proof(
                    subject,
                    confirmed_at=self._wall_now(),
                    evidence="Windows Job 已按 KILL_ON_CLOSE 关闭且根进程不存在",
                )
            if state == "reused":
                return build_windows_death_proof(
                    subject,
                    confirmed_at=self._wall_now(),
                    evidence="Windows Job 已按 KILL_ON_CLOSE 关闭且 PID 已复用",
                )
            if state == "unknown" or self._monotonic_now() >= deadline.expires_at:
                return None
            self._sleep_until(deadline.expires_at)

    def _probe_process_identity(self, data: WindowsJobReferenceData) -> str:
        try:
            process_handle = self._native.open_process(data.pid)
        except WindowsJobNativeError:
            return "unknown"
        if process_handle is None:
            return "absent"
        try:
            marker = self._native.process_birth_marker(process_handle)
            if marker != data.birth_marker:
                return "reused"
            return "absent" if self._native.poll_process(process_handle) is not None else "active"
        except WindowsJobNativeError:
            return "unknown"
        finally:
            self._resources.close_handle(process_handle)

    def _open_matching_process(self, data: WindowsJobReferenceData) -> int | None:
        process_handle: int | None = None
        try:
            process_handle = self._native.open_process(data.pid)
            if process_handle is None:
                return None
            if self._native.process_birth_marker(process_handle) == data.birth_marker:
                matched = process_handle
                process_handle = None
                return matched
        except WindowsJobNativeError:
            return None
        finally:
            if process_handle is not None:
                self._resources.close_handle(process_handle)

    def _raise_partial_start(
        self,
        record: OwnedWindowsJob,
        deadline: Deadline,
        note: str,
        *,
        cause: Exception | None = None,
    ) -> None:
        self._force_record(record)
        proof = self._wait_record_death(record, deadline.expires_at)
        if cause is not None:
            propagate_memory_error(cause)
        raise AttemptProcessStartError(
            handle=record.handle,
            death_proof=proof,
            retryable=proof is not None,
            note=note if proof is None else f"{note}但已确认死亡",
        )

    def _force_record(self, record: OwnedWindowsJob) -> bool:
        had_error = False
        memory_error: MemoryError | None = None
        if record.assigned is not False:
            try:
                self._native.terminate_job(record.job_handle, _TERMINATE_EXIT_CODE)
            except Exception as error:  # noqa: BLE001 - 强制路径需继续尝试根进程终止
                had_error = True
                if isinstance(error, MemoryError):
                    memory_error = error
        if record.assigned is not True and record.process_handle is not None:
            try:
                self._native.terminate_process(record.process_handle, _TERMINATE_EXIT_CODE)
            except Exception as error:  # noqa: BLE001 - 返回歧义状态而非泄漏普通异常
                had_error = True
                if isinstance(error, MemoryError):
                    memory_error = error
        if memory_error is not None:
            raise memory_error
        return had_error

    def _wait_record_death(
        self,
        record: OwnedWindowsJob,
        expires_at: float,
    ) -> ConfirmedProcessDeath | None:
        while True:
            evidence = self._record_death_evidence(record)
            if evidence is not None:
                proof = build_windows_death_proof(
                    record.handle,
                    confirmed_at=self._wall_now(),
                    evidence=evidence,
                )
                self._resources.release(record)
                return proof
            if self._monotonic_now() >= expires_at:
                return None
            self._sleep_until(expires_at)

    def _record_death_evidence(self, record: OwnedWindowsJob) -> str | None:
        try:
            if self._native.query_active_processes(record.job_handle) != 0:
                return None
            if record.assigned is True:
                return "Windows Job ActiveProcesses=0"
            if record.process_handle is None:
                return None
            if self._native.poll_process(record.process_handle) is not None:
                return "挂起根进程已退出且 Windows Job ActiveProcesses=0"
        except Exception as error:  # noqa: BLE001 - 原生探测异常只表示死亡未确认
            propagate_memory_error(error)
            return None
        return None

    def _sleep_until(self, expires_at: float) -> None:
        remaining = expires_at - self._monotonic_now()
        if remaining > 0:
            self._sleeper(min(self._poll_interval_sec, remaining))

    def _monotonic_now(self) -> float:
        return clock_now(self._monotonic_clock, "Windows Job 单调")

    def _wall_now(self) -> float:
        return clock_now(self._wall_clock, "Windows Job epoch")

    @staticmethod
    def _validate_prepare(
        attempt_id: str,
        argv: Sequence[str],
        cwd: Path,
        bootstrap_log: Path,
        deadline: Deadline,
    ) -> tuple[tuple[str, ...], Path, Path]:
        if type(attempt_id) is not str or not attempt_id.strip() or "\x00" in attempt_id:
            raise ValueError("attempt_id 无效")
        if isinstance(argv, (str, bytes)) or not argv:
            raise ValueError("Windows Job argv 无效")
        command = tuple(argv)
        if any(type(item) is not str or not item or "\x00" in item for item in command):
            raise ValueError("Windows Job argv 无效")
        executable = Path(command[0])
        if not executable.is_absolute() or not executable.is_file():
            raise ValueError("Windows Job argv[0] 必须是绝对可信可执行文件")
        workdir = Path(cwd)
        log_path = Path(bootstrap_log)
        if not workdir.is_absolute() or not workdir.is_dir() or not log_path.is_absolute():
            raise ValueError("Windows Job 工作目录或日志路径无效")
        if not isinstance(deadline, Deadline):
            raise ValueError("Windows Job deadline 无效")
        return command, workdir, log_path

    @staticmethod
    def _grace(value: float) -> float:
        if type(value) not in (int, float):
            raise ValueError("Windows Job grace_sec 无效")
        grace = float(value)
        if not math.isfinite(grace) or grace < 0:
            raise ValueError("Windows Job grace_sec 无效")
        return grace

    @staticmethod
    def _prepare_error(
        note: str,
        *,
        retryable: bool = True,
    ) -> AttemptProcessStartError:
        return AttemptProcessStartError(
            handle=None,
            death_proof=None,
            retryable=retryable,
            note=note,
        )

__all__ = ["WINDOWS_JOB_KIND", "WindowsJobAttemptProcessBackend"]
