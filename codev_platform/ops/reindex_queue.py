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


def cmd_reindex_queue(args: argparse.Namespace) -> int:
    from codev_platform.reindex import open_default_queue, ReindexWorker
    q = open_default_queue()

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
        jobs = q.pending()
        if not jobs:
            _out("队列空")
            return 0
        _out(f"待办 {len(jobs)}:")
        for j in jobs:
            _out(f"  {j.key}")
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
    rq.add_argument("action", choices=["enqueue", "status", "worker"])
    rq.add_argument("project", nargs="?", default=None,
                    help="enqueue: 目标 project_id (不给则从 cwd .claude/project.json 解析)")
    rq.add_argument("--kind", default="all",
                    help="enqueue: chroma | codegraph | cross_link | all (默认 all)")
    rq.set_defaults(func=cmd_reindex_queue)
