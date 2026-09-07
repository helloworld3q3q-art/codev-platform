"""systemd delegated cgroup v2 的逐 attempt 进程封装。"""

from __future__ import annotations

import math
import signal
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ExecutionHandle,
    ProcessBackendReadinessError,
    ProcessReference,
    RecoveryReport,
    RecoveryState,
    TerminationReport,
    execution_handle_from_journal,
    handle_epoch_time,
    validate_process_identity,
)
from codev_platform.reindex.attempts import (
    AttemptJournalEntry,
    ConfirmedProcessDeath,
)
from codev_platform.reindex.bootstrap_runtime import (
    clock_now,
    close_gate,
    positive_finite,
    release_gate,
    validate_attempt_id,
    validate_command,
)
from codev_platform.reindex.cgroup_v2 import (
    CGROUP_CONTAINMENT_KIND,
    CgroupDelegationError,
    CgroupFilesystem,
    SystemdCgroupV2,
    encode_cgroup_native_ref,
    parse_cgroup_events,
    parse_cgroup_native_ref,
)
from codev_platform.reindex.cgroup_recovery import recover_unjournaled_attempt
from codev_platform.reindex.cgroup_process_state import LiveCgroupProcess
from codev_platform.reindex.cgroup_spawn import CgroupBootstrapPreparer
from codev_platform.reindex.cgroup_termination import build_cgroup_termination_report
from codev_platform.reindex.posix_process_identity import (
    LinuxProcessTable,
    linux_birth_marker,
)

_START_CLEANUP_SEC = 0.25


class CgroupAttemptProcessBackend:
    """用 delegated cgroup v2 封装并证明 attempt 全子树死亡。"""

    def __init__(
        self,
        *,
        filesystem: CgroupFilesystem | None = None,
        process_table: LinuxProcessTable | None = None,
        poll_interval: float = 0.02,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not all(callable(item) for item in (monotonic, wall_clock, sleeper)):
            raise ValueError("cgroup 时钟与 sleeper 必须可调用")
        self._table = process_table or LinuxProcessTable()
        self.filesystem = filesystem or SystemdCgroupV2(boot_id=self._table.boot_id)
        if self.filesystem.boot_id != self._table.boot_id:
            raise ValueError("cgroup 与进程表 boot_id 不一致")
        self._poll_interval = positive_finite(poll_interval, "poll_interval")
        self._monotonic = monotonic
        self._wall = wall_clock
        self._sleep = sleeper
        self._live: dict[str, LiveCgroupProcess] = {}
        self._preparer = CgroupBootstrapPreparer(
            filesystem=self.filesystem,
            process_table=self._table,
            poll_interval=self._poll_interval,
            monotonic=self._monotonic_now,
            sleeper=self._sleep,
            cleanup_sec=_START_CLEANUP_SEC,
        )

    def assert_ready(self, deadline: Deadline) -> None:
        """委托文件系统证明 delegation 与强杀控制面均可用。"""
        if not isinstance(deadline, Deadline):
            raise ValueError("cgroup readiness deadline 无效")
        self._require_readiness_budget(deadline)
        try:
            self.filesystem.assert_ready(deadline)
        except MemoryError:
            raise
        except Exception as exc:
            raise ProcessBackendReadinessError("cgroup readiness 探针失败") from exc
        self._require_readiness_budget(deadline)

    def _require_readiness_budget(self, deadline: Deadline) -> None:
        if deadline.expired(now=self._monotonic_now()):
            raise ProcessBackendReadinessError("cgroup readiness 预算已耗尽")

    def prepare(
        self,
        *,
        attempt_id: str,
        argv: Sequence[str],
        cwd: Path,
        bootstrap_log: Path,
        deadline: Deadline,
    ) -> ExecutionHandle:
        validate_attempt_id(attempt_id)
        command = validate_command(argv)
        if deadline.remaining(now=self._monotonic_now()) <= _START_CLEANUP_SEC:
            raise AttemptProcessStartError(
                handle=None, death_proof=None, retryable=True, note="启动预算已耗尽"
            )
        started_at = self._wall_now()
        native_ref = encode_cgroup_native_ref(self._table.boot_id, attempt_id)
        try:
            created_ref = self.filesystem.create_attempt(attempt_id)
            if created_ref != native_ref:
                raise CgroupDelegationError("attempt cgroup 引用不一致")
        except BaseException as exc:
            self._preparer.abort(None, native_ref, deadline)
            if not isinstance(exc, Exception):
                raise
            raise AttemptProcessStartError(
                handle=None,
                death_proof=None,
                retryable=False,
                note="attempt cgroup 创建失败",
            ) from None
        handle, state = self._preparer.prepare(
            attempt_id=attempt_id,
            command=command,
            cwd=Path(cwd),
            log_path=Path(bootstrap_log),
            native_ref=native_ref,
            started_at=started_at,
            deadline=deadline,
            known_identities=self._live,
        )
        self._live[handle.process_identity] = state
        return handle

    def activate(self, handle: ExecutionHandle, deadline: Deadline) -> None:
        """journal 持久化后才放行已进入 attempt cgroup 的目标。"""
        state = self._state(handle)
        if state.activation_fd is None:
            return
        if deadline.expired(now=self._monotonic_now()):
            self._fail_activation(handle, deadline, "cgroup 激活预算已耗尽")
        descriptor = state.activation_fd
        state.activation_fd = None
        if not release_gate(descriptor):
            self._fail_activation(handle, deadline, "cgroup bootstrap 放行失败")

    def _state(self, handle: ExecutionHandle) -> LiveCgroupProcess:
        self._validate_handle(handle)
        state = self._live.get(handle.process_identity)
        if not isinstance(state, LiveCgroupProcess):
            raise ValueError("cgroup handle 不属于当前后端实例")
        return state

    def _fail_activation(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
        note: str,
    ) -> None:
        self._close_activation(self._state(handle))
        report = self.terminate(handle, grace_sec=0.0, deadline=deadline)
        raise AttemptProcessStartError(
            handle=handle,
            death_proof=report.death_proof,
            retryable=report.death_proof is not None,
            note=note,
        )

    @staticmethod
    def _close_activation(state: LiveCgroupProcess) -> None:
        descriptor = state.activation_fd
        state.activation_fd = None
        if descriptor is None:
            return
        close_gate(descriptor)

    def _monotonic_now(self) -> float:
        return clock_now(self._monotonic, "cgroup 单调")

    def _wall_now(self) -> float:
        return clock_now(self._wall, "cgroup epoch")

    def _remove_empty(self, native_ref: str) -> None:
        try:
            self.filesystem.remove_empty(native_ref)
        except (AttributeError, OSError, ValueError):
            return

    def poll(self, handle: ExecutionHandle) -> int | None:
        self._validate_handle(handle)
        state = self._live.get(handle.process_identity)
        if isinstance(state, LiveCgroupProcess) and state.process is not None:
            return state.process.poll()
        if self._safe_exists(handle.native_ref) is False:
            return 1
        populated = self._safe_populated(handle.native_ref)
        return None if populated is not False else 1

    def _validate_handle(self, handle: ExecutionHandle) -> tuple[str, str]:
        if not isinstance(handle, ExecutionHandle):
            raise ValueError("cgroup handle 类型无效")
        if handle.containment_kind != CGROUP_CONTAINMENT_KIND:
            raise ValueError("containment kind 不匹配")
        boot_id, name = parse_cgroup_native_ref(handle.native_ref)
        if handle.native_ref != encode_cgroup_native_ref(boot_id, handle.attempt_id):
            raise ValueError("attempt 与 cgroup 路径不匹配")
        validate_process_identity(
            handle.process_identity,
            pid=handle.pid,
            native_ref=handle.native_ref,
        )
        return boot_id, name

    def _safe_populated(self, native_ref: str) -> bool | None:
        try:
            return self.filesystem.populated(native_ref)
        except (OSError, ValueError):
            return None

    def _safe_exists(self, native_ref: str) -> bool | None:
        try:
            return self.filesystem.exists(native_ref)
        except (OSError, ValueError):
            return None

    def _wait_empty(self, native_ref: str, expires_at: float) -> bool:
        while True:
            populated = self._safe_populated(native_ref)
            if populated is False:
                return True
            if populated is None:
                return False
            remaining = expires_at - self._monotonic_now()
            if remaining <= 0:
                return False
            self._sleep(min(self._poll_interval, remaining))

    def _term_root(self, handle: ExecutionHandle) -> None:
        try:
            if not self.filesystem.contains_pid(handle.native_ref, handle.pid):
                return
            info = self._table.read(handle.pid)
            if info is None:
                return
            validate_process_identity(
                handle.process_identity,
                pid=handle.pid,
                native_ref=handle.native_ref,
                birth_marker=linux_birth_marker(self._table.boot_id, info.start_ticks),
            )
        except (OSError, ValueError):
            return
        self._table.signal_same(info, signal.SIGTERM)

    def _proof(self, handle: ExecutionHandle, evidence: str) -> ConfirmedProcessDeath:
        return ConfirmedProcessDeath(
            handle.process_identity,
            CGROUP_CONTAINMENT_KIND,
            handle_epoch_time(handle, self._wall_now()),
            evidence,
        )

    def terminate(
        self,
        handle: ExecutionHandle,
        *,
        grace_sec: float,
        deadline: Deadline,
    ) -> TerminationReport:
        if type(grace_sec) not in (int, float) or not math.isfinite(grace_sec) or grace_sec < 0:
            raise ValueError("grace_sec 必须是有限非负数")
        boot_id, _name = self._validate_handle(handle)
        requested = handle_epoch_time(handle, self._wall_now())
        state = self._live.get(handle.process_identity)
        if isinstance(state, LiveCgroupProcess):
            self._close_activation(state)
        if boot_id != self._table.boot_id:
            return self._report(
                handle,
                requested,
                graceful=True,
                forced=False,
                confirmed=True,
                evidence="Linux boot_id 已变化",
            )
        if self._safe_exists(handle.native_ref) is False:
            return self._report(
                handle,
                requested,
                graceful=True,
                forced=False,
                confirmed=True,
                evidence="同启动周期 cgroup 路径已消失",
            )
        grace_end = min(deadline.expires_at, self._monotonic_now() + grace_sec)
        self._term_root(handle)
        if self._wait_empty(handle.native_ref, grace_end):
            return self._report(
                handle,
                requested,
                graceful=True,
                forced=False,
                confirmed=True,
                evidence="cgroup.events 已确认 populated=0",
            )
        try:
            self.filesystem.kill(handle.native_ref)
        except (OSError, ValueError):
            pass
        confirmed = self._wait_empty(handle.native_ref, deadline.expires_at)
        return self._report(
            handle,
            requested,
            graceful=False,
            forced=True,
            confirmed=confirmed,
            evidence="cgroup.events 已确认 populated=0",
        )

    def _report(
        self,
        handle: ExecutionHandle,
        requested: float,
        *,
        graceful: bool,
        forced: bool,
        confirmed: bool,
        evidence: str,
    ) -> TerminationReport:
        finished = max(requested, self._wall_now())
        report = build_cgroup_termination_report(
            handle=handle,
            requested_at=requested,
            finished_at=finished,
            graceful=graceful,
            forced=forced,
            confirmed=confirmed,
            evidence=evidence,
        )
        if report.death_proof is not None:
            self._remove_empty(handle.native_ref)
            self._live.pop(handle.process_identity, None)
        return report

    def recover(
        self,
        journal: AttemptJournalEntry,
        deadline: Deadline,
    ) -> RecoveryReport:
        try:
            handle = execution_handle_from_journal(journal)
        except ValueError:
            return RecoveryReport(RecoveryState.UNCONFIRMED, None, None, "journal 进程引用不完整")
        if handle is None:
            return recover_unjournaled_attempt(
                filesystem=self.filesystem,
                boot_id=self._table.boot_id,
                attempt_id=journal.attempt_id,
                deadline=deadline,
                wait_empty=self._wait_empty,
            )
        return self.recover_handle(handle, deadline)

    def recover_handle(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> RecoveryReport:
        del deadline
        try:
            boot_id, _name = self._validate_handle(handle)
        except ValueError:
            return RecoveryReport(RecoveryState.UNCONFIRMED, None, None, "cgroup 句柄身份有歧义")
        if boot_id != self._table.boot_id:
            proof = self._proof(handle, "Linux boot_id 已变化")
            return RecoveryReport(RecoveryState.CONFIRMED_DEAD, handle, proof, "旧启动周期已结束")
        try:
            exists = self.filesystem.exists(handle.native_ref)
        except (OSError, ValueError):
            return RecoveryReport(
                RecoveryState.UNCONFIRMED, None, None, "cgroup 路径存在性无法验证"
            )
        if not exists:
            proof = self._proof(handle, "同启动周期 cgroup 路径已消失")
            return RecoveryReport(
                RecoveryState.CONFIRMED_DEAD, handle, proof, "attempt cgroup 已清理"
            )
        populated = self._safe_populated(handle.native_ref)
        if populated is True:
            self._live[handle.process_identity] = LiveCgroupProcess(
                process=None,
                activation_fd=None,
            )
            return RecoveryReport(RecoveryState.ACTIVE, handle, None, "attempt cgroup 仍有进程")
        if populated is False:
            proof = self._proof(handle, "cgroup.events 已确认 populated=0")
            self._remove_empty(handle.native_ref)
            return RecoveryReport(
                RecoveryState.CONFIRMED_DEAD, handle, proof, "attempt cgroup 已空"
            )
        return RecoveryReport(RecoveryState.UNCONFIRMED, None, None, "cgroup.events 无法验证")

    def confirm_dead(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        boot_id, _name = self._validate_handle(handle)
        if boot_id != self._table.boot_id:
            proof = self._proof(handle, "Linux boot_id 已变化")
            self._live.pop(handle.process_identity, None)
            return proof
        if self._safe_exists(handle.native_ref) is False:
            proof = self._proof(handle, "同启动周期 cgroup 路径已消失")
            self._live.pop(handle.process_identity, None)
            return proof
        if self._wait_empty(handle.native_ref, deadline.expires_at):
            proof = self._proof(handle, "cgroup.events 已确认 populated=0")
            self._remove_empty(handle.native_ref)
            self._live.pop(handle.process_identity, None)
            return proof
        return None

    def confirm_reference_dead(
        self,
        reference: ProcessReference,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        if reference.containment_kind != CGROUP_CONTAINMENT_KIND:
            return None
        try:
            boot_id, _name = parse_cgroup_native_ref(reference.native_ref)
            validate_process_identity(
                reference.process_identity,
                native_ref=reference.native_ref,
            )
        except ValueError:
            return None
        if boot_id != self._table.boot_id:
            return ConfirmedProcessDeath(
                reference.process_identity,
                CGROUP_CONTAINMENT_KIND,
                self._wall_now(),
                "Linux boot_id 已变化",
            )
        exists = self._safe_exists(reference.native_ref)
        if exists is False:
            return ConfirmedProcessDeath(
                reference.process_identity,
                CGROUP_CONTAINMENT_KIND,
                self._wall_now(),
                "同启动周期 cgroup 路径已消失",
            )
        if exists is None:
            return None
        if self._wait_empty(reference.native_ref, deadline.expires_at):
            proof = ConfirmedProcessDeath(
                reference.process_identity,
                CGROUP_CONTAINMENT_KIND,
                self._wall_now(),
                "cgroup.events 已确认 populated=0",
            )
            self._remove_empty(reference.native_ref)
            return proof
        return None


__all__ = [
    "CGROUP_CONTAINMENT_KIND",
    "CgroupAttemptProcessBackend",
    "CgroupDelegationError",
    "SystemdCgroupV2",
    "encode_cgroup_native_ref",
    "parse_cgroup_events",
    "parse_cgroup_native_ref",
]
