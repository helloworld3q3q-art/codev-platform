"""cgroup bootstrap 从预检到身份登记的原子准备事务。"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Collection, Sequence
from pathlib import Path

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ExecutionHandle,
)
from codev_platform.reindex.bootstrap_runtime import close_gate
from codev_platform.reindex.cgroup_abort import abort_cgroup_bootstrap
from codev_platform.reindex.cgroup_bootstrap import build_bootstrap_command
from codev_platform.reindex.cgroup_process_state import LiveCgroupProcess
from codev_platform.reindex.cgroup_v2 import (
    CGROUP_CONTAINMENT_KIND,
    CgroupFilesystem,
)
from codev_platform.reindex.posix_process_identity import (
    LinuxProcessInfo,
    LinuxProcessTable,
)
from codev_platform.reindex.process_stdio import open_direct_log


class CgroupBootstrapPreparer:
    """只负责 blocked bootstrap 的创建、纳管证明和失败回收。"""

    def __init__(
        self,
        *,
        filesystem: CgroupFilesystem,
        process_table: LinuxProcessTable,
        poll_interval: float,
        monotonic: Callable[[], float],
        sleeper: Callable[[float], None],
        cleanup_sec: float,
    ) -> None:
        self._filesystem = filesystem
        self._table = process_table
        self._poll_interval = poll_interval
        self._monotonic = monotonic
        self._sleep = sleeper
        self._cleanup_sec = cleanup_sec

    def prepare(
        self,
        *,
        attempt_id: str,
        command: Sequence[str],
        cwd: Path,
        log_path: Path,
        native_ref: str,
        started_at: float,
        deadline: Deadline,
        known_identities: Collection[str],
    ) -> tuple[ExecutionHandle, LiveCgroupProcess]:
        target, environment = self._preflight(native_ref, deadline)
        read_fd, write_fd = self._pipe(native_ref, deadline)
        log = None
        try:
            bootstrap = build_bootstrap_command(
                command,
                target,
                read_fd,
                os.getpid(),
            )
            log = open_direct_log(log_path)
        except BaseException as exc:
            close_gate(read_fd)
            close_gate(write_fd)
            self.abort(None, native_ref, deadline)
            self._raise_failure(exc, "cgroup bootstrap 资源准备失败")
        try:
            process = subprocess.Popen(  # noqa: S603 - 绝对本地 bootstrap 与命令
                bootstrap,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                close_fds=True,
                pass_fds=(read_fd,),
                start_new_session=True,
                env=environment,
            )
        except BaseException as exc:
            close_gate(write_fd)
            self.abort(None, native_ref, deadline)
            self._close_parent_resources(read_fd, log)
            self._raise_failure(exc, "cgroup bootstrap 启动失败")
        self._close_parent_resources(read_fd, log)
        return self._register(
            process=process,
            write_fd=write_fd,
            attempt_id=attempt_id,
            native_ref=native_ref,
            started_at=started_at,
            deadline=deadline,
            known_identities=known_identities,
        )

    def _preflight(self, native_ref: str, deadline: Deadline) -> tuple[Path, dict[str, str]]:
        try:
            target = self._filesystem.path_for(native_ref)
            environment = dict(os.environ)
            environment["CODEV_REINDEX_CGROUP_ROOT"] = str(self._filesystem.root)
            return target, environment
        except BaseException as exc:
            self.abort(None, native_ref, deadline)
            self._raise_failure(exc, "cgroup bootstrap 环境预检失败")

    def _pipe(self, native_ref: str, deadline: Deadline) -> tuple[int, int]:
        try:
            return os.pipe()
        except BaseException as exc:
            self.abort(None, native_ref, deadline)
            self._raise_failure(exc, "cgroup bootstrap 控制管道创建失败")

    def _register(
        self,
        *,
        process: subprocess.Popen,
        write_fd: int,
        attempt_id: str,
        native_ref: str,
        started_at: float,
        deadline: Deadline,
        known_identities: Collection[str],
    ) -> tuple[ExecutionHandle, LiveCgroupProcess]:
        handle = None
        try:
            info = self.wait_assignment(process, native_ref, deadline)
            if info is None:
                raise ValueError("cgroup bootstrap 分配或身份验证失败")
            identity = self._table.build_identity(info, native_ref)
            handle = ExecutionHandle(
                attempt_id,
                process.pid,
                identity,
                CGROUP_CONTAINMENT_KIND,
                native_ref,
                started_at,
            )
            if identity in known_identities:
                raise ValueError("cgroup 进程身份登记冲突")
        except BaseException as exc:
            close_gate(write_fd)
            self.abort(process, native_ref, deadline)
            self._raise_failure(exc, "cgroup 进程身份登记失败", handle=handle)
        return handle, LiveCgroupProcess(process, write_fd)

    def wait_assignment(
        self,
        process: subprocess.Popen,
        native_ref: str,
        deadline: Deadline,
    ) -> LinuxProcessInfo | None:
        end = deadline.expires_at - self._cleanup_sec
        while self._monotonic() < end:
            try:
                assigned = self._filesystem.contains_pid(native_ref, process.pid)
                info = self._table.read(process.pid)
            except (OSError, ValueError):
                assigned, info = False, None
            if assigned and info is not None:
                return info
            if process.poll() is not None:
                return None
            remaining = end - self._monotonic()
            if remaining <= 0:
                break
            self._sleep(min(self._poll_interval, remaining))
        return None

    def abort(
        self,
        process: subprocess.Popen | None,
        native_ref: str,
        deadline: Deadline,
    ) -> None:
        abort_cgroup_bootstrap(
            filesystem=self._filesystem,
            native_ref=native_ref,
            process=process,
            deadline=deadline,
            populated=self._safe_populated,
            cleanup_sec=self._cleanup_sec,
        )

    def _safe_populated(self, native_ref: str) -> bool | None:
        try:
            return self._filesystem.populated(native_ref)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _close_parent_resources(read_fd: int, log) -> None:
        close_gate(read_fd)
        try:
            log.close()
        except BaseException:
            pass

    @staticmethod
    def _raise_failure(
        exc: BaseException,
        note: str,
        *,
        handle: ExecutionHandle | None = None,
    ) -> None:
        if not isinstance(exc, Exception):
            raise exc
        raise AttemptProcessStartError(
            handle=handle,
            death_proof=None,
            retryable=handle is None,
            note=note,
        ) from None


__all__ = ["CgroupBootstrapPreparer"]
