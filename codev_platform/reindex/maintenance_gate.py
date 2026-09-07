"""reindex 维护窗口的全局启动门禁。"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from pathlib import Path

from .file_durability import durable_unlink, durable_write_replace, fsync_directory
from .maintenance_gate_contract import (
    MaintenanceGateError,
    MaintenanceGateLockBusyError,
    _MaintenanceLockUnavailable,
)
from .maintenance_gate_locking import (
    GlobalLockFile,
    open_global_named_lock as _open_global_named_lock_backend,
    open_linux_lock as _open_linux_lock,
    prepare_local_linux_lock,
    provision_global_locks,
    release_linux_lock as _release_linux_lock,
    same_file as _lock_same_file,
)
from .maintenance_gate_restore import (
    arm_restore_standby,
    claim_restore_standby,
    complete_restore_standby,
    complete_restore_standby_while_systemd_transition_locked,
    maintenance_admin_window_permit,
    maintenance_restore_standby_permit,
    read_maintenance_gate_record,
    renew_claimed_restore_standby,
    renew_claimed_restore_standby_while_systemd_transition_locked,
    wait_for_restore_standby_release,
)

_GLOBAL_GATE_ROOT = Path("/var/lib")
_DEFAULT_MARKER_PATH = _GLOBAL_GATE_ROOT / "codev-platform" / "reindex-maintenance.gate"
_LOCK_PAYLOAD = b"codev-platform-reindex-maintenance-lock-v1\n"
_TRANSITION_INTENT_LOCK_PAYLOAD = b"codev-platform-systemd-transition-intent-v1\n"
_TRANSITION_SESSION_LOCK_PAYLOAD = b"codev-platform-systemd-transition-session-v1\n"
_MARKER_MODE = 0o644
_MAX_MARKER_BYTES = 256
_MAX_RESTORE_STANDBY_SEC = 120.0
_TRANSITION_GUARD_SECRET = object()
_TRANSITION_INTENT_GUARD_SECRET = object()
_TRANSITION_SESSION_GUARD_SECRET = object()
_same_file = _lock_same_file


class _SystemdTransitionGuard:
    """只由真实 gate 临界区创建的进程内能力令牌。"""

    __slots__ = ("_active", "_secret")

    def __init__(self, secret: object) -> None:
        self._secret = secret
        self._active = True

    def invalidate(self) -> None:
        """先于 ContextVar 复位失效，连带废止所有已复制上下文。"""
        self._active = False


_ACTIVE_TRANSITION_GUARD: ContextVar[_SystemdTransitionGuard | None] = ContextVar(
    "codev_systemd_transition_guard",
    default=None,
)
_ACTIVE_TRANSITION_INTENT_GUARD: ContextVar[_SystemdTransitionGuard | None] = ContextVar(
    "codev_systemd_transition_intent_guard",
    default=None,
)
_ACTIVE_TRANSITION_SESSION_GUARD: ContextVar[_SystemdTransitionGuard | None] = ContextVar(
    "codev_systemd_transition_session_guard",
    default=None,
)
_GateLockFactory = Callable[[], AbstractContextManager[_SystemdTransitionGuard]]


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def require_systemd_transition_guard() -> None:
    """证明当前执行流确实位于 gate EX 临界区，拒绝仅凭命名绕锁。"""
    guard = _ACTIVE_TRANSITION_GUARD.get()
    if (
        type(guard) is not _SystemdTransitionGuard
        or guard._secret is not _TRANSITION_GUARD_SECRET
        or guard._active is not True
    ):
        raise MaintenanceGateError("当前执行流缺少有效 systemd 转换守卫")


def require_systemd_transition_intent_guard() -> None:
    """证明当前执行流持有 intent EX，但不声称旧 gate 读者已经排空。"""
    guard = _ACTIVE_TRANSITION_INTENT_GUARD.get()
    if (
        type(guard) is not _SystemdTransitionGuard
        or guard._secret is not _TRANSITION_INTENT_GUARD_SECRET
        or guard._active is not True
    ):
        raise MaintenanceGateError("当前执行流缺少有效 systemd 转换意图守卫")


def require_systemd_transition_session_guard() -> None:
    """证明当前执行流持有跨阶段管理员会话锁，worker 不参与该锁。"""
    guard = _ACTIVE_TRANSITION_SESSION_GUARD.get()
    if (
        type(guard) is not _SystemdTransitionGuard
        or guard._secret is not _TRANSITION_SESSION_GUARD_SECRET
        or guard._active is not True
    ):
        raise MaintenanceGateError("当前执行流缺少有效 systemd 转换会话守卫")


@contextmanager
def _systemd_transition_guard() -> Iterator[_SystemdTransitionGuard]:
    guard = _SystemdTransitionGuard(_TRANSITION_GUARD_SECRET)
    token = _ACTIVE_TRANSITION_GUARD.set(guard)
    try:
        yield guard
    finally:
        guard.invalidate()
        _ACTIVE_TRANSITION_GUARD.reset(token)


@contextmanager
def _systemd_transition_intent_guard() -> Iterator[_SystemdTransitionGuard]:
    guard = _SystemdTransitionGuard(_TRANSITION_INTENT_GUARD_SECRET)
    token = _ACTIVE_TRANSITION_INTENT_GUARD.set(guard)
    try:
        yield guard
    finally:
        guard.invalidate()
        _ACTIVE_TRANSITION_INTENT_GUARD.reset(token)


@contextmanager
def _systemd_transition_session_guard() -> Iterator[_SystemdTransitionGuard]:
    guard = _SystemdTransitionGuard(_TRANSITION_SESSION_GUARD_SECRET)
    token = _ACTIVE_TRANSITION_SESSION_GUARD.set(guard)
    try:
        yield guard
    finally:
        guard.invalidate()
        _ACTIVE_TRANSITION_SESSION_GUARD.reset(token)


def _global_owner_uid() -> int:
    return 0


def _effective_uid() -> int:
    return os.geteuid()


def _marker_path(path: Path | None) -> Path:
    marker = _DEFAULT_MARKER_PATH if path is None else Path(path)
    if not marker.is_absolute():
        raise ValueError("维护门禁路径必须是绝对路径")
    return marker


def _lock_path(marker: Path) -> Path:
    return marker.with_name(f".{marker.name}.lock")


def _transition_intent_lock_path(marker: Path) -> Path:
    return marker.with_name(f".{marker.name}.transition-intent.lock")


def _transition_session_lock_path(marker: Path) -> Path:
    return marker.with_name(f".{marker.name}.transition-session.lock")


def _is_default_marker(marker: Path) -> bool:
    default = _DEFAULT_MARKER_PATH
    try:
        if os.path.normcase(os.path.normpath(marker)) == os.path.normcase(
            os.path.normpath(default)
        ):
            return True
        return marker.resolve(strict=False) == default.resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def _default_gate_directory_name(marker: Path) -> str:
    parent = marker.parent
    if parent.parent != _GLOBAL_GATE_ROOT or not parent.name:
        raise _MaintenanceLockUnavailable("默认维护门禁路径配置无效")
    return parent.name


def _open_global_linux_lock(*, exclusive: bool) -> int:
    marker = _marker_path(None)
    return _open_global_named_lock_backend(
        global_root=_GLOBAL_GATE_ROOT,
        directory_name=_default_gate_directory_name(marker),
        lock_name=_lock_path(marker).name,
        owner_uid=_global_owner_uid(),
        exclusive=exclusive,
        label="维护门禁锁",
    )


def _open_global_transition_intent_lock(*, exclusive: bool) -> int:
    marker = _marker_path(None)
    return _open_global_named_lock_backend(
        global_root=_GLOBAL_GATE_ROOT,
        directory_name=_default_gate_directory_name(marker),
        lock_name=_transition_intent_lock_path(marker).name,
        owner_uid=_global_owner_uid(),
        exclusive=exclusive,
        label="systemd 转换意图锁",
    )


def _open_global_transition_session_lock(*, exclusive: bool) -> int:
    marker = _marker_path(None)
    return _open_global_named_lock_backend(
        global_root=_GLOBAL_GATE_ROOT,
        directory_name=_default_gate_directory_name(marker),
        lock_name=_transition_session_lock_path(marker).name,
        owner_uid=_global_owner_uid(),
        exclusive=exclusive,
        label="systemd 转换会话锁",
    )


def _prepare_global_linux_lock(marker: Path) -> Path:
    lock = _lock_path(marker)
    intent_lock = _transition_intent_lock_path(marker)
    provision_global_locks(
        global_root=_GLOBAL_GATE_ROOT,
        directory_name=_default_gate_directory_name(marker),
        owner_uid=_global_owner_uid(),
        effective_uid=_effective_uid(),
        locks=(
            GlobalLockFile(lock.name, _LOCK_PAYLOAD, "维护门禁锁"),
            GlobalLockFile(
                intent_lock.name,
                _TRANSITION_INTENT_LOCK_PAYLOAD,
                "systemd 转换意图锁",
            ),
            GlobalLockFile(
                _transition_session_lock_path(marker).name,
                _TRANSITION_SESSION_LOCK_PAYLOAD,
                "systemd 转换会话锁",
            ),
        ),
    )
    descriptor = _open_global_reader_gate_lock()
    try:
        return lock
    finally:
        _release_linux_lock(descriptor)


def _prepare_linux_lock(marker: Path, *, global_path: bool) -> Path:
    if global_path:
        return _prepare_global_linux_lock(marker)
    return prepare_local_linux_lock(marker, _lock_path(marker), payload=_LOCK_PAYLOAD)


@contextmanager
def _exclusive_gate_lock(marker: Path) -> Iterator[None]:
    """特权写侧以独占锁发布或移除维护标记；非 Linux 测试路径无需锁。"""
    if not _is_linux():
        yield
        return
    if _is_default_marker(marker):
        with maintenance_systemd_transition_lock():
            yield
        return
    lock = _prepare_linux_lock(marker, global_path=False)
    descriptor = _open_linux_lock(lock, exclusive=True)
    body_error: BaseException | None = None
    try:
        yield
    except BaseException as error:
        body_error = error
        raise
    finally:
        try:
            _release_linux_lock(descriptor)
        except BaseException:
            if body_error is None:
                raise


def _open_global_reader_gate_lock() -> int:
    """按 intent SH -> gate SH 准入，并在返回前释放短持有的 intent。"""
    intent_descriptor = _open_global_transition_intent_lock(exclusive=False)
    try:
        gate_descriptor = _open_global_linux_lock(exclusive=False)
    except BaseException:
        try:
            _release_linux_lock(intent_descriptor)
        except BaseException:
            pass
        raise
    try:
        _release_linux_lock(intent_descriptor)
    except BaseException:
        try:
            _release_linux_lock(gate_descriptor)
        except BaseException:
            pass
        raise
    return gate_descriptor


@contextmanager
def _worker_start_permit(*, path: Path | None = None) -> Iterator[bool]:
    """在共享维护锁内复检维护标记，并把许可持有至脱离式子进程创建返回。"""
    if path is None and not _is_linux():
        yield True
        return
    try:
        marker = _marker_path(path)
    except (TypeError, ValueError):
        yield False
        return
    if not _is_linux():
        yield not maintenance_gate_active(path=marker)
        return
    try:
        if _is_default_marker(marker):
            descriptor = _open_global_reader_gate_lock()
        else:
            descriptor = _open_linux_lock(_lock_path(marker), exclusive=False)
    except (FileNotFoundError, _MaintenanceLockUnavailable):
        yield False
        return
    try:
        body_error: BaseException | None = None
        try:
            yield not maintenance_gate_active(path=marker)
        except BaseException as error:
            body_error = error
            raise
    finally:
        try:
            _release_linux_lock(descriptor)
        except BaseException:
            if body_error is None:
                raise


def maintenance_gate_active(path: Path | None = None) -> bool:
    """只有明确证明 marker 不存在时才允许启动；其余状态一律视为维护中。"""
    if path is None and not _is_linux():
        return False
    try:
        marker = _marker_path(path)
        marker.lstat()
    except FileNotFoundError:
        return False
    except (OSError, TypeError, ValueError):
        return True
    return True


def provision_maintenance_gate(path: Path | None = None) -> None:
    """特权预置 Linux 默认维护目录与锁；Windows 默认保持无状态。"""
    if not _is_linux():
        return
    try:
        marker = _marker_path(path)
        _prepare_linux_lock(marker, global_path=_is_default_marker(marker))
    except MaintenanceGateError:
        raise
    except (OSError, TypeError, ValueError):
        raise MaintenanceGateError("无法安全预置 reindex 维护门禁") from None


def _current_process_is_reindex_unit() -> bool:
    """延迟导入进程证明，避免门禁与 CLI/worker 层形成导入环。"""
    from codev_platform.reindex.external_worker_guard import (
        current_process_in_reindex_unit_cgroup,
    )

    return current_process_in_reindex_unit_cgroup()


def _write_maintenance_gate_record(marker: Path, record: object) -> None:
    """耐久替换 root 控制记录后固定为 service 可读、非 root 不可写。"""
    from .maintenance_gate_record import encode_maintenance_gate_record

    durable_write_replace(marker, encode_maintenance_gate_record(record))
    os.chmod(marker, _MARKER_MODE)
    fsync_directory(marker.parent)


def maintenance_gate_allows_current_worker() -> bool:
    """marker 存在时任何写 worker 都不得继续运行；待命另走窄许可。"""
    try:
        return not maintenance_gate_active()
    except Exception:
        return False


@contextmanager
def maintenance_reindex_operation_permit() -> Iterator[bool]:
    """为单次实际 reindex 写操作持有共享许可；service cgroup 不再有旁路。"""
    if not _is_linux():
        yield True
        return
    with _worker_start_permit() as permitted:
        yield permitted


@contextmanager
def _noop_systemd_gate_lock() -> Iterator[_SystemdTransitionGuard]:
    require_systemd_transition_intent_guard()
    with _systemd_transition_guard() as guard:
        yield guard


@contextmanager
def _global_systemd_gate_lock() -> Iterator[_SystemdTransitionGuard]:
    require_systemd_transition_intent_guard()
    descriptor = _open_global_linux_lock(exclusive=True)
    body_error: BaseException | None = None
    try:
        with _systemd_transition_guard() as guard:
            try:
                yield guard
            except BaseException as error:
                body_error = error
                raise
    finally:
        try:
            _release_linux_lock(descriptor)
        except BaseException:
            if body_error is None:
                raise


@contextmanager
def maintenance_systemd_transition_session() -> Iterator[_SystemdTransitionGuard]:
    """跨恢复阶段串行管理员操作；worker 不获取该锁，避免阻断待命读取。"""
    active = _ACTIVE_TRANSITION_SESSION_GUARD.get()
    if (
        type(active) is _SystemdTransitionGuard
        and active._secret is _TRANSITION_SESSION_GUARD_SECRET
        and active._active is True
    ):
        yield active
        return
    if not _is_linux():
        with _systemd_transition_session_guard() as guard:
            yield guard
        return
    try:
        marker = _marker_path(None)
        _prepare_global_linux_lock(marker)
        descriptor = _open_global_transition_session_lock(exclusive=True)
    except MaintenanceGateError:
        raise
    except (OSError, TypeError, ValueError):
        raise MaintenanceGateError("无法取得 systemd 转换会话锁") from None
    body_error: BaseException | None = None
    try:
        with _systemd_transition_session_guard() as guard:
            try:
                yield guard
            except BaseException as error:
                body_error = error
                raise
    finally:
        try:
            _release_linux_lock(descriptor)
        except BaseException:
            if body_error is None:
                raise


@contextmanager
def maintenance_systemd_transition_intent() -> Iterator[_GateLockFactory]:
    """在管理员会话内持有转换意图，再把 gate EX 工厂交给有界抢占编排。"""
    with maintenance_systemd_transition_session():
        with _maintenance_systemd_transition_intent() as gate_lock:
            yield gate_lock


@contextmanager
def _maintenance_systemd_transition_intent() -> Iterator[_GateLockFactory]:
    require_systemd_transition_session_guard()
    if not _is_linux():
        with _systemd_transition_intent_guard():
            yield _noop_systemd_gate_lock
        return
    try:
        marker = _marker_path(None)
        _prepare_global_linux_lock(marker)
        intent_descriptor = _open_global_transition_intent_lock(exclusive=True)
    except MaintenanceGateError:
        raise
    except (OSError, TypeError, ValueError):
        raise MaintenanceGateError("无法取得 systemd 维护转换锁") from None
    body_error: BaseException | None = None
    try:
        with _systemd_transition_intent_guard():
            try:
                yield _global_systemd_gate_lock
            except BaseException as error:
                body_error = error
                raise
    finally:
        try:
            _release_linux_lock(intent_descriptor)
        except BaseException:
            if body_error is None:
                raise


@contextmanager
def maintenance_systemd_transition_lock() -> Iterator[_SystemdTransitionGuard]:
    """按 intent EX -> gate EX 串行化全部 systemd 维护转换。"""
    with maintenance_systemd_transition_intent() as gate_lock:
        with gate_lock() as guard:
            yield guard


def activate_maintenance_gate(path: Path | None = None) -> None:
    """原子发布维护 marker；任何失败均向调用方返回受控错误。"""
    try:
        marker = _marker_path(path)
        with _exclusive_gate_lock(marker):
            from .maintenance_gate_record import maintenance_record

            _write_maintenance_gate_record(marker, maintenance_record())
    except MaintenanceGateError:
        raise
    except (OSError, TypeError, ValueError):
        raise MaintenanceGateError("无法原子启用 reindex 维护门禁") from None


def activate_maintenance_gate_while_systemd_transition_locked() -> None:
    """仅供已持有全局转换锁的 root 编排发布 marker，禁止脱离转换锁调用。"""
    from .maintenance_gate_record import maintenance_record

    require_systemd_transition_guard()
    _write_maintenance_gate_record(_marker_path(None), maintenance_record())


def activate_maintenance_gate_while_systemd_transition_intent_locked() -> None:
    """仅凭 intent EX 提前发布 marker；不声称旧 gate 读者已经排空。"""
    from .maintenance_gate_record import maintenance_record

    require_systemd_transition_intent_guard()
    _write_maintenance_gate_record(_marker_path(None), maintenance_record())


def deactivate_maintenance_gate(path: Path | None = None) -> None:
    """原子移除维护 marker；不存在时视为已经恢复。"""
    try:
        marker = _marker_path(path)
        with _exclusive_gate_lock(marker):
            durable_unlink(marker)
    except MaintenanceGateError:
        raise
    except (OSError, TypeError, ValueError):
        raise MaintenanceGateError("无法原子移除 reindex 维护门禁") from None


__all__ = [
    "MaintenanceGateError",
    "MaintenanceGateLockBusyError",
    "activate_maintenance_gate",
    "activate_maintenance_gate_while_systemd_transition_intent_locked",
    "activate_maintenance_gate_while_systemd_transition_locked",
    "arm_restore_standby",
    "claim_restore_standby",
    "complete_restore_standby",
    "complete_restore_standby_while_systemd_transition_locked",
    "deactivate_maintenance_gate",
    "maintenance_admin_window_permit",
    "maintenance_gate_active",
    "maintenance_gate_allows_current_worker",
    "maintenance_reindex_operation_permit",
    "maintenance_restore_standby_permit",
    "maintenance_systemd_transition_session",
    "maintenance_systemd_transition_intent",
    "maintenance_systemd_transition_lock",
    "provision_maintenance_gate",
    "require_systemd_transition_intent_guard",
    "require_systemd_transition_guard",
    "require_systemd_transition_session_guard",
    "read_maintenance_gate_record",
    "renew_claimed_restore_standby",
    "renew_claimed_restore_standby_while_systemd_transition_locked",
    "wait_for_restore_standby_release",
]
