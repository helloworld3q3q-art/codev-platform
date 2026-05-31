"""`codev-platform memory <init-db|doctor>` —— memory PG 库的 schema 初始化 + 体检。

安全红线(B6 收尾):
- 全程 lazy import psycopg / 复用 session_pg.py + memory_store_pg.py 现成幂等建表(不重复造 DDL)。
- 只建**表**(CREATE TABLE IF NOT EXISTS), **绝不**建 database(需超级用户, 由用户照 P4 runbook 手动)。
- psycopg 缺失 / dsn 未配 → 友好提示退非 0, 绝不自动 pip / 不自动建库 (rules §11)。
"""
from __future__ import annotations

import argparse
import sys


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _require_dsn(cfg) -> str | None:
    """取 memory.pg_dsn; 空/未配 → 提示并返回 None(不抛)。"""
    from codev_platform.core.config import get

    dsn = get(cfg, "memory.pg_dsn")
    if not dsn or not str(dsn).strip():
        _err("FATAL: config memory.pg_dsn 未配 —— memory PG 未启用 (会话仅内存)。")
        _err("  填本机 ~/.codev-platform/config.json 的 memory.pg_dsn (或 env CODEV_PLATFORM_MEMORY_DSN)。")
        _err("  建库 SQL 见 P4 runbook; 表由 `memory init-db` 幂等建。")
        return None
    return str(dsn).strip()


def _try_import_store():
    """尝试 import PG store 类(SqlSessionStore / SqlMemoryStore)。

    ImportError(psycopg / psycopg_pool 缺)→ 友好提示返回 None。不 raise, 不自动装。
    """
    try:
        import psycopg_pool  # noqa: F401
        from codev_platform.agent.memory_store_pg import SqlMemoryStore
        from codev_platform.agent.session_pg import SqlSessionStore

        return SqlSessionStore, SqlMemoryStore
    except ImportError:
        _err("FATAL: 缺 psycopg 依赖 (psycopg[binary] + psycopg_pool)。")
        _err(f"  当前解释器: {sys.executable}")
        _err("  装 agent extra (见 P4 runbook): pip install -e '<codev-platform 仓路径>[agent]'")
        _err("  —— 绝不自动安装; 请手动装好后重试。")
        return None


# 关键表清单(来自 session_pg / memory_store_pg 的 _SCHEMA, doctor 据此核查)。
_EXPECTED_TABLES = ("agent_sessions", "agent_messages", "memory_entries")


def _cmd_init_db(cfg) -> int:
    dsn = _require_dsn(cfg)
    if dsn is None:
        return 1
    stores = _try_import_store()
    if stores is None:
        return 1
    SqlSessionStore, SqlMemoryStore = stores

    from codev_platform.core.config import get

    read_dsn = get(cfg, "memory.pg_dsn_read") or None

    _out("memory init-db: 仅建表 (CREATE TABLE IF NOT EXISTS), 不建 database。")
    _out("  注: database 须超级用户手动建 (见 P4 runbook); 这里只在已存在的库里建 schema。")
    # 实例化 + 触发首次 _ensure() 幂等建表 (复用现成 DDL, 不重复造)。
    # session_pg._ensure 建 agent_sessions / agent_messages; memory_store_pg._ensure 建 memory_entries。
    try:
        sess = SqlSessionStore(dsn, read_dsn)
        sess._ensure()
        mem = SqlMemoryStore(dsn, read_dsn)
        mem._ensure()
    except Exception as exc:  # noqa: BLE001 - 连不上/权限不足等运行期错, 友好报告非 0
        _err(f"FATAL: 建表失败 (连库或权限问题?): {type(exc).__name__}: {exc}")
        _err("  确认 database 已建 + dsn 用户对该库有 CREATE 权; 建库 SQL 见 P4 runbook。")
        return 1

    _out("OK: schema 幂等建立完成 (已存在则跳过)。涉及表:")
    for t in _EXPECTED_TABLES:
        _out(f"  - {t}")
    return 0


def _cmd_doctor(cfg) -> int:
    from codev_platform.core.config import get

    ok = True

    # 1. dsn 是否配
    dsn = get(cfg, "memory.pg_dsn")
    if dsn and str(dsn).strip():
        _out("[OK]   memory.pg_dsn 已配")
        dsn = str(dsn).strip()
    else:
        _out("[缺]   memory.pg_dsn 未配 —— 填 config 或 env CODEV_PLATFORM_MEMORY_DSN")
        dsn = None
        ok = False

    # 2. psycopg 是否装
    stores = _try_import_store()
    if stores is None:
        _out("[缺]   psycopg 未装 —— 见上方提示手动装 [agent] extra")
        return 1
    _out("[OK]   psycopg / psycopg_pool 已装")
    SqlSessionStore, SqlMemoryStore = stores

    if dsn is None:
        _out("修复指引: 配好 memory.pg_dsn 后重跑 doctor; 再跑 `memory init-db` 建表。")
        return 1

    # 3. 能否连通 + 关键表是否存在
    read_dsn = get(cfg, "memory.pg_dsn_read") or None
    try:
        import psycopg

        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            ).fetchall()
        present = {r[0] for r in rows}
    except Exception as exc:  # noqa: BLE001
        _out(f"[缺]   连库失败: {type(exc).__name__}: {exc}")
        _out("修复指引: 确认 database 已建 + dsn 主机/端口/账号正确 (建库 SQL 见 P4 runbook)。")
        return 1
    _out("[OK]   连库成功")

    missing = [t for t in _EXPECTED_TABLES if t not in present]
    if missing:
        ok = False
        for t in _EXPECTED_TABLES:
            mark = "缺" if t in missing else "OK"
            _out(f"[{mark}]   表 {t}")
        _out("修复指引: 跑 `codev-platform memory init-db` 幂等建表。")
    else:
        for t in _EXPECTED_TABLES:
            _out(f"[OK]   表 {t}")

    return 0 if ok else 1


def load_config():
    """Thin indirection over core.config.load_config (lazy import, test-patchable)。"""
    from codev_platform.core.config import load_config as _lc

    return _lc()


def cmd_memory(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.action == "init-db":
        return _cmd_init_db(cfg)
    if args.action == "doctor":
        return _cmd_doctor(cfg)
    _err(f"unknown action: {args.action}")
    return 1


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "memory",
        help="memory PG 库管理 (init-db 幂等建表 / doctor 体检; 不建 database, 不自动装依赖)",
    )
    p.add_argument("action", choices=["init-db", "doctor"])
    p.set_defaults(func=cmd_memory)
