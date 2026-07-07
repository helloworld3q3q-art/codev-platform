"""codev_platform.ops.reindex_queue —— reindex 写队列的 CLI 薄壳。

  reindex-queue enqueue [project] [--kind ...]   生产者: 落标记即返回 (hook/webhook/手动)
  reindex-queue status                           看待办
  reindex-queue worker                           跑常驻消费者 (systemd codev-reindex)

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
        older_than_sec = int(getattr(args, "older_than_sec", _STALE_PENDING_SEC))
        if older_than_sec < 0:
            _err("FATAL: --older-than-sec 不能为负数")
            return 1
        jobs = q.peek()   # 只读, 不认领 —— pending() 在 PG 后端有副作用(原子认领+锁租约), status 不能用
        if not jobs:
            _out("队列空")
            return 0
        now = _time.time()
        # STALE 标记: 长时间 pending 仍未被认领 = 可能孤儿(无 worker 白名单覆盖 / worker 没起, 审计 risk #2)。
        stale = _stale_jobs(jobs, now=now, older_than_sec=older_than_sec)
        _out(f"待办 {len(jobs)}:" + (f"  ⚠ {len(stale)} 个 STALE({_stale_label(older_than_sec)} 未认领, 疑孤儿)" if stale else ""))
        for j in jobs:
            age = int(now - j.enqueued_at) if j.enqueued_at else -1
            flag = " ⚠STALE" if j in stale else ""
            _out(f"  {j.key}  (age {age}s){flag}")
        return 0

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
        worker = ReindexWorker(q, load_config())
        try:
            asyncio.run(worker.run_forever())
        except KeyboardInterrupt:
            _out("worker 退出")
        return 0

    _err(f"unknown action: {args.action}")
    return 1


def register(subparsers) -> None:
    rq = subparsers.add_parser(
        "reindex-queue",
        help="reindex 写队列 (enqueue 生产 / status 看待办 / worker 常驻消费)",
    )
    rq.add_argument("action", choices=["enqueue", "status", "worker", "prune-stale"])
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
    rq.set_defaults(func=cmd_reindex_queue)
