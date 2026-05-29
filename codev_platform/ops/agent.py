"""`codev-platform agent serve` -- 起 agent HTTP 服务(FastAPI via uvicorn).

agent 重依赖(fastapi/uvicorn/anthropic)在可选 extra `[agent]`,未装时给清晰提示。
"""
from __future__ import annotations

import argparse
import sys


def cmd_agent(args: argparse.Namespace) -> int:
    if args.action != "serve":
        print(f"未知 action: {args.action}(支持: serve)")
        return 2
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        # 关键:agent extra 要装到"当前运行 codev-platform 的解释器",不一定是仓内 .venv。
        # 报清楚当前解释器,避免装错环境(常见坑:CLI 在系统 python,依赖却装进 .venv)。
        print(
            "缺少 agent 依赖(fastapi/uvicorn/anthropic/openai)。\n"
            f"当前解释器: {sys.executable}\n"
            "给这个解释器装 agent extra:\n"
            f'  "{sys.executable}" -m pip install -e "<codev-platform 仓路径>[agent]"\n'
            "或直接用已装依赖的 venv 起:\n"
            "  <venv>\\Scripts\\python.exe -m codev_platform.cli agent serve",
            file=sys.stderr,
        )
        return 1
    # passthrough 绑非 loopback = 未认证对外开放 → loud WARN(secure-by-default 兜底)
    try:
        import logging
        logging.basicConfig(level=logging.INFO)
        from codev_platform.core.config import load_config
        from codev_platform.gateway.auth import build_authenticator, warn_if_insecure
        warn_if_insecure(build_authenticator(load_config()), args.host)
    except Exception:  # noqa: BLE001 - 警告失败不阻塞起服务
        pass
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
