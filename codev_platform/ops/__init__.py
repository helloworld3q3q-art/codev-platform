"""codev_platform.ops -- cross-platform dev/ops orchestration (replaces the .ps1 layer).

Each submodule (health, reindex, hooks) exposes ``register(subparsers)`` to add its
CLI subcommand(s). ``register_all`` wires them into the top-level parser so the main
cli.py needs only a single call -- submodules never edit cli.py (parallel-safe).
"""
from __future__ import annotations


def register_all(subparsers) -> None:
    """Import each ops submodule and let it register its subcommands.

    Missing/in-progress submodules are skipped gracefully so partial work doesn't
    break the CLI during the parallel build.
    """
    for modname in ("reindex", "reindex_queue", "webhook", "gateway", "health", "hooks", "agent", "codegraph", "memory_db", "backup", "bootstrap"):
        try:
            mod = __import__(f"codev_platform.ops.{modname}", fromlist=["register"])
        except Exception:
            continue
        reg = getattr(mod, "register", None)
        if callable(reg):
            reg(subparsers)
