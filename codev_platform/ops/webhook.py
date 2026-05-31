"""codev_platform.ops.webhook —— webhook 接收器 CLI 薄壳。

  webhook serve [--port N]   跑接收器 (systemd codev-webhook 常驻)
  webhook status             查 /health

只调 codev_platform.webhook.server, 不含逻辑。
"""
from __future__ import annotations

import argparse
import sys


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def cmd_webhook(args: argparse.Namespace) -> int:
    from codev_platform.webhook import server as _server

    if args.action == "serve":
        import asyncio
        try:
            asyncio.run(_server.run_http(args.port))
        except KeyboardInterrupt:
            _out("webhook receiver 退出")
        return 0

    if args.action == "status":
        import urllib.request
        port = args.port or _server.webhook_port()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                _out(f"webhook: OK :{port}  {r.read().decode('utf-8')}")
                return 0
        except Exception as exc:  # noqa: BLE001
            _out(f"webhook: DOWN :{port} ({exc})")
            return 1

    print(f"unknown action: {args.action}", file=sys.stderr)
    return 1


def register(subparsers) -> None:
    wh = subparsers.add_parser("webhook", help="VCS webhook 接收器 (serve 跑常驻 / status 查活)")
    wh.add_argument("action", choices=["serve", "status"])
    wh.add_argument("--port", type=int, default=None, help="覆盖 config.webhook.port")
    wh.set_defaults(func=cmd_webhook)
