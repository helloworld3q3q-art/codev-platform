r"""把 *.md 人肉记忆迁进 PG memory store —— 薄 CLI shim,逻辑在 codev_platform.agent.memory_import。

也可用 `codev-platform memory import-md <path> [--scope ...] [--apply]`(wheel 安装通用)。

跑法(需 config.memory.pg_dsn + psycopg):
  # codev-platform 跨项目偏好 → org(默认)
  .venv\Scripts\python.exe scripts\migrate_memory_md.py [--apply]
  # 业务仓 platform 业务专属 → project
  .venv\Scripts\python.exe scripts\migrate_memory_md.py --source D:\WorkSpace\platform\docs\memory \
      --scope project --scope-ref openclaw-stock [--apply]
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from codev_platform.agent.memory_import import run_import


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="记忆 .md 源目录(默认 codev-platform/memory)")
    ap.add_argument("--scope", default="org", help="org | team | project | personal(默认 org)")
    ap.add_argument("--scope-ref", default="org", help="作用域 ref(默认 org)")
    ap.add_argument("--owner", default="local", help="owner_user_id(默认 local)")
    ap.add_argument("--apply", action="store_true", help="真写入 PG(默认 dry-run)")
    args = ap.parse_args()
    source = Path(args.source).resolve() if args.source else Path(__file__).resolve().parent.parent / "memory"
    return run_import(source, scope=args.scope, scope_ref=args.scope_ref,
                      owner=args.owner, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
