"""Linux POSIX 进程组隔离、身份复核与失败关闭恢复。"""

from __future__ import annotations

import os
import math
import signal
import subprocess
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
from codev_platform.reindex.posix_process_identity import (
    LinuxProcessInfo,
    LinuxProcessScan,
    LinuxProcessTable,
    encode_posix_native_ref,
    linux_birth_marker,
    parse_linux_stat,
    parse_posix_native_ref,
)
from codev_platform.reindex.posix_process_state import LivePosixProcess
from codev_platform.reindex.posix_bootstrap import build_bootstrap_command
from codev_platform.reindex.process_stdio import open_direct_log

POSIX_CONTAINMENT_KIND = "posix_process_group"
_START_CLEANUP_SEC = 0.25


class PosixAttemptProcessBackend:
    """仅供 DEV/cooperative 诊断；生产不得选择，activate 后不签严格死亡证明。"""

    def __init__(
        self,
        *,
        process_table: LinuxProcessTable | None = None,
        poll_interval: float = 0.02,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not all(callable(item) for item in (monotonic, wall_clock, sleeper)):
            raise ValueError("POSIX 时钟与 sleeper 必须可调用")
        self._table = process_table or LinuxProcessTable()
        self._poll_interval = positive_finite(poll_interval, "poll_interval")
        self._monotonic = monotonic
        self._wall = wall_clock
        self._sleep = sleeper
        self._live: dict[str, LivePosixProcess] = {}

    @staticmethod
    def assert_ready(deadline: Deadline) -> None:
        if not isinstance(deadline, Deadline):
            raise ValueError("POSIX readiness deadline 无效")
        raise ProcessBackendReadinessError("POSIX 进程组不具备生产 containment readiness")

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
        try:
            read_fd, write_fd = os.pipe()
        except BaseException as exc:
            if not isinstance(exc, Exception):
                raise
            raise AttemptProcessStartError(
                handle=None,
                death_proof=None,
                retryable=True,
                note="POSIX bootstrap 控制管道创建失败",
            ) from None
        log = None
        try:
            bootstrap_command = build_bootstrap_command(command, read_fd, os.getpid())
            log = open_direct_log(bootstrap_log)
        except BaseException as exc:
            close_gate(read_fd)
            close_gate(write_fd)
            if log is not None:
                try:
                    log.close()
                except BaseException:
                    pass
            if not isinstance(exc, Exception):
                raise
            raise AttemptProcessStartError(
                handle=None,
                death_proof=None,
                retryable=True,
                note="POSIX bootstrap 日志或命令资源准备失败",
            ) from None
        try:
            process = subprocess.Popen(  # noqa: S603 - argv 是已验证的绝对本地命令
                bootstrap_command,
                cwd=Path(cwd),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                pass_fds=(read_fd,),
                start_new_session=True,
            )
        except BaseException as exc:
            close_gate(write_fd)
            if not isinstance(exc, Exception):
                raise
            raise AttemptProcessStartError(
                handle=None, death_proof=None, retryable=True, note="POSIX 进程启动失败"
            ) from None
        finally:
            close_gate(read_fd)
            try:
                log.close()
            except BaseException:
                pass
        handle = None
        try:
            info = self._read_started_process(process.pid, deadline)
            if info is None or info.pgid != process.pid or info.session_id != process.pid:
                raise ValueError("POSIX session 建立证明失败")
            native_ref = encode_posix_native_ref(self._table.boot_id, info.pgid, info.session_id)
            identity = self._table.build_identity(info, native_ref)
            handle = ExecutionHandle(
                attempt_id,
                process.pid,
                identity,
                POSIX_CONTAINMENT_KIND,
                native_ref,
                started_at,
            )
            if identity in self._live:
                raise ValueError("POSIX 进程身份登记冲突")
        except BaseException as exc:  # spawn 后任何失败都必须事务回收
            close_gate(write_fd)
            self._stop_blocked_bootstrap(process, deadline)
            if not isinstance(exc, Exception):
                raise
            raise AttemptProcessStartError(
                handle=handle,
                death_proof=None,
                retryable=False,
                note="POSIX 进程身份登记失败",
            ) from None
        self._live[identity] = LivePosixProcess(
            process=process,
            root=info,
            activation_fd=write_fd,
        )
        return handle

    def activate(self, handle: ExecutionHandle, deadline: Deadline) -> None:
        """journal 持久化后才放行目标；失败时关闭启动门并回收。"""
        state = self._state(handle)
        if state.activation_fd is None:
            return
        if deadline.expired(now=self._monotonic_now()):
            self._fail_activation(handle, deadline, "POSIX 激活预算已耗尽")
        descriptor = state.activation_fd
        state.activation_fd = None
        state.uncertain = True
        if not release_gate(descriptor):
            self._fail_activation(handle, deadline, "POSIX 启动门放行失败")
        self._observe(handle)

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
    def _close_activation(state: LivePosixProcess) -> None:
        descriptor = state.activation_fd
        state.activation_fd = None
        if descriptor is None:
            return
        close_gate(descriptor)

    def _monotonic_now(self) -> float:
        return clock_now(self._monotonic, "POSIX 单调")

    def _wall_now(self) -> float:
        return clock_now(self._wall, "POSIX epoch")

    def _read_started_process(
        self,
        pid: int,
        deadline: Deadline,
    ) -> LinuxProcessInfo | None:
        identity_deadline = deadline.expires_at - _START_CLEANUP_SEC
        while self._monotonic_now() < identity_deadline:
            try:
                info = self._table.read(pid)
            except (OSError, ValueError):
                info = None
            if info is not None:
                return info
            remaining = identity_deadline - self._monotonic_now()
            if remaining <= 0:
                break
            self._sleep(min(self._poll_interval, remaining))
        return None

    def _stop_blocked_bootstrap(
        self,
        process: subprocess.Popen,
        deadline: Deadline,
    ) -> None:
        cleanup_expires = time.monotonic() + min(_START_CLEANUP_SEC, deadline.remaining())
        first_wait = min(0.05, max(0.0, cleanup_expires - time.monotonic()))
        try:
            process.wait(timeout=first_wait)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
        remaining = max(0.0, cleanup_expires - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except (OSError, subprocess.TimeoutExpired):
            return

    def poll(self, handle: ExecutionHandle) -> int | None:
        state = self._state(handle)
        if state.process is not None:
            self._observe(handle)
            return state.process.poll()
        return 1 if self._tree_dead(handle) else None

    def _state(self, handle: ExecutionHandle) -> LivePosixProcess:
        self._validate_handle(handle)
        state = self._live.get(handle.process_identity)
        if state is None:
            raise ValueError("POSIX handle 不属于当前后端实例")
        return state

    def _validate_handle(self, handle: ExecutionHandle) -> tuple[str, int, int]:
        if not isinstance(handle, ExecutionHandle):
            raise ValueError("POSIX handle 类型无效")
        if handle.containment_kind != POSIX_CONTAINMENT_KIND:
            raise ValueError("containment kind 不匹配")
        boot_id, pgid, session_id = parse_posix_native_ref(handle.native_ref)
        if pgid != handle.pid or session_id != handle.pid:
            raise ValueError("POSIX handle 与原生引用不匹配")
        validate_process_identity(
            handle.process_identity,
            pid=handle.pid,
            native_ref=handle.native_ref,
        )
        return boot_id, pgid, session_id

    def _observe(self, handle: ExecutionHandle) -> LinuxProcessScan:
        state = self._state_without_observe(handle)
        scan = self._table.scan()
        if not scan.complete:
            state.uncertain = True
        by_parent: dict[int, list[LinuxProcessInfo]] = {}
        for info in scan.processes:
            by_parent.setdefault(info.ppid, []).append(info)
        roots = [state.root.pid, *state.observed]
        descendants: dict[int, LinuxProcessInfo] = {}
        stack = list(dict.fromkeys(roots))
        while stack and len(descendants) <= self._table.max_processes:
            parent = stack.pop()
            for child in by_parent.get(parent, ()):
                if child.pid in descendants or child.pid == state.root.pid:
                    continue
                descendants[child.pid] = child
                stack.append(child.pid)
        if stack:
            state.uncertain = True
        for info in descendants.values():
            previous = state.observed.get(info.pid)
            if previous is None or previous.start_ticks == info.start_ticks:
                state.observed[info.pid] = info
            if info.pgid != handle.pid or info.session_id != handle.pid:
                state.escaped = True
        return scan

    def _state_without_observe(self, handle: ExecutionHandle) -> LivePosixProcess:
        self._validate_handle(handle)
        state = self._live.get(handle.process_identity)
        if state is None:
            raise ValueError("POSIX handle 不属于当前后端实例")
        return state

    def _tree_dead(self, handle: ExecutionHandle) -> bool:
        state = self._state_without_observe(handle)
        if state.process is not None:
            state.process.poll()
        scan = self._observe(handle)
        for info in scan.processes:
            if not info.live:
                continue
            if info.pid == state.root.pid and info.start_ticks == state.root.start_ticks:
                return False
            if info.pgid == handle.pid or info.session_id == handle.pid:
                return False
            previous = state.observed.get(info.pid)
            if previous is not None and previous.start_ticks == info.start_ticks:
                return False
        return True

    def _signal_group(self, handle: ExecutionHandle, sig: int) -> bool:
        state = self._state_without_observe(handle)
        scan = self._observe(handle)
        members = tuple(
            info
            for info in scan.processes
            if info.live and (info.pgid == handle.pid or info.session_id == handle.pid)
        )
        if not scan.complete or not self._known_group_members(state, members):
            state.uncertain = True
            return False
        if not members:
            return True
        try:
            os.killpg(handle.pid, sig)
            return True
        except ProcessLookupError:
            return True
        except OSError:
            state.uncertain = True
            return False

    @staticmethod
    def _known_group_members(
        state: LivePosixProcess,
        members: tuple[LinuxProcessInfo, ...],
    ) -> bool:
        for info in members:
            if info.pid == state.root.pid and info.start_ticks == state.root.start_ticks:
                continue
            observed = state.observed.get(info.pid)
            if observed is None or observed.start_ticks != info.start_ticks:
                return False
        return True

    def _kill_escaped(self, handle: ExecutionHandle) -> None:
        state = self._state_without_observe(handle)
        for info in tuple(state.observed.values()):
            if info.pgid != handle.pid or info.session_id != handle.pid:
                self._table.signal_same(info, signal.SIGKILL)

    def _wait_dead(self, handle: ExecutionHandle, expires_at: float) -> bool:
        while True:
            if self._tree_dead(handle):
                return True
            remaining = expires_at - self._monotonic_now()
            if remaining <= 0:
                return False
            self._sleep(min(self._poll_interval, remaining))

    def _proof(self, handle: ExecutionHandle, evidence: str) -> ConfirmedProcessDeath:
        return ConfirmedProcessDeath(
            handle.process_identity,
            POSIX_CONTAINMENT_KIND,
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
        requested = handle_epoch_time(handle, self._wall_now())
        state = self._state(handle)
        self._close_activation(state)
        if self._tree_dead(handle):
            confirmed = not state.escaped and not state.uncertain
            return self._report(
                handle,
                requested,
                graceful=confirmed,
                forced=False,
                confirmed=confirmed,
            )
        grace_end = min(deadline.expires_at, self._monotonic_now() + grace_sec)
        self._signal_group(handle, signal.SIGTERM)
        if self._wait_dead(handle, grace_end):
            confirmed = not state.escaped and not state.uncertain
            return self._report(
                handle,
                requested,
                graceful=confirmed,
                forced=False,
                confirmed=confirmed,
            )
        self._signal_group(handle, signal.SIGKILL)
        self._kill_escaped(handle)
        dead = self._wait_dead(handle, deadline.expires_at)
        confirmed = dead and not state.escaped and not state.uncertain
        return self._report(
            handle,
            requested,
            graceful=False,
            forced=True,
            confirmed=confirmed,
        )

    def _report(
        self,
        handle: ExecutionHandle,
        requested: float,
        *,
        graceful: bool,
        forced: bool,
        confirmed: bool = True,
    ) -> TerminationReport:
        finished = max(requested, self._wall_now())
        proof = None
        if confirmed:
            proof = ConfirmedProcessDeath(
                handle.process_identity,
                POSIX_CONTAINMENT_KIND,
                finished,
                "目标未放行且 blocked bootstrap 进程组已空",
            )
        note = "POSIX 进程树死亡已确认" if proof else "POSIX 进程树死亡无法确认"
        report = TerminationReport(
            requested, finished, graceful, forced, proof is not None, proof, note
        )
        if proof is not None:
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
            return RecoveryReport(
                RecoveryState.NEVER_STARTED,
                None,
                None,
                "两阶段启动尚未登记句柄，目标不可能已放行",
            )
        return self.recover_handle(handle, deadline)

    def recover_handle(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> RecoveryReport:
        del deadline
        try:
            boot_id, pgid, session_id = self._validate_handle(handle)
        except ValueError:
            return RecoveryReport(RecoveryState.UNCONFIRMED, None, None, "POSIX 句柄身份有歧义")
        if boot_id != self._table.boot_id:
            proof = self._proof(handle, "Linux boot_id 已变化")
            return RecoveryReport(
                RecoveryState.CONFIRMED_DEAD,
                handle,
                proof,
                "旧启动周期已结束",
            )
        try:
            root = self._table.read(handle.pid)
            if root is None or not root.live or root.pgid != pgid or root.session_id != session_id:
                raise ValueError("POSIX 根进程已变化")
            validate_process_identity(
                handle.process_identity,
                pid=handle.pid,
                native_ref=handle.native_ref,
                birth_marker=linux_birth_marker(boot_id, root.start_ticks),
            )
        except (OSError, ValueError):
            return RecoveryReport(
                RecoveryState.UNCONFIRMED,
                None,
                None,
                "同一启动周期无法复核 POSIX 根进程身份",
            )
        self._live[handle.process_identity] = LivePosixProcess(
            process=None,
            root=root,
            activation_fd=None,
            uncertain=True,
        )
        return RecoveryReport(RecoveryState.ACTIVE, handle, None, "POSIX 根进程仍存活")

    def confirm_dead(
        self,
        handle: ExecutionHandle,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        state = self._state(handle)
        dead = self._wait_dead(handle, deadline.expires_at)
        if dead and not state.escaped and not state.uncertain:
            proof = self._proof(handle, "目标未放行且 blocked bootstrap 进程组已空")
            self._live.pop(handle.process_identity, None)
            return proof
        return None

    def confirm_reference_dead(
        self,
        reference: ProcessReference,
        deadline: Deadline,
    ) -> ConfirmedProcessDeath | None:
        del deadline
        if reference.containment_kind != POSIX_CONTAINMENT_KIND:
            return None
        try:
            boot_id, _pgid, _sid = parse_posix_native_ref(reference.native_ref)
            pid = validate_process_identity(
                reference.process_identity,
                native_ref=reference.native_ref,
            )
        except ValueError:
            return None
        if boot_id == self._table.boot_id:
            return None
        return ConfirmedProcessDeath(
            reference.process_identity,
            POSIX_CONTAINMENT_KIND,
            self._wall_now(),
            f"Linux boot_id 已变化，旧 PID {pid} 所属启动周期已结束",
        )


__all__ = [
    "LinuxProcessInfo",
    "LinuxProcessTable",
    "POSIX_CONTAINMENT_KIND",
    "PosixAttemptProcessBackend",
    "encode_posix_native_ref",
    "linux_birth_marker",
    "parse_linux_stat",
    "parse_posix_native_ref",
]
