"""`codev-platform agent serve` -- 起 agent HTTP 服务(FastAPI via uvicorn).

agent 重依赖(fastapi/uvicorn/anthropic)在可选 extra `[agent]`,未装时给清晰提示。
"""
from __future__ import annotations

import argparse


def cmd_agent(args: argparse.Namespace) -> int:
    if args.action != "serve":
        print(f"未知 action: {args.action}(支持: serve)")
        return 2
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("缺少 agent 依赖。先装:  pip install -e .[agent]")
        return 1
    # 延迟到此 import service,避免没装 extra 时整个 CLI 挂掉
    uvicorn.run(
        "codev_platform.agent.service:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("agent", help="agent HTTP 服务(只读代码理解)")
    p.add_argument("action", choices=["serve"], help="serve = 起 HTTP 服务")
    p.add_argument("--host", default="127.0.0.1", help="监听地址(默认本地)")
    p.add_argument("--port", type=int, default=8848, help="端口(默认 8848)")
    p.add_argument("--reload", action="store_true", help="代码热重载(开发用)")
    p.set_defaults(func=cmd_agent)
