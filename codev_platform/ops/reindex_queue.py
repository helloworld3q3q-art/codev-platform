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

from codev_platform.core.project_id import ProjectIdError, resolve_local, validate

_STALE_PENDING_SEC = 300   # status: 待办 > 5min 仍未被认领 → 标 STALE(疑孤儿: 无 worker 白名单覆盖)


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


def _format_age(seconds: int | None) -> str:
    return "unknown" if seconds is None else f"{seconds}s"


def _print_worker_status() -> dict:
    from codev_platform.reindex import supervisor
    st = supervisor.worker_status()
    if st.get("running"):
        tail = (
            f"pid={st.get('pid')} heartbeat_age={_format_age(st.get('heartbeat_age_sec'))} "
            f"mode={st.get('mode') or '?'}"
        )
        if st.get("last_job"):
            tail += f" last_job={st.get('last_job')}:{st.get('last_job_status') or '?'}"
        _out(f"worker running: yes  {tail}")
    else:
        tail = f"last_pid={st.get('pid')}" if st.get("pid") else "no state"
        if st.get("exit_reason"):
            tail += f" exit={st.get('exit_reason')}"
        _out(f"worker running: no   {tail}")
    return st


def _unused_common_args(args: argparse.Namespace, *,
                        allow_project: bool = False,
                        allow_kind: bool = False,
                        allow_prune_flags: bool = False,
                        allow_older_than: bool = False,
                        allow_worker_flags: bool = False) -> list[str]:
    bad: list[str] = []
    if not allow_project and getattr(args, "project", None):
        bad.append("project")
    if not allow_kind and getattr(args, "kind", "all") != "all":
        bad.append("--kind")
    if not allow_prune_flags:
        if getattr(args, "yes", False):
            bad.append("--yes")
        if getattr(args, "force_file", False):
            bad.append("--force-file")
    if not allow_older_than and getattr(args, "older_than_sec", _STALE_PENDING_SEC) != _STALE_PENDING_SEC:
        bad.append("--older-than-sec")
    if not allow_worker_flags:
        if getattr(args, "idle_exit_sec", None) is not None:
            bad.append("--idle-exit-sec")
        if getattr(args, "heartbeat_sec", None) is not None:
            bad.append("--heartbeat-sec")
    return bad


def cmd_reindex_queue(args: argparse.Namespace) -> int:
    from codev_platform.reindex import open_default_queue, ReindexWorker
    destructive_prune = args.action == "prune-stale" and bool(getattr(args, "yes", False))
    q = open_default_queue(fail_soft=not destructive_prune)

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
        for k in kinds:
            q.enqueue(pid, k)
        _out(f"enqueued: {pid} -> {', '.join(kinds)}  (worker 会串行消费)")
        return 0

    if args.action == "status":
        import time as _time
        bad = _unused_common_args(args, allow_older_than=True)
        if bad:
            _err(f"FATAL: status 不接受这些参数: {', '.join(bad)}")
            return 1
        older_than_sec = int(getattr(args, "older_than_sec", _STALE_PENDING_SEC))
        if older_than_sec < 0:
            _err("FATAL: --older-than-sec 不能为负数")
            return 1
        jobs = q.peek()   # 只读, 不认领 —— pending() 在 PG 后端有副作用(原子认领+锁租约), status 不能用
        st = _print_worker_status()
        _out(f"queue backend: {type(q).__name__}")
        if not jobs:
            _out("队列空")
            return 0
        now = _time.time()
        # STALE 标记: 长时间 pending 且 worker 不运行 = 可能孤儿。FileSpoolQueue 没有 running
        # lease,worker 正在跑长任务时 marker 仍会出现在 peek() 里,不能误报"未认领"。
        stale = [] if st.get("running") else _stale_jobs(jobs, now=now, older_than_sec=older_than_sec)
        enqueued_times = [j.enqueued_at for j in jobs if j.enqueued_at is not None]
        oldest = max(0, int(now - min(enqueued_times))) if enqueued_times else -1
        _out(f"oldest pending age: {oldest}s")
        suffix = f"  ⚠ {len(stale)} 个 STALE({_stale_label(older_than_sec)} 未认领, 疑孤儿)" if stale else ""
        if jobs and not st.get("running"):
            suffix += "  ⚠ worker not running"
        _out(f"待办 {len(jobs)}:" + suffix)
        for j in jobs:
            age = int(now - j.enqueued_at) if j.enqueued_at else -1
            flag = " ⚠STALE" if j in stale else ""
            _out(f"  {j.key}  (age {age}s){flag}")
        return 0

    if args.action == "drain-once":
        bad = _unused_common_args(args)
        if bad:
            _err(f"FATAL: drain-once 不接受这些参数: {', '.join(bad)}")
            return 1
        from codev_platform.core.config import load_config
        from codev_platform.reindex import supervisor
        cfg = load_config()
        owner_token = getattr(args, "owner_token", None) or supervisor.new_owner_token()
        with supervisor.acquire_run_lock(owner_token) as acquired:
            if not acquired:
                _err("FATAL: reindex worker already running; drain-once refused")
                return 1
            supervisor.record_worker_start(owner_token, mode="drain-once")

            def _heartbeat() -> None:
                supervisor.record_heartbeat(owner_token)

            def _job_event(job, status) -> None:
                supervisor.record_job_event(owner_token, job, status)

            try:
                worker = ReindexWorker(q, cfg, on_heartbeat=_heartbeat, on_job_event=_job_event)
                settings = supervisor.worker_settings(cfg)
                with supervisor.heartbeat_thread(owner_token, settings.heartbeat_sec):
                    n = worker.drain_once()
                    _out(f"drain-once processed={n}")
                supervisor.record_worker_exit(owner_token, "drain-once")
                return 0
            except Exception as exc:
                supervisor.record_worker_exit(owner_token, "error", str(exc)[:200])
                raise

    if args.action == "prune-stale":
        import time as _time
        older_than_sec = int(getattr(args, "older_than_sec", _STALE_PENDING_SEC))
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
        stale = _stale_jobs(q.peek(), now=now, older_than_sec=older_than_sec,
                            project_id=project_id, kinds=kinds)
        if not stale:
            _out(f"无 STALE 待清理({_stale_label(older_than_sec)})")
            return 0
        _out(("DRY-RUN " if not args.yes else "") +
             f"STALE {len(stale)} 个({_stale_label(older_than_sec)}):")
        for j in stale:
            age = int(now - j.enqueued_at)
            _out(f"  {j.key}  (age {age}s)")
        if not args.yes:
            _out("未删除任何任务; 加 --yes 才会清理")
            return 0
        from codev_platform.reindex.queue import FileSpoolQueue
        if isinstance(q, FileSpoolQueue) and not args.force_file:
            _err("FATAL: file 队列无 running lease, 为避免误删正在跑的本机任务, "
                 "请先确认 worker 已停止, 再加 --force-file")
            return 1
        discard = getattr(q, "discard", None)
        if not callable(discard):
            _err(f"FATAL: 当前队列后端 {type(q).__name__} 不支持 discard")
            return 1
        removed = 0
        kept = 0
        for j in stale:
            if discard(j):
                removed += 1
            else:
                kept += 1
        _out(f"清理完成: removed={removed}, kept={kept}(peek 后被重新入队/状态变化)")
        return 0

    if args.action == "worker":
        import asyncio
        from codev_platform.core.config import load_config
        from codev_platform.reindex import supervisor
        bad = _unused_common_args(args, allow_worker_flags=True)
        if bad:
            _err(f"FATAL: worker 不接受这些参数: {', '.join(bad)}")
            return 1
        cfg = load_config()
        owner_token = getattr(args, "owner_token", None) or supervisor.new_owner_token()
        heartbeat_sec = getattr(args, "heartbeat_sec", None)
        if heartbeat_sec is None:
            heartbeat_sec = supervisor.worker_settings(cfg).heartbeat_sec
        idle_exit_sec = getattr(args, "idle_exit_sec", None)
        if float(heartbeat_sec) <= 0:
            _err("FATAL: --heartbeat-sec 必须大于 0")
            return 1
        if idle_exit_sec is not None and float(idle_exit_sec) <= 0:
            _err("FATAL: --idle-exit-sec 必须大于 0")
            return 1
        short_lived = idle_exit_sec is not None
        with supervisor.acquire_run_lock(owner_token) as acquired:
            if not acquired:
                _out("worker 已在运行, 本进程退出")
                return 0
            supervisor.record_worker_start(
                owner_token,
                mode="short" if short_lived else "forever",
                idle_exit_sec=float(idle_exit_sec) if short_lived else None,
                heartbeat_sec=float(heartbeat_sec),
            )

            def _heartbeat() -> None:
                supervisor.record_heartbeat(owner_token)

            def _job_event(job, status) -> None:
                supervisor.record_job_event(owner_token, job, status)

            worker = ReindexWorker(q, cfg, on_heartbeat=_heartbeat, on_job_event=_job_event)
            try:
                exit_reason = None
                with supervisor.heartbeat_thread(owner_token, float(heartbeat_sec)):
                    if short_lived:
                        asyncio.run(worker.run_until_idle(float(idle_exit_sec), float(heartbeat_sec)))
                        exit_reason = "idle"
                    else:
                        asyncio.run(worker.run_forever())
                if exit_reason:
                    supervisor.record_worker_exit(owner_token, exit_reason)
            except KeyboardInterrupt:
                supervisor.record_worker_exit(owner_token, "keyboard_interrupt")
                _out("worker 退出")
            except Exception as exc:
                supervisor.record_worker_exit(owner_token, "error", str(exc)[:200])
                raise
            return 0

    _err(f"unknown action: {args.action}")
    return 1


def register(subparsers) -> None:
    rq = subparsers.add_parser(
        "reindex-queue",
        help="reindex 写队列 (enqueue 生产 / status 看待办 / drain-once / worker 消费)",
    )
    rq.add_argument("action", choices=["enqueue", "status", "worker", "drain-once", "prune-stale"])
    rq.add_argument("project", nargs="?", default=None,
                    help="enqueue/prune-stale: 目标 project_id (enqueue 不给则从 cwd .claude/project.json 解析)")
    rq.add_argument("--kind", default="all",
                    help="enqueue/prune-stale: chroma | codegraph | all (默认 all)")
    rq.add_argument("--older-than-sec", type=int, default=_STALE_PENDING_SEC,
                    help="status/prune-stale: STALE 阈值秒数 (默认 300)")
    rq.add_argument("--yes", action="store_true",
                    help="prune-stale: 真正删除; 默认只 dry-run 列出")
    rq.add_argument("--force-file", action="store_true",
                    help="prune-stale --yes: file 队列确认 worker 已停后才允许删除")
    rq.add_argument("--idle-exit-sec", type=float, default=None, dest="idle_exit_sec",
                    help="worker: 队列空闲 N 秒后退出; 不传则保持常驻")
    rq.add_argument("--heartbeat-sec", type=float, default=None, dest="heartbeat_sec",
                    help="worker: heartbeat/status 更新间隔秒数")
    rq.add_argument("--owner-token", default=None, help=argparse.SUPPRESS)
    rq.set_defaults(func=cmd_reindex_queue)
