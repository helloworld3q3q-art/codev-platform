"""reindex 队列 worker 与 drain-once 的 CLI 执行职责。"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from typing import Any

from codev_platform.reindex.isolated_worker_startup import (
    QueueStartupBootstrapRequired,
    QueueStartupOperatorBlocked,
)
from codev_platform.reindex.owner_readiness import (
    OWNER_BOOTSTRAP_EXIT_CODE,
    OWNER_OPERATOR_BLOCKED_EXIT_CODE,
)

Output = Callable[[str], None]
UnusedArguments = Callable[..., list[str]]
ExecutionModeResolver = Callable[[dict], str]
IsolatedWorkerBuilder = Callable[[dict, str], Any]
MaintenanceWaiter = Callable[[], bool]
MaintenanceInitializationPermit = Callable[[], AbstractContextManager[bool]]
ConfigLoader = Callable[[], dict]

_BOOTSTRAP_REQUIRED_MESSAGE = (
    "错误：稳定 queue owner 尚未初始化；"
    "需在维护窗口完成恢复审计后执行 reindex-queue init-owner --yes"
)
_OPERATOR_BLOCKED_MESSAGE = (
    "错误：稳定 queue owner 需要人工核验；"
    "请执行 reindex-queue status 并在维护窗口处理 owner 状态"
)


def _bootstrap_required_exit(owner_token: str, err: Output) -> int:
    """记录缺失 owner 的稳定退出状态，避免直接 worker 输出敏感异常。"""

    from codev_platform.reindex import supervisor

    supervisor.record_worker_exit(owner_token, "bootstrap-required")
    err(_BOOTSTRAP_REQUIRED_MESSAGE)
    return OWNER_BOOTSTRAP_EXIT_CODE


def _operator_blocked_exit(owner_token: str, err: Output) -> int:
    """记录需人工处理的 owner 停止状态，避免直接 worker 输出敏感异常。"""

    from codev_platform.reindex import supervisor

    supervisor.record_worker_exit(owner_token, "owner-operator-blocked")
    err(_OPERATOR_BLOCKED_MESSAGE)
    return OWNER_OPERATOR_BLOCKED_EXIT_CODE


def run_drain_once(
    args: argparse.Namespace,
    *,
    out: Output,
    err: Output,
    unused_common_args: UnusedArguments,
    worker_execution_mode: ExecutionModeResolver,
    build_isolated_worker: IsolatedWorkerBuilder,
) -> int:
    """在已取得维护许可后执行一次队列 drain。"""
    bad = unused_common_args(args)
    if bad:
        err(f"FATAL: drain-once 不接受这些参数: {', '.join(bad)}")
        return 1
    from codev_platform.core.config import load_config
    from codev_platform.reindex import supervisor

    cfg = load_config()
    try:
        mode = worker_execution_mode(cfg)
    except ValueError as exc:
        err(f"FATAL: {exc!s}")
        return 1
    owner_token = getattr(args, "owner_token", None) or supervisor.new_owner_token()
    with supervisor.acquire_run_lock(owner_token) as acquired:
        if not acquired:
            err("FATAL: reindex worker already running; drain-once refused")
            return 1
        supervisor.record_worker_start(owner_token, mode="drain-once", execution_mode=mode)

        def heartbeat() -> None:
            supervisor.record_heartbeat(owner_token)

        def job_event(job, status) -> None:
            supervisor.record_job_event(owner_token, job, status)

        try:
            if mode == "isolated":
                runtime = build_isolated_worker(cfg, owner_token)
                count = runtime.loop.drain_once()
            else:
                from codev_platform.reindex import ReindexWorker, open_default_queue

                queue = open_default_queue(fail_soft=True)
                worker = ReindexWorker(
                    queue,
                    cfg,
                    on_heartbeat=heartbeat,
                    on_job_event=job_event,
                )
                settings = supervisor.worker_settings(cfg)
                with supervisor.heartbeat_thread(owner_token, settings.heartbeat_sec):
                    count = worker.drain_once()
            out(f"drain-once processed={count}")
            supervisor.record_worker_exit(owner_token, "drain-once")
            return 0
        except QueueStartupBootstrapRequired:
            return _bootstrap_required_exit(owner_token, err)
        except QueueStartupOperatorBlocked:
            return _operator_blocked_exit(owner_token, err)
        except Exception as exc:
            supervisor.record_worker_exit(owner_token, "error", str(exc)[:200])
            raise


def run_worker(
    args: argparse.Namespace,
    *,
    out: Output,
    err: Output,
    unused_common_args: UnusedArguments,
    worker_execution_mode: ExecutionModeResolver,
    build_isolated_worker: IsolatedWorkerBuilder,
    maintenance_waiter: MaintenanceWaiter | None = None,
    maintenance_initialization_permit: MaintenanceInitializationPermit | None = None,
    config_loader: ConfigLoader | None = None,
) -> int:
    """先完成无写待命，再在短许可内构造 isolated runtime 并逐轮写入。"""
    bad = unused_common_args(
        args,
        allow_worker_flags=True,
        allow_required_execution_mode=True,
    )
    if bad:
        err(f"FATAL: worker 不接受这些参数: {', '.join(bad)}")
        return 1
    wait_for_release = (
        _default_maintenance_waiter
        if maintenance_waiter is None
        else maintenance_waiter
    )
    if not callable(wait_for_release):
        err("FATAL: reindex 维护待命适配器不可用")
        return 1
    try:
        released = wait_for_release()
    except MemoryError:
        raise
    except Exception:
        released = False
    if released is not True:
        err("FATAL: reindex 维护窗口已启用且待命身份无法证明；worker 不可运行")
        return 1
    acquire_initialization_permit = (
        _default_maintenance_initialization_permit
        if maintenance_initialization_permit is None
        else maintenance_initialization_permit
    )
    if not callable(acquire_initialization_permit):
        err("FATAL: reindex 初始化维护许可适配器不可用")
        return 1
    load = _default_config_loader if config_loader is None else config_loader
    if not callable(load):
        err("FATAL: reindex 配置读取器不可用")
        return 1
    from codev_platform.reindex import supervisor

    with ExitStack() as lifetime:
        try:
            context = acquire_initialization_permit()
        except MemoryError:
            raise
        except Exception:
            context = None
        if not isinstance(context, AbstractContextManager):
            err("FATAL: reindex 初始化维护许可不可用")
            return 1
        with context as permitted:
            if permitted is not True:
                err("FATAL: reindex 维护窗口已启用；worker 初始化被拒绝")
                return 1
            try:
                cfg = load()
                mode = worker_execution_mode(cfg)
            except MemoryError:
                raise
            except Exception as exc:
                err(f"FATAL: {exc!s}")
                return 1
            if mode != "isolated":
                err("FATAL: 常驻 reindex worker 只允许 execution_mode=isolated")
                return 1
            required_mode = getattr(args, "require_execution_mode", None)
            if required_mode is not None and mode != required_mode:
                err(f"FATAL: 该入口要求 execution_mode={required_mode}")
                return 1
            owner_token = getattr(args, "owner_token", None) or supervisor.new_owner_token()
            heartbeat_sec = getattr(args, "heartbeat_sec", None)
            if heartbeat_sec is None:
                heartbeat_sec = 1.0
            idle_exit_sec = getattr(args, "idle_exit_sec", None)
            if float(heartbeat_sec) <= 0:
                err("FATAL: --heartbeat-sec 必须大于 0")
                return 1
            if idle_exit_sec is not None and float(idle_exit_sec) <= 0:
                err("FATAL: --idle-exit-sec 必须大于 0")
                return 1
            lock_context = supervisor.acquire_run_lock(owner_token)
            if not isinstance(lock_context, AbstractContextManager):
                err("FATAL: reindex 运行锁上下文无效")
                return 1
            try:
                acquired = lifetime.enter_context(lock_context)
            except supervisor.RunLockUnavailableError as exc:
                err(f"FATAL: reindex 运行锁无法安全取得: {exc!s}")
                return 1
            if acquired is not True:
                out("worker 已在运行, 本进程退出")
                return 0
            short_lived = idle_exit_sec is not None
            supervisor.record_worker_start(
                owner_token,
                mode="short" if short_lived else "forever",
                idle_exit_sec=float(idle_exit_sec) if short_lived else None,
                heartbeat_sec=float(heartbeat_sec),
                execution_mode=mode,
            )
            try:
                runtime = build_isolated_worker(cfg, owner_token)
            except KeyboardInterrupt:
                supervisor.record_worker_exit(owner_token, "keyboard_interrupt")
                raise
            except QueueStartupBootstrapRequired:
                return _bootstrap_required_exit(owner_token, err)
            except QueueStartupOperatorBlocked:
                return _operator_blocked_exit(owner_token, err)
            except Exception as exc:
                supervisor.record_worker_exit(owner_token, "error", str(exc)[:200])
                raise
        try:
            if short_lived:
                asyncio.run(
                    runtime.loop.run_until_idle(
                        idle_exit_sec=float(idle_exit_sec),
                        poll_sec=float(heartbeat_sec),
                    )
                )
                supervisor.record_worker_exit(owner_token, "idle")
            else:
                asyncio.run(runtime.loop.run_forever(poll_sec=float(heartbeat_sec)))
        except QueueStartupBootstrapRequired:
            return _bootstrap_required_exit(owner_token, err)
        except QueueStartupOperatorBlocked:
            return _operator_blocked_exit(owner_token, err)
        except KeyboardInterrupt:
            supervisor.record_worker_exit(owner_token, "keyboard_interrupt")
            out("worker 退出")
        except Exception as exc:
            supervisor.record_worker_exit(owner_token, "error", str(exc)[:200])
            raise
        return 0


def _default_maintenance_waiter() -> bool:
    """marker 内只允许受控 service 待命，最终删除 marker 后才进入写路径。"""
    from codev_platform.reindex.maintenance_gate import wait_for_restore_standby_release

    return wait_for_restore_standby_release()


def _default_maintenance_initialization_permit() -> AbstractContextManager[bool]:
    """初始化只持有短共享许可；常驻运行阶段改由 loop 逐轮复检。"""
    from codev_platform.reindex.maintenance_gate import maintenance_reindex_operation_permit

    return maintenance_reindex_operation_permit()


def _default_config_loader() -> dict:
    from codev_platform.core.config import load_config

    return load_config()


__all__ = ["run_drain_once", "run_worker"]
