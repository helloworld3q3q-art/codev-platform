"""`codev-platform memory <init-db|doctor>` —— 平台 PG 受控迁移入口与 memory 体检。

安全红线(B6 收尾):
- 全程 lazy import psycopg / SQLAlchemy / Alembic；DDL 只由事务化迁移协调器执行。
- **绝不**建 database（需管理员预建）；禁止 Store 在运行期建表或人工 ``stamp head``。
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


# memory doctor 的关键表清单；完整平台结构由迁移协调器指纹验证。
_EXPECTED_TABLES = ("agent_sessions", "agent_messages", "memory_entries")


def _cmd_init_db(cfg) -> int:
    dsn = _require_dsn(cfg)
    if dsn is None:
        return 1
    stores = _try_import_store()
    if stores is None:
        return 1

    _out("memory init-db: 执行平台 PG 事务化 Alembic 迁移，不创建 database。")
    _out("  存量无版本库会先做隔离参考 schema 指纹分类，禁止人工 stamp head。")
    engine = None
    try:
        from codev_platform.web.db.engine import make_engine
        from codev_platform.web.db.migration_postgres import migrate_postgres

        engine = make_engine(dsn)
        report = migrate_postgres(engine)
    except Exception as error:  # noqa: BLE001 - 不回显可能携带 DSN 的底层异常
        _err(f"FATAL: 平台 PG 迁移失败（{type(error).__name__}）。")
        _err("  数据库会保持原事务状态；请检查管理员权限、维护窗口和 schema 漂移。")
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    _out(f"OK: 平台 PG 已迁移到 {report.head_revision}。")
    if report.legacy_adopted:
        _out(f"  已从精确匹配的存量 revision 接管：{report.adopted_revision}")
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

    if dsn is None:
        _out("修复指引: 配好 memory.pg_dsn 后重跑 doctor; 再跑 `memory init-db` 建表。")
        return 1

    # 3. 能否连通 + 关键表是否存在
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


def _cmd_import_md(args: argparse.Namespace) -> int:
    """把 *.md 人肉记忆导入 PG memory(默认 dry-run, --apply 真写)。逻辑在 agent.memory_import。"""
    from pathlib import Path

    from codev_platform.agent.memory_import import run_import
    return run_import(
        Path(args.path).resolve(), scope=args.scope, scope_ref=args.scope_ref,
        owner=args.owner, apply=args.apply, echo=_out,
    )


def _cmd_list(args: argparse.Namespace) -> int:
    """可见性 CLI(P3):列某作用域 active 记忆。personal 自动用本机 user(隐私:只看自己的)。

    org_id 取 env CODEV_ORG_ID > 'default'。store 按 (org_id, scope, scope_ref) 物理隔离 →
    `--scope org` 绝不返回 personal 条(personal 对 org 不可见的断言由 store schema 保证)。
    """
    import os

    from codev_platform.agent import deps
    from codev_platform.core import identity as _id
    store = deps.get_memory_store()
    if store is None:
        _err("memory 未启用(未配 memory.pg_dsn)。")
        return 2
    org_id = os.environ.get("CODEV_ORG_ID", "default")
    if args.scope == "personal":
        scope_ref = _id.resolve_local()  # 只看本机 user 自己的 personal(隐私)
    else:
        scope_ref = args.scope_ref
        if not scope_ref:
            _err("非 personal 作用域需 --scope-ref(org='org' / project=project_id / team=team_id)。")
            return 2
    entries = store.list_scope(args.scope, scope_ref, org_id=org_id, limit=args.limit)
    _out(f"{args.scope}/{scope_ref} (org={org_id}) 现有 {len(entries)} 条 active 记忆:")
    for e in entries:
        rl = " [RL]" if e.is_redline else ""
        _out(f"  {e.id[:8]} [{e.kind or '-'}]{rl} {e.topic_key or '-'}: {e.content[:70]}")
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    if args.action == "import-md":
        return _cmd_import_md(args)
    if args.action == "list":
        return _cmd_list(args)
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
        help="memory PG 库管理 (init-db 建表 / doctor 体检 / import-md 导入 .md 记忆)",
    )
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("init-db", help="幂等建 memory PG schema 表 (不建 database)")
    sub.add_parser("doctor", help="memory PG 连通 + schema 体检")
    imp = sub.add_parser("import-md", help="导入 feedback_*.md / reference_*.md 到 PG memory")
    imp.add_argument("path", help="记忆 .md 源目录")
    imp.add_argument("--scope", default="org", help="org | team | project | personal(默认 org)")
    imp.add_argument("--scope-ref", dest="scope_ref", default="org",
                     help="作用域 ref:project=project_id / personal=user_id(默认 org)")
    imp.add_argument("--owner", default="local", help="owner_user_id(默认 local)")
    imp.add_argument("--apply", action="store_true", help="真写入 PG(默认 dry-run)")
    lst = sub.add_parser("list", help="可见性:列某作用域 active 记忆(personal 只看本机自己的)")
    lst.add_argument("--scope", default="personal",
                     help="org | team | project | personal(默认 personal)")
    lst.add_argument("--scope-ref", dest="scope_ref", default="",
                     help="作用域 ref:org='org' / project=project_id / team=team_id(personal 自动用本机 user)")
    lst.add_argument("--limit", type=int, default=100, help="返回上限(默认 100)")
    p.set_defaults(func=cmd_memory)
