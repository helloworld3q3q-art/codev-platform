"""codev_platform.ops.reindex_queue —— reindex 写队列的 CLI 薄壳。

  reindex-queue enqueue [project] [--kind ...]   生产者: 落标记即返回 (hook/webhook/手动)
  reindex-queue status                           看待办
  reindex-queue drain-once                       跑完当前待办后退出
  reindex-queue worker                           跑消费者 (默认常驻; 可短驻)

只调 codev_platform.reindex 的三层 (queue/runners/worker), 不含逻辑。
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from codev_platform.ops.reindex_queue_parser import register
from codev_platform.core.project_id import ProjectIdError, resolve_local, validate
from codev_platform.ops.reindex_worker_runtime import (
    build_isolated_worker as _build_isolated_worker,
    worker_execution_mode as _worker_execution_mode,
)
from codev_platform.ops.reindex_queue_worker import (
    run_drain_once as _run_drain_once_action,
    run_worker as _run_worker_action,
)
from codev_platform.ops.reindex_admin import (
    ReindexAdminPreconditionError,
    run_confirmed_maintenance_action,
)
from codev_platform.reindex.maintenance_gate import maintenance_reindex_operation_permit
from codev_platform.reindex.queue import JobMeta

_STALE_PENDING_SEC = 300   # status: 待办 > 5min 仍未被认领 → 标 STALE(疑孤儿: 无 worker 白名单覆盖)

__all__ = ["cmd_reindex_queue", "register"]


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _resolve_kinds(kind_arg: str) -> list[str] | None:
    from codev_platform.reindex import runners
    valid = set(runners.kinds())
    if kind_arg == "all":
        return list(runners.kinds())
    if kind_arg in valid:
        return [kind_arg]
    _err(f"FATAL: 未知 kind '{kind_arg}'; 可选: {', '.join(sorted(valid))} 或 all")
    return None


def _resolve_prune_kinds(kind_arg: str) -> set[str] | None:
    if kind_arg == "all":
        return None
    if any(sep in str(kind_arg) for sep in ("/", "\\", "..", "__")):
        _err(f"FATAL: 非法 kind '{kind_arg}'")
        return set()
    return {kind_arg}


def _stale_label(seconds: int) -> str:
    return f">{seconds // 60}min" if seconds % 60 == 0 else f">{seconds}s"


def _stale_jobs(jobs, *, now: float, older_than_sec: int,
                project_id: str | None = None,
                kinds: set[str] | None = None):
    return [
        j for j in jobs
        if j.enqueued_at is not None
        and (now - j.enqueued_at) > older_than_sec
        and (project_id is None or j.project_id == project_id)
        and (kinds is None or j.kind in kinds)
    ]


def _resolve_project_filter(project: str | None) -> str | None:
    if not project:
        return None
    try:
        return validate(project)
    except ProjectIdError as exc:
        _err(f"FATAL: {exc!s}")
        return ""


def _break_lease_target(snapshot, project_id: str, kinds: set[str]):
    active_jobs = list(snapshot.active) + list(getattr(snapshot, "expired_active", []) or [])
    return next(
        (job for job in active_jobs if job.project_id == project_id and job.kind in kinds),
        None,
    )


def _job_age_sec(job, *, now: float) -> int | None:
    if job is None or job.enqueued_at is None:
        return None
    return max(0, int(now - float(job.enqueued_at)))


def _target_is_expired(job, snapshot, *, now: float) -> bool:
    if job is None:
        return False
    if any(item.key == job.key and item.token == job.token for item in snapshot.expired_active):
        return True
    lease_expires_at = getattr(job, "lease_expires_at", None)
    return isinstance(lease_expires_at, int | float) and float(lease_expires_at) <= now


def _format_age(seconds: int | None) -> str:
    return "unknown" if seconds is None else f"{seconds}s"


def _normalize_pull_policy(value: str | None) -> str | None:
    raw = (value or "").strip()
    if raw == "never":
        return "never"
    if raw in {"ff-only", "ff_only"}:
        return "ff_only"
    return None


def _active_pending_conflicts(pending_jobs, active_jobs) -> list:
    active_keys = {job.key for job in active_jobs}
    return [job for job in pending_jobs if job.key in active_keys]


def _unused_common_args(args: argparse.Namespace, *,
                        allow_project: bool = False,
                        allow_kind: bool = False,
                        allow_yes: bool = False,
                        allow_force_file: bool = False,
                        allow_older_than: bool = False,
                        allow_worker_flags: bool = False,
                        allow_required_execution_mode: bool = False,
                        allow_pull_policy: bool = False,
                        allow_target_commit: bool = False,
                        allow_pending_map: bool = False) -> list[str]:
    bad: list[str] = []
    if not allow_project and getattr(args, "project", None):
        bad.append("project")
    if not allow_kind and getattr(args, "kind", "all") != "all":
        bad.append("--kind")
    if not allow_pull_policy and getattr(args, "pull_policy", "never") != "never":
        bad.append("--pull-policy")
    if not allow_target_commit and getattr(args, "target_commit", None) is not None:
        bad.append("--target-commit")
    if not allow_pending_map and getattr(args, "pending_map", None) is not None:
        bad.append("--pending-map")
    if getattr(args, "yes", False) and not allow_yes:
        bad.append("--yes")
    if getattr(args, "force_file", False) and not allow_force_file:
        bad.append("--force-file")
    older_than_sec = getattr(args, "older_than_sec", None)
    if not allow_older_than and older_than_sec not in (None, _STALE_PENDING_SEC):
        bad.append("--older-than-sec")
    if not allow_worker_flags:
        if getattr(args, "idle_exit_sec", None) is not None:
            bad.append("--idle-exit-sec")
        if getattr(args, "heartbeat_sec", None) is not None:
            bad.append("--heartbeat-sec")
    if not allow_required_execution_mode and getattr(args, "require_execution_mode", None):
        bad.append("--require-execution-mode")
    return bad


def _cmd_drain_once(args: argparse.Namespace) -> int:
    """委托 worker 执行模块，保持队列 CLI 只负责路由。"""
    return _run_drain_once_action(
        args,
        out=_out,
        err=_err,
        unused_common_args=_unused_common_args,
        worker_execution_mode=_worker_execution_mode,
        build_isolated_worker=_build_isolated_worker,
    )


def _cmd_worker(args: argparse.Namespace) -> int:
    """委托 worker 执行模块，保持队列 CLI 只负责路由。"""
    return _run_worker_action(
        args,
        out=_out,
        err=_err,
        unused_common_args=_unused_common_args,
        worker_execution_mode=_worker_execution_mode,
        build_isolated_worker=_build_isolated_worker,
    )


def _run_permitted_worker_action(
    args: argparse.Namespace,
    action: Callable[[argparse.Namespace], int],
) -> int:
    """把非受控 worker 操作完整包在维护共享许可内。"""
    with maintenance_reindex_operation_permit() as permitted:
        if not permitted:
            _err("FATAL: reindex 维护窗口已启用或维护许可无法证明；worker 不可运行")
            return 1
        return action(args)


def _run_confirmed_queue_mutation(
    args: argparse.Namespace,
    action: Callable[[argparse.Namespace, object], int],
) -> int:
    """在维护独占锁和 supervisor 运行锁内打开队列并执行破坏性运维写。"""
    from codev_platform.reindex import open_default_queue

    try:
        return run_confirmed_maintenance_action(
            input_loader=lambda: open_default_queue(fail_soft=False),
            action=lambda queue: action(args, queue),
        )
    except MemoryError:
        raise
    except ReindexAdminPreconditionError:
        _err("FATAL: 未处于受管 reindex 维护窗口，拒绝破坏性队列写操作")
    except Exception as error:
        _err(f"FATAL: 破坏性 reindex 队列操作失败: {type(error).__name__}")
    return 1


def _cmd_prune_stale(args: argparse.Namespace, q: object) -> int:
    """在调用方已确定的队列一致性窗口内清理 stale pending。"""
    import time as _time

    older_than_sec = int(getattr(args, "older_than_sec", None) or _STALE_PENDING_SEC)
    if older_than_sec < 0:
        _err("FATAL: --older-than-sec 不能为负数")
        return 1
    project_id = _resolve_project_filter(args.project)
    if project_id == "":
        return 1
    kinds = _resolve_prune_kinds(args.kind)
    if kinds == set():
        return 1
    now = _time.time()
    snapshot = q.snapshot()
    stale = _stale_jobs(
        snapshot.pending,
        now=now,
        older_than_sec=older_than_sec,
        project_id=project_id,
        kinds=kinds,
    )
    conflicts = _active_pending_conflicts(
        stale,
        list(snapshot.active) + list(snapshot.expired_active),
    )
    if conflicts:
        _err("FATAL: 以下 stale pending 同时存在 active lease，拒绝删除：")
        for job in conflicts:
            _err(f"  {job.key}")
        _err("请先等待 active 完成，或使用 `codev-platform reindex-queue break-lease <project> --kind <kind> --yes`")
        return 1
    if not stale:
        _out(f"无 STALE 待清理({_stale_label(older_than_sec)})")
        return 0
    _out(("DRY-RUN " if not args.yes else "") + f"STALE {len(stale)} 个({_stale_label(older_than_sec)}):")
    for job in stale:
        age = int(now - job.enqueued_at)
        _out(f"  {job.key}  (age {age}s)")
    if not args.yes:
        _out("未删除任何任务; 加 --yes 才会清理")
        return 0
    from codev_platform.reindex.queue import FileSpoolQueue

    if isinstance(q, FileSpoolQueue) and not args.force_file:
        _err(
            "FATAL: file 队列无 running lease, 为避免误删正在跑的本机任务, "
            "请先确认 worker 已停止, 再加 --force-file"
        )
        return 1
    discard = getattr(q, "discard", None)
    if not callable(discard):
        _err(f"FATAL: 当前队列后端 {type(q).__name__} 不支持 discard")
        return 1
    removed = 0
    kept = 0
    for job in stale:
        if discard(job):
            removed += 1
        else:
            kept += 1
    _out(f"清理完成: removed={removed}, kept={kept}(peek 后被重新入队/状态变化)")
    return 0


def _cmd_break_lease(args: argparse.Namespace, q: object) -> int:
    """在调用方已确定的队列一致性窗口内受控释放单个 active lease。"""
    bad = _unused_common_args(
        args,
        allow_project=True,
        allow_kind=True,
        allow_yes=True,
        allow_older_than=True,
    )
    if bad:
        _err(f"FATAL: break-lease 不接受这些参数: {', '.join(bad)}")
        return 1
    older_than_raw = getattr(args, "older_than_sec", None)
    if older_than_raw is not None and int(older_than_raw) < 0:
        _err("FATAL: --older-than-sec 不能为负数")
        return 1
    project_id = _resolve_project_filter(args.project)
    if not project_id:
        _err("FATAL: break-lease 必须提供 project_id")
        return 1
    if args.kind == "all":
        _err("FATAL: break-lease 必须用 --kind 精确定位单个 active lease")
        return 1
    kinds = _resolve_prune_kinds(args.kind)
    if not kinds:
        return 1
    snapshot = q.snapshot()
    target = _break_lease_target(snapshot, project_id, kinds)
    if target is None:
        _out("未找到匹配的 active lease")
        return 0
    import time as _time
    from codev_platform.reindex import status as reindex_status

    now = _time.time()
    if not args.yes:
        _out(f"DRY-RUN break active lease: {target.key}")
        _out("未执行 break; 加 --yes 才会真正释放 active lease")
        return 0
    latest_snapshot = q.snapshot()
    latest_target = _break_lease_target(latest_snapshot, project_id, kinds)
    if latest_target is None:
        _err("FATAL: 目标 active lease 已变化或消失；请先执行 status 再重试")
        return 1
    now = _time.time()
    active_age_sec = _job_age_sec(latest_target, now=now)
    summary = reindex_status.summarize(queue=q)
    heartbeat_severity = str(summary.get("heartbeat_severity") or "OK")
    phase_severity = str(summary.get("phase_severity") or "OK")
    running = bool(summary.get("running"))
    target_expired = _target_is_expired(latest_target, latest_snapshot, now=now)
    allow_break = (
        target_expired
        or not running
        or heartbeat_severity == "FAIL"
        or phase_severity == "FAIL"
    )
    if not allow_break:
        _err("FATAL: worker 仍在健康运行，拒绝 break-lease；请先执行 status 或继续等待")
        return 1
    if older_than_raw is not None and (
        active_age_sec is None or active_age_sec < int(older_than_raw)
    ):
        _err(
            "FATAL: active lease 年龄未达到阈值 "
            f"({active_age_sec if active_age_sec is not None else 'unknown'}s < {int(older_than_raw)}s)，拒绝 break-lease；"
            "请调低 --older-than-sec 或继续等待"
        )
        return 1
    _out(f"break active lease: {latest_target.key}")
    breaker = getattr(q, "break_lease", None)
    if not callable(breaker):
        _err(f"FATAL: 当前队列后端 {type(q).__name__} 不支持 break_lease")
        return 1
    if not breaker(latest_target):
        _err("FATAL: break_lease 未命中 active lease，可能已被其他进程处理")
        return 1
    _out("break active lease: done")
    return 0


def cmd_reindex_queue(args: argparse.Namespace) -> int:
    if args.action not in {"worker", "drain-once"} and getattr(args, "owner_token", None):
        _err(f"FATAL: {args.action} 不接受 --owner-token")
        return 1
    if args.action in {"init-owner", "migrate-legacy"}:
        bad = _unused_common_args(
            args,
            allow_yes=True,
            allow_pending_map=args.action == "migrate-legacy",
        )
        if bad:
            _err(f"FATAL: {args.action} 不接受这些参数: {', '.join(bad)}")
            return 1
        from codev_platform.ops.reindex_admin import cmd_reindex_admin

        return cmd_reindex_admin(args)
    destructive_prune = args.action == "prune-stale" and bool(getattr(args, "yes", False))
    destructive_break = args.action == "break-lease" and bool(getattr(args, "yes", False))
    worker_action = args.action in {"worker", "drain-once"}
    q = None
    if not worker_action and not (destructive_prune or destructive_break):
        from codev_platform.reindex import open_default_queue
        from codev_platform.reindex.producer_route import LocalHookRelayRequired

        try:
            q = open_default_queue(
                fail_soft=args.action != "enqueue",
            )
        except LocalHookRelayRequired as exc:
            _err(f"FATAL: {exc!s}")
            return 1

    if args.action == "enqueue":
        pid = args.project
        if not pid:
            try:
                pid = resolve_local()
            except ProjectIdError as exc:
                _err(f"FATAL: 未给 project 且无法从 cwd 解析: {exc!s}")
                return 1
        try:
            pid = validate(pid)
        except ProjectIdError as exc:
            _err(f"FATAL: {exc!s}")
            return 1
        kinds = _resolve_kinds(args.kind)
        if kinds is None:
            return 1
        pull_policy = _normalize_pull_policy(getattr(args, "pull_policy", "never"))
        if pull_policy is None:
            _err("FATAL: --pull-policy 只接受 never | ff-only")
            return 1
        try:
            from codev_platform.reindex.target_commit import (
                require_target_commit,
                resolve_project_head,
            )

            requested = getattr(args, "target_commit", None)
            target_commit = (
                require_target_commit(requested)
                if requested is not None
                else resolve_project_head(pid)
            )
        except Exception as exc:  # noqa: BLE001 - CLI 只输出安全错误类别
            _err(f"FATAL: 无法解析目标提交: {type(exc).__name__}")
            return 1
        meta = JobMeta(
            source="manual",
            pull_policy=pull_policy,
            target_commit=target_commit,
        )
        for k in kinds:
            q.enqueue(pid, k, meta=meta)
        _out(f"enqueued: {pid} -> {', '.join(kinds)}  (worker 会串行消费)")
        return 0

    if args.action == "status":
        import time as _time
        from codev_platform.reindex import status as reindex_status
        bad = _unused_common_args(args, allow_older_than=True)
        if bad:
            _err(f"FATAL: status 不接受这些参数: {', '.join(bad)}")
            return 1
        older_than_sec = int(getattr(args, "older_than_sec", None) or _STALE_PENDING_SEC)
        if older_than_sec < 0:
            _err("FATAL: --older-than-sec 不能为负数")
            return 1
        now = _time.time()
        snapshot = q.snapshot()
        summary = reindex_status.summarize(queue=q, now=now, stale_pending_sec=older_than_sec)
        worker = summary.get("worker") or {}
        if summary.get("running"):
            _out(
                "worker running: yes  "
                f"pid={worker.get('pid')} mode={worker.get('mode') or '?'} "
                f"execution_mode={worker.get('execution_mode') or '?'} "
                f"heartbeat_age={_format_age(worker.get('heartbeat_age_sec'))}"
            )
        else:
            tail = f"last_pid={worker.get('pid')}" if worker.get("pid") else "no state"
            if worker.get("exit_reason"):
                tail += f" exit={worker.get('exit_reason')}"
            _out(f"worker running: no   {tail}")
        _out(
            f"phase: {summary.get('phase') or '?'}  "
            f"phase_age={_format_age(summary.get('phase_age_sec'))}  "
            f"active_job={summary.get('active_job') or '-'}"
        )
        _out(
            f"last_result: {reindex_status._result_brief(summary.get('last_result')) or '-'}  "
            f"recommended_action={summary.get('recommended_action') or 'wait'}"
        )
        _out(f"queue backend: {summary.get('queue_backend') or type(q).__name__}")
        _out(
            "counts: "
            f"pending={summary.get('pending_count', 0)} "
            f"active={summary.get('active_count', 0)} "
            f"results={summary.get('results_count', 0)} "
            f"dirty_after_active={summary.get('dirty_after_active_count', 0)} "
            f"expired_active={summary.get('expired_active_count', 0)}"
        )
        if not snapshot.pending and not snapshot.active and not snapshot.expired_active:
            _out("队列空")
            return 0
        stale = [] if summary.get("running") else _stale_jobs(
            snapshot.pending, now=now, older_than_sec=older_than_sec)
        active_keys = {job.key for job in snapshot.active}
        oldest = summary.get("oldest_pending_age_sec")
        _out(f"oldest pending age: {_format_age(oldest)}")
        suffix = f"  ⚠ {len(stale)} 个 STALE({_stale_label(older_than_sec)} 未认领, 疑孤儿)" if stale else ""
        if snapshot.pending and not summary.get("running"):
            suffix += "  ⚠ worker not running"
        _out(f"待办 {len(snapshot.pending)}:" + suffix)
        for j in snapshot.pending:
            age = int(now - j.enqueued_at) if j.enqueued_at else -1
            flags = []
            if j in stale:
                flags.append("STALE")
            if j.key in active_keys:
                flags.append("dirty-after-active")
            flag = f" ⚠{'/'.join(flags)}" if flags else ""
            _out(f"  {j.key}  (age {age}s){flag}")
        if snapshot.active:
            _out(f"active {len(snapshot.active)}:")
            for j in snapshot.active:
                age = int(now - j.enqueued_at) if j.enqueued_at else -1
                flag = " dirty-after-active" if j.key in {p.key for p in snapshot.pending} else ""
                _out(f"  {j.key}  (age {age}s){flag}")
        if snapshot.expired_active:
            _out(f"expired active {len(snapshot.expired_active)}:")
            for j in snapshot.expired_active:
                age = int(now - j.enqueued_at) if j.enqueued_at else -1
                _out(f"  {j.key}  (age {age}s)")
        return 0

    if args.action == "drain-once":
        return _run_permitted_worker_action(args, _cmd_drain_once)

    if args.action == "prune-stale":
        if destructive_prune:
            return _run_confirmed_queue_mutation(args, _cmd_prune_stale)
        return _cmd_prune_stale(args, q)

    if args.action == "break-lease":
        if destructive_break:
            return _run_confirmed_queue_mutation(args, _cmd_break_lease)
        return _cmd_break_lease(args, q)

    if args.action == "worker":
        # 常驻 worker 在 marker 内只会待命；真实写许可由每一轮 loop 单独持有。
        return _cmd_worker(args)

    _err(f"unknown action: {args.action}")
    return 1
