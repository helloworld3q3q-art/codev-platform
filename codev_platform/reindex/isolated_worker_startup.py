"""隔离 worker 在任何恢复动作前建立稳定 queue owner 上下文。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .queue_backend_binding import queue_backend_binding
from .runtime_owner import (
    QueueOwnerBootstrapRequired,
    QueueOwnerCorruptionError,
    QueueOwnerIdentity,
    QueueOwnerRecoveryRequired,
    QueueOwnerStore,
    QueueOwnerUnavailableError,
)


class QueueStartupError(RuntimeError):
    """稳定 owner、control journal 或恢复审计无法安全建立。"""


class QueueStartupBootstrapRequired(QueueStartupError):
    """稳定 queue owner 缺失，worker 必须等待显式运维初始化。"""


class QueueStartupOperatorBlocked(QueueStartupError):
    """稳定 queue owner 需由操作员恢复或核验后才能启动。"""


@dataclass(frozen=True, slots=True)
class QueueStartupContext:
    """仅供生产组合根持有的稳定 owner 与已审计 journal。"""

    owner: QueueOwnerIdentity = field(repr=False)
    journal: object = field(repr=False)
    recovery_snapshot: object = field(repr=False)


def load_queue_startup_context(
    *,
    queue: object,
    cfg: dict,
    owner_store: QueueOwnerStore | object | None = None,
    journal_factory: Callable[[str], object] | None = None,
    inspect_recovery: Callable[[object, object], object] | None = None,
    verify_owner: Callable[[object, str], None] | None = None,
) -> QueueStartupContext:
    """按 owner → journal → 只读审计 → 所有权校验的固定顺序建立启动事实。"""
    store = QueueOwnerStore() if owner_store is None else owner_store
    make_journal = _default_journal_factory if journal_factory is None else journal_factory
    inspect = _default_recovery_inspector if inspect_recovery is None else inspect_recovery
    verify = _default_owner_verifier if verify_owner is None else verify_owner
    if not callable(getattr(store, "load", None)):
        raise QueueStartupError("稳定 queue owner 存储不可用")
    if not all(callable(item) for item in (make_journal, inspect, verify)):
        raise QueueStartupError("启动恢复适配器不可用")
    bootstrap_required = False
    operator_blocked = False
    startup_failed = False
    try:
        binding = queue_backend_binding(queue, cfg)
        owner = store.load(binding)
        if type(owner) is not QueueOwnerIdentity:
            raise ValueError("稳定 queue owner 类型无效")
        journal = make_journal(owner.token)
        if journal is None:
            raise ValueError("control journal 不可用")
        snapshot = inspect(queue, journal)
        if snapshot is None:
            raise ValueError("恢复审计结果无效")
        verify(snapshot, owner.token)
    except QueueOwnerBootstrapRequired:
        bootstrap_required = True
    except (
        QueueOwnerRecoveryRequired,
        QueueOwnerCorruptionError,
        QueueOwnerUnavailableError,
    ):
        operator_blocked = True
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        startup_failed = True
    if bootstrap_required:
        raise QueueStartupBootstrapRequired("隔离 worker 启动前稳定 queue owner 尚未初始化")
    if operator_blocked:
        raise QueueStartupOperatorBlocked("隔离 worker 启动前稳定 queue owner 需要操作员处理")
    if startup_failed:
        raise QueueStartupError("隔离 worker 启动前恢复事实无法确认")
    return QueueStartupContext(owner, journal, snapshot)


def initialize_queue_owner(
    *,
    queue: object,
    cfg: dict,
    owner_store: QueueOwnerStore | object | None = None,
    journal_factory: Callable[[str], object] | None = None,
    inspect_recovery: Callable[[object, object], object] | None = None,
) -> QueueOwnerIdentity:
    """显式初始化缺失 owner 前，先以只读 bootstrap journal 审计恢复事实。"""
    store = QueueOwnerStore() if owner_store is None else owner_store
    make_journal = _default_journal_factory if journal_factory is None else journal_factory
    inspect = _default_recovery_inspector if inspect_recovery is None else inspect_recovery
    if not callable(getattr(store, "initialize", None)):
        raise QueueStartupError("稳定 queue owner 初始化存储不可用")
    if not all(callable(item) for item in (make_journal, inspect)):
        raise QueueStartupError("初始化恢复审计适配器不可用")
    initialization_failed = False
    try:
        binding = queue_backend_binding(queue, cfg)
        journal = make_journal("bootstrap-readonly")
        if journal is None:
            raise ValueError("bootstrap control journal 不可用")
        snapshot = inspect(queue, journal)
        if snapshot is None:
            raise ValueError("恢复审计结果无效")
        recovery_state_present = _snapshot_recovery_state_present(snapshot)
        if recovery_state_present(0.0):
            raise ValueError("存在待恢复状态")
        owner = store.initialize(
            binding,
            recovery_state_present=recovery_state_present,
        )
        if type(owner) is not QueueOwnerIdentity:
            raise ValueError("稳定 queue owner 类型无效")
    except MemoryError:
        raise
    except Exception:
        initialization_failed = True
    if initialization_failed:
        raise QueueStartupError("初始化稳定 queue owner 前恢复事实无法确认")
    return owner


def _snapshot_recovery_state_present(snapshot: object) -> Callable[[float], bool]:
    """把已审计快照转换为无 I/O 的 owner 初始化探针。"""
    present = getattr(snapshot, "recovery_state_present", None)
    if type(present) is not bool:
        raise ValueError("恢复审计状态类型无效")

    def recovery_state_present(_remaining: float) -> bool:
        return present

    return recovery_state_present


def _default_journal_factory(owner_token: str) -> object:
    from .control_journal import ControlJournal

    return ControlJournal(owner_token=owner_token)


def _default_recovery_inspector(queue: object, journal: object) -> object:
    from .recovery_audit import inspect_startup_recovery

    return inspect_startup_recovery(queue, journal)


def _default_owner_verifier(snapshot: object, owner_token: str) -> None:
    from .recovery_audit import verify_startup_owner

    verify_startup_owner(snapshot, owner_token)


__all__ = [
    "QueueStartupContext",
    "QueueStartupBootstrapRequired",
    "QueueStartupError",
    "QueueStartupOperatorBlocked",
    "initialize_queue_owner",
    "load_queue_startup_context",
]
