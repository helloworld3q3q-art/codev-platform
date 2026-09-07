"""reindex 运维写操作的停机证明与 run lock 编排。"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
import sys
from typing import Any, TypeVar

from codev_platform.reindex.runtime_owner import QueueOwnerIdentity


class ReindexAdminPreconditionError(RuntimeError):
    """运维写操作缺少可证明的停机或互斥前置条件。"""


WorkerStatusReader = Callable[[], object]
RunLockFactory = Callable[[str], AbstractContextManager[bool]]
OwnerInitializer = Callable[..., QueueOwnerIdentity]
MigrationRunner = Callable[..., Any]
LegacyWorkerStopProof = Callable[[], None]
MaintenanceWindowProof = Callable[[], None]
MaintenanceWindowPermit = Callable[[], AbstractContextManager[bool]]
Output = Callable[[str], None]
Prepared = TypeVar("Prepared")
Result = TypeVar("Result")


def run_confirmed_maintenance_action(
    *,
    input_loader: Callable[[], Prepared],
    action: Callable[[Prepared], Result],
    worker_status: WorkerStatusReader | None = None,
    acquire_run_lock: RunLockFactory | None = None,
    new_owner_token: Callable[[], str] | None = None,
    legacy_worker_stop_proof: LegacyWorkerStopProof | None = None,
    maintenance_window_permit: MaintenanceWindowPermit | None = None,
    maintenance_window_proof: MaintenanceWindowProof | None = None,
) -> Result:
    """在已证明维护窗口内延迟加载可写资源，再执行单一运维动作。

    队列构造对 File 会建目录、对 PG 会建 schema；因此不能作为锁外预检。
    """
    if not callable(input_loader) or not callable(action):
        raise ReindexAdminPreconditionError("reindex 运维动作适配器不可用")
    status_reader = _default_worker_status if worker_status is None else worker_status
    lock_factory = _default_acquire_run_lock if acquire_run_lock is None else acquire_run_lock
    token_factory = _default_new_owner_token if new_owner_token is None else new_owner_token
    with _confirmed_maintenance_window(
        maintenance_window_permit,
        maintenance_window_proof,
    ):
        _require_stopped_worker(status_reader)
        _require_legacy_worker_stopped(legacy_worker_stop_proof)
        with _exclusive_admin_lock(lock_factory, token_factory):
            _require_maintenance_window(maintenance_window_proof)
            _require_legacy_worker_stopped(legacy_worker_stop_proof)
            return action(input_loader())


def run_confirmed_owner_initialization(
    *,
    queue: object,
    cfg: dict,
    worker_status: WorkerStatusReader,
    acquire_run_lock: RunLockFactory,
    new_owner_token: Callable[[], str],
    owner_initializer: OwnerInitializer | None = None,
    legacy_worker_stop_proof: LegacyWorkerStopProof | None = None,
    maintenance_window_permit: MaintenanceWindowPermit | None = None,
    maintenance_window_proof: MaintenanceWindowProof | None = None,
) -> QueueOwnerIdentity:
    """在取得自身锁前确认 worker 已停，再显式创建稳定 queue owner。"""
    return _run_confirmed_owner_initialization(
        input_loader=lambda: (queue, cfg),
        worker_status=worker_status,
        acquire_run_lock=acquire_run_lock,
        new_owner_token=new_owner_token,
        owner_initializer=owner_initializer,
        legacy_worker_stop_proof=legacy_worker_stop_proof,
        maintenance_window_permit=maintenance_window_permit,
        maintenance_window_proof=maintenance_window_proof,
    )


def run_confirmed_owner_initialization_from_loaders(
    *,
    config_loader: Callable[[], dict],
    queue_opener: Callable[..., object],
    worker_status: WorkerStatusReader | None = None,
    acquire_run_lock: RunLockFactory | None = None,
    new_owner_token: Callable[[], str] | None = None,
    owner_initializer: OwnerInitializer | None = None,
    legacy_worker_stop_proof: LegacyWorkerStopProof | None = None,
    maintenance_window_permit: MaintenanceWindowPermit | None = None,
    maintenance_window_proof: MaintenanceWindowProof | None = None,
) -> QueueOwnerIdentity:
    """仅在维护锁和运行锁已取得后，加载配置并严格打开队列。"""
    if not callable(config_loader) or not callable(queue_opener):
        raise ReindexAdminPreconditionError("owner 初始化资源加载器不可用")

    def _load_owner_inputs() -> tuple[object, dict]:
        cfg = config_loader()
        if type(cfg) is not dict:
            raise ValueError("配置根类型无效")
        return queue_opener(fail_soft=False), cfg

    return _run_confirmed_owner_initialization(
        input_loader=_load_owner_inputs,
        worker_status=worker_status,
        acquire_run_lock=acquire_run_lock,
        new_owner_token=new_owner_token,
        owner_initializer=owner_initializer,
        legacy_worker_stop_proof=legacy_worker_stop_proof,
        maintenance_window_permit=maintenance_window_permit,
        maintenance_window_proof=maintenance_window_proof,
    )


def run_confirmed_legacy_migration(
    *,
    queue: object,
    timeout_sec: float,
    worker_status: WorkerStatusReader,
    acquire_run_lock: RunLockFactory,
    new_owner_token: Callable[[], str],
    migration_runner: MigrationRunner | None = None,
    legacy_worker_stop_proof: LegacyWorkerStopProof | None = None,
    maintenance_window_permit: MaintenanceWindowPermit | None = None,
    maintenance_window_proof: MaintenanceWindowProof | None = None,
) -> Any:
    """在停机快照与独占锁同时成立时，执行 legacy active 受控收口。"""
    migrate = _default_migration_runner if migration_runner is None else migration_runner
    return run_confirmed_maintenance_action(
        input_loader=lambda: queue,
        action=lambda loaded_queue: migrate(
            queue=loaded_queue,
            confirmed=True,
            run_lock_acquired=True,
            legacy_worker_stopped=True,
            timeout_sec=timeout_sec,
        ),
        worker_status=worker_status,
        acquire_run_lock=acquire_run_lock,
        new_owner_token=new_owner_token,
        legacy_worker_stop_proof=legacy_worker_stop_proof,
        maintenance_window_permit=maintenance_window_permit,
        maintenance_window_proof=maintenance_window_proof,
    )


def run_confirmed_queue_migration(
    *,
    queue: object,
    pending_mapping: object,
    stable_owner_token: str | None,
    bootstrap: bool,
    timeout_sec: float,
    worker_status: WorkerStatusReader,
    acquire_run_lock: RunLockFactory,
    new_owner_token: Callable[[], str],
    migration_runner: MigrationRunner | None = None,
    legacy_worker_stop_proof: LegacyWorkerStopProof | None = None,
    maintenance_window_permit: MaintenanceWindowPermit | None = None,
    maintenance_window_proof: MaintenanceWindowProof | None = None,
) -> Any:
    """在同一已证明停机窗口内执行 pending 与 active 的组合迁移。"""
    return run_confirmed_queue_migration_from_loader(
        input_loader=lambda: {
            "queue": queue,
            "pending_mapping": pending_mapping,
            "stable_owner_token": stable_owner_token,
            "bootstrap": bootstrap,
            "timeout_sec": timeout_sec,
        },
        migration_runner=migration_runner,
        worker_status=worker_status,
        acquire_run_lock=acquire_run_lock,
        new_owner_token=new_owner_token,
        legacy_worker_stop_proof=legacy_worker_stop_proof,
        maintenance_window_permit=maintenance_window_permit,
        maintenance_window_proof=maintenance_window_proof,
    )


def run_confirmed_queue_migration_from_loader(
    *,
    input_loader: Callable[[], dict[str, object]],
    migration_runner: MigrationRunner | None = None,
    worker_status: WorkerStatusReader | None = None,
    acquire_run_lock: RunLockFactory | None = None,
    new_owner_token: Callable[[], str] | None = None,
    legacy_worker_stop_proof: LegacyWorkerStopProof | None = None,
    maintenance_window_permit: MaintenanceWindowPermit | None = None,
    maintenance_window_proof: MaintenanceWindowProof | None = None,
) -> Any:
    """在确认窗口内才加载迁移所需队列与 owner 上下文。"""
    migrate = _default_queue_migration_runner if migration_runner is None else migration_runner

    def _run_migration(inputs: dict[str, object]) -> Any:
        return migrate(
            queue=inputs["queue"],
            pending_mapping=inputs["pending_mapping"],
            confirmed=True,
            run_lock_acquired=True,
            legacy_worker_stopped=True,
            stable_owner_token=inputs["stable_owner_token"],
            bootstrap=inputs["bootstrap"],
            timeout_sec=inputs["timeout_sec"],
        )

    return run_confirmed_maintenance_action(
        input_loader=input_loader,
        action=_run_migration,
        worker_status=worker_status,
        acquire_run_lock=acquire_run_lock,
        new_owner_token=new_owner_token,
        legacy_worker_stop_proof=legacy_worker_stop_proof,
        maintenance_window_permit=maintenance_window_permit,
        maintenance_window_proof=maintenance_window_proof,
    )


def cmd_reindex_admin(
    args: object,
    *,
    out: Output = print,
    err: Output | None = None,
    config_loader: Callable[[], dict] | None = None,
    queue_opener: Callable[..., object] | None = None,
    owner_runner: Callable[..., QueueOwnerIdentity] | None = None,
    migration_command: Callable[..., int] | None = None,
) -> int:
    """分发显式 reindex 运维动作，避免通用队列命令绕过停机门禁。"""
    write_error = _default_error_output if err is None else err
    action = getattr(args, "action", None)
    if action == "migrate-legacy":
        run_migration = _default_migration_command if migration_command is None else migration_command
        return run_migration(
            args,
            out=out,
            err=write_error,
            config_loader=config_loader,
            queue_opener=queue_opener,
        )
    if action != "init-owner":
        write_error("FATAL: 不支持的 reindex 运维动作")
        return 1
    return _cmd_init_owner(
        args,
        out=out,
        err=write_error,
        config_loader=config_loader,
        queue_opener=queue_opener,
        owner_runner=owner_runner,
    )


def _cmd_init_owner(
    args: object,
    *,
    out: Output,
    err: Output,
    config_loader: Callable[[], dict] | None,
    queue_opener: Callable[..., object] | None,
    owner_runner: Callable[..., QueueOwnerIdentity] | None,
) -> int:
    """执行稳定 owner 初始化的最小写路径。"""
    if getattr(args, "yes", False) is not True:
        out("演练：init-owner 是写操作；确认后请添加 --yes")
        return 0
    load = _default_config_loader if config_loader is None else config_loader
    open_queue = _default_strict_queue_opener if queue_opener is None else queue_opener
    initialize = _default_owner_initializer if owner_runner is None else owner_runner
    try:
        owner = run_confirmed_owner_initialization_from_loaders(
            config_loader=load,
            queue_opener=open_queue,
            owner_initializer=initialize,
        )
    except MemoryError:
        raise
    except Exception as error:
        err(f"FATAL: 初始化稳定 queue owner 失败: {type(error).__name__}")
        return 1
    out(f"稳定 queue owner 初始化完成（后端={owner.binding.kind}）")
    return 0


@contextmanager
def _confirmed_maintenance_window(
    permit_factory: MaintenanceWindowPermit | None,
    proof: MaintenanceWindowProof | None,
) -> Iterator[None]:
    """把管理员实际写阶段锁定在 prepare 已证明的维护稳态内。"""
    acquire = (
        _default_maintenance_window_permit
        if permit_factory is None
        else permit_factory
    )
    if not callable(acquire):
        raise ReindexAdminPreconditionError("维护窗口许可适配器不可用")
    try:
        context = acquire()
    except MemoryError:
        raise
    except Exception as error:
        raise ReindexAdminPreconditionError("无法取得 reindex 维护窗口") from error
    if not isinstance(context, AbstractContextManager):
        raise ReindexAdminPreconditionError("维护窗口许可上下文无效")
    with context as permitted:
        if permitted is not True:
            raise ReindexAdminPreconditionError("未处于受管 reindex 维护窗口，拒绝运维写操作")
        _require_maintenance_window(proof)
        yield


def _require_maintenance_window(proof: MaintenanceWindowProof | None) -> None:
    verify = _default_maintenance_window_proof if proof is None else proof
    if not callable(verify):
        raise ReindexAdminPreconditionError("维护窗口证明适配器不可用")
    try:
        verify()
    except MemoryError:
        raise
    except Exception as error:
        raise ReindexAdminPreconditionError("无法证明 reindex 维护窗口处于安全态") from error


def _require_stopped_worker(reader: WorkerStatusReader) -> dict[str, object]:
    if not callable(reader):
        raise ReindexAdminPreconditionError("worker 状态读取器不可用")
    try:
        status = reader()
    except MemoryError:
        raise
    except Exception as error:
        raise ReindexAdminPreconditionError("无法确认 worker 已停止") from error
    if type(status) is not dict or status.get("running") is not False:
        raise ReindexAdminPreconditionError("worker 未确认停止，拒绝运维写操作")
    return dict(status)


def _require_legacy_worker_stopped(proof: LegacyWorkerStopProof | None) -> None:
    verify = _default_legacy_worker_stop_proof if proof is None else proof
    if not callable(verify):
        raise ReindexAdminPreconditionError("旧 worker 停止证明适配器不可用")
    try:
        verify()
    except MemoryError:
        raise
    except Exception as error:
        raise ReindexAdminPreconditionError("无法证明旧 reindex worker 已停止") from error


@contextmanager
def _exclusive_admin_lock(
    acquire_run_lock: RunLockFactory,
    new_owner_token: Callable[[], str],
) -> Iterator[None]:
    """把 supervisor 运行锁转换为只允许成功进入的运维上下文。"""
    if not callable(acquire_run_lock) or not callable(new_owner_token):
        raise ReindexAdminPreconditionError("reindex 运维锁适配器不可用")
    try:
        owner_token = new_owner_token()
    except MemoryError:
        raise
    except Exception as error:
        raise ReindexAdminPreconditionError("无法生成 reindex 运维锁身份") from error
    if type(owner_token) is not str or not owner_token:
        raise ReindexAdminPreconditionError("reindex 运维锁身份无效")
    try:
        context = acquire_run_lock(owner_token)
        if not isinstance(context, AbstractContextManager):
            raise TypeError("run lock 上下文无效")
    except MemoryError:
        raise
    except Exception as error:
        raise ReindexAdminPreconditionError("无法取得 reindex 运维锁") from error
    with context as acquired:
        if acquired is not True:
            raise ReindexAdminPreconditionError("未取得 reindex run lock，拒绝运维写操作")
        yield


def _run_confirmed_owner_initialization(
    *,
    input_loader: Callable[[], tuple[object, dict]],
    worker_status: WorkerStatusReader | None,
    acquire_run_lock: RunLockFactory | None,
    new_owner_token: Callable[[], str] | None,
    owner_initializer: OwnerInitializer | None,
    legacy_worker_stop_proof: LegacyWorkerStopProof | None,
    maintenance_window_permit: MaintenanceWindowPermit | None,
    maintenance_window_proof: MaintenanceWindowProof | None,
) -> QueueOwnerIdentity:
    """复用同一确认窗口，避免加载式与已加载式 owner 初始化语义漂移。"""
    initialize = _default_owner_initializer if owner_initializer is None else owner_initializer

    def _initialize(inputs: tuple[object, dict]) -> QueueOwnerIdentity:
        queue, cfg = inputs
        return initialize(queue=queue, cfg=cfg)

    owner = run_confirmed_maintenance_action(
        input_loader=input_loader,
        action=_initialize,
        worker_status=worker_status,
        acquire_run_lock=acquire_run_lock,
        new_owner_token=new_owner_token,
        legacy_worker_stop_proof=legacy_worker_stop_proof,
        maintenance_window_permit=maintenance_window_permit,
        maintenance_window_proof=maintenance_window_proof,
    )
    if type(owner) is not QueueOwnerIdentity:
        raise ReindexAdminPreconditionError("稳定 queue owner 初始化结果无效")
    return owner


def _default_owner_initializer(*, queue: object, cfg: dict) -> QueueOwnerIdentity:
    from codev_platform.reindex.isolated_worker_startup import initialize_queue_owner

    return initialize_queue_owner(queue=queue, cfg=cfg)


def _default_worker_status() -> object:
    from codev_platform.reindex import supervisor

    return supervisor.worker_status()


def _default_acquire_run_lock(owner_token: str) -> AbstractContextManager[bool]:
    from codev_platform.reindex import supervisor

    return supervisor.acquire_run_lock(owner_token)


def _default_new_owner_token() -> str:
    from codev_platform.reindex import supervisor

    return supervisor.new_owner_token()


def _default_migration_runner(**kwargs: object) -> Any:
    from codev_platform.ops.reindex_migration import run_legacy_queue_migration

    return run_legacy_queue_migration(**kwargs)


def _default_queue_migration_runner(**kwargs: object) -> Any:
    from codev_platform.ops.reindex_migration import run_queue_migration_window

    return run_queue_migration_window(**kwargs)


def _default_maintenance_window_permit() -> AbstractContextManager[bool]:
    """延迟接线 root 预置的 marker 锁，避免 admin 层持有门禁存储细节。"""
    from codev_platform.reindex.maintenance_gate import maintenance_admin_window_permit

    return maintenance_admin_window_permit()


def _default_maintenance_window_proof() -> None:
    """同时验证 marker、受管 drop-in、systemd cgroup 与宽写入者为空。"""
    from codev_platform.ops.reindex_maintenance import inspect_reindex_maintenance

    inspect_reindex_maintenance()


def _default_migration_command(args: object, **kwargs: object) -> int:
    from codev_platform.ops.reindex_migration_cli import cmd_migrate_legacy

    return cmd_migrate_legacy(args, **kwargs)


def _default_legacy_worker_stop_proof() -> None:
    from codev_platform.ops.reindex_admin_systemd_guard import (
        verify_codev_reindex_stopped,
    )
    from codev_platform.reindex.external_worker_guard import (
        assert_no_external_reindex_writers,
    )

    verify_codev_reindex_stopped()
    assert_no_external_reindex_writers()


def _default_config_loader() -> dict:
    from codev_platform.core.config import load_config

    return load_config()


def _default_strict_queue_opener(*, fail_soft: bool) -> object:
    from codev_platform.reindex import open_default_queue

    if fail_soft is not False:
        raise ValueError("运维队列必须严格打开")
    return open_default_queue(fail_soft=False)


def _default_error_output(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


__all__ = [
    "ReindexAdminPreconditionError",
    "cmd_reindex_admin",
    "run_confirmed_maintenance_action",
    "run_confirmed_legacy_migration",
    "run_confirmed_queue_migration",
    "run_confirmed_queue_migration_from_loader",
    "run_confirmed_owner_initialization",
    "run_confirmed_owner_initialization_from_loaders",
]
