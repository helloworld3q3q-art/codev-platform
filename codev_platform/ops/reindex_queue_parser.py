"""reindex 队列 CLI 的参数注册，避免管理动作实现与参数表耦合。"""
from __future__ import annotations

import argparse


def register(subparsers) -> None:
    """注册 reindex-queue 薄 CLI 的兼容参数表。"""
    from codev_platform.ops.reindex_queue import cmd_reindex_queue

    rq = subparsers.add_parser(
        "reindex-queue",
        help="reindex 写队列 (enqueue 生产 / status 看待办 / drain-once / worker 消费)",
    )
    rq.add_argument(
        "action",
        choices=[
            "enqueue",
            "status",
            "worker",
            "drain-once",
            "prune-stale",
            "break-lease",
            "init-owner",
            "migrate-legacy",
        ],
    )
    rq.add_argument(
        "project",
        nargs="?",
        default=argparse.SUPPRESS,
        help="enqueue/prune-stale: 目标 project_id (enqueue 不给则从 cwd .claude/project.json 解析)",
    )
    rq.add_argument(
        "--project",
        dest="project",
        default=None,
        help="enqueue/prune-stale/break-lease: 目标 project_id",
    )
    rq.add_argument("--kind", default="all", help="enqueue/prune-stale: chroma | codegraph | all (默认 all)")
    rq.add_argument("--pull-policy", default="never", help="enqueue: never | ff-only (默认 never)")
    rq.add_argument("--target-commit", default=None, help="enqueue: 完整小写 Git OID；不传则解析项目主仓 HEAD")
    rq.add_argument("--older-than-sec", type=int, default=None, help="status/prune-stale/break-lease: 阈值秒数 (默认 300)")
    rq.add_argument("--yes", action="store_true", help="prune-stale/break-lease/init-owner/migrate-legacy: 真正执行; 默认只演练")
    rq.add_argument("--pending-map", default=None, help="migrate-legacy: legacy pending 的严格 JSON 映射文件")
    rq.add_argument("--force-file", action="store_true", help="prune-stale --yes: file 队列确认 worker 已停后才允许删除")
    rq.add_argument("--idle-exit-sec", type=float, default=None, dest="idle_exit_sec", help="worker: 队列空闲 N 秒后退出; 不传则保持常驻")
    rq.add_argument("--heartbeat-sec", type=float, default=None, dest="heartbeat_sec", help="worker: heartbeat/status 更新间隔秒数")
    rq.add_argument("--owner-token", default=None, help=argparse.SUPPRESS)
    rq.add_argument(
        "--require-execution-mode",
        choices=["isolated", "legacy"],
        default=None,
        help=argparse.SUPPRESS,
    )
    rq.set_defaults(func=cmd_reindex_queue)


__all__ = ["register"]
