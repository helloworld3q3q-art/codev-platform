"""`codev-platform org <action>` —— 组织 / 用户 / 团队 / 项目管理 CLI 薄壳(D13)。

只做参数解析 + 调 RbacStore 写方法, 不含任何权限计算(那在 core/rbac.py)。

  org create <org_id> [--name]                       建 org
  org add-user <user_id> [--name]                    建 user
  org add-member <org_id> <user_id> [--role member]  org 成员授角色
  team create <team_id> --org <org_id> [--name]      建 team(挂 org 下)
  team add-member <team_id> <user_id> [--role member] team 成员授角色
  project set-org <project_id> --org <org_id> [--name] 项目归属 org
  project grant <project_id> <principal> [--kind user|team] [--role member] 授项目访问权
  org list                                           列已建 org + 成员(核对用)

安全红线(同 memory_db / gateway 薄壳):
- lazy import deps.get_rbac_store + rbac_store_pg(顶层 import 在无 psycopg 的平台 venv 会失败)。
- psycopg 缺失 / dsn 未配 → get_rbac_store() 返 None → 友好提示退非 0; 绝不 raise / 不自动 pip / 不建 database。
- 表由 RbacStore.ensure_schema() 幂等建(首次写时), database 须用户照 P4 runbook 手动建。
"""
from __future__ import annotations

import argparse
import sys


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _get_store():
    """取 RbacStore 单例; None(psycopg 缺 / dsn 未配)→ 打印指引返回 None, 调用方退非 0。"""
    from codev_platform.agent.deps import get_rbac_store

    store = get_rbac_store()
    if store is None:
        _err("FATAL: RBAC 存储不可用 —— memory.pg_dsn 未配 或 psycopg 未装。")
        _err("  配本机 ~/.codev-platform/config.json 的 memory.pg_dsn (或 env CODEV_PLATFORM_MEMORY_DSN),")
        _err("  并装 [agent] extra (psycopg)。建库 SQL 见 P4 runbook; 表由本命令首次写时幂等建。")
    return store


def cmd_org(args: argparse.Namespace) -> int:
    store = _get_store()
    if store is None:
        return 1

    try:
        if args.action == "create":
            store.add_org(args.target, name=args.name)
            _out(f"OK: org '{args.target}' 已建 (name={args.name or '-'})")
            return 0

        if args.action == "add-user":
            store.add_user(args.target, name=args.name)
            _out(f"OK: user '{args.target}' 已建 (name={args.name or '-'})")
            return 0

        if args.action == "add-member":
            if not args.user:
                _err("FATAL: add-member 需 <org_id> <user_id>")
                return 1
            store.add_org_member(args.target, args.user, role=args.role)
            _out(f"OK: org '{args.target}' 成员 '{args.user}' role={args.role}")
            return 0

        if args.action == "team-create":
            if not args.org:
                _err("FATAL: team create 需 --org <org_id>")
                return 1
            store.add_team(args.target, args.org, name=args.name)
            _out(f"OK: team '{args.target}' 已建 (org={args.org}, name={args.name or '-'})")
            return 0

        if args.action == "team-add-member":
            if not args.user:
                _err("FATAL: team add-member 需 <team_id> <user_id>")
                return 1
            store.add_team_member(args.target, args.user, role=args.role)
            _out(f"OK: team '{args.target}' 成员 '{args.user}' role={args.role}")
            return 0

        if args.action == "project-set-org":
            if not args.org:
                _err("FATAL: project set-org 需 --org <org_id>")
                return 1
            store.upsert_project(args.target, args.org, name=args.name)
            _out(f"OK: project '{args.target}' 归属 org '{args.org}' (name={args.name or '-'})")
            return 0

        if args.action == "project-grant":
            if not args.principal:
                _err("FATAL: project grant 需 <project_id> <principal>")
                return 1
            if args.kind not in ("user", "team"):
                _err("FATAL: --kind 取值 user | team")
                return 1
            store.grant_project(args.target, args.principal, args.kind, role=args.role)
            _out(f"OK: project '{args.target}' 授 {args.kind} '{args.principal}' role={args.role}")
            return 0

        if args.action == "list":
            return _cmd_list(store)
    except Exception as exc:  # noqa: BLE001 — 连库 / 权限 / 外键等运行期错, 友好报告非 0
        _err(f"FATAL: 操作失败 (连库或权限 / 外键问题?): {type(exc).__name__}: {exc}")
        _err("  确认 database 已建 + dsn 用户对该库有 CREATE/写权; 先建 org 再建 team/member。")
        return 1

    _err(f"unknown action: {args.action}")
    return 1


def _cmd_list(store) -> int:
    """列已建 org + 各 org 成员(核对用)。直接走 store 的只读连接池, 不连真库逻辑由 store 负责。"""
    store.ensure_schema()
    with store._read_pool.connection() as conn:  # noqa: SLF001 — 薄壳核对查询, 复用 store 连接池
        orgs = conn.execute("SELECT org_id, name FROM orgs ORDER BY org_id").fetchall()
        if not orgs:
            _out("(无已建 org)")
            return 0
        members = conn.execute(
            "SELECT org_id, user_id, org_role FROM org_members ORDER BY org_id, user_id"
        ).fetchall()
    by_org: dict[str, list[tuple[str, str]]] = {}
    for org_id, user_id, role in members:
        by_org.setdefault(org_id, []).append((user_id, role))
    _out(f"已建 {len(orgs)} 个 org:")
    for org_id, name in orgs:
        _out(f"  {org_id}  (name={name or '-'})")
        for user_id, role in by_org.get(org_id, []):
            _out(f"    - {user_id}  role={role}")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "org",
        help="组织/用户/团队/项目管理 (create / add-user / add-member / team / project / list)",
    )
    sub = p.add_subparsers(dest="_group", required=True)

    p_create = sub.add_parser("create", help="建 org")
    p_create.add_argument("target", help="org_id")
    p_create.add_argument("--name", default=None, help="org 显示名")
    p_create.set_defaults(func=cmd_org, action="create", user=None, principal=None,
                          org=None, kind="user", role="member")

    p_user = sub.add_parser("add-user", help="建 user")
    p_user.add_argument("target", help="user_id")
    p_user.add_argument("--name", default=None, help="用户显示名")
    p_user.set_defaults(func=cmd_org, action="add-user", user=None, principal=None,
                        org=None, kind="user", role="member")

    p_mem = sub.add_parser("add-member", help="org 成员授角色")
    p_mem.add_argument("target", help="org_id")
    p_mem.add_argument("user", help="user_id")
    p_mem.add_argument("--role", default="member", help="admin|member|viewer (默认 member)")
    p_mem.set_defaults(func=cmd_org, action="add-member", name=None, principal=None,
                       org=None, kind="user")

    p_team = sub.add_parser("team", help="团队管理 (create / add-member)")
    team_sub = p_team.add_subparsers(dest="_team", required=True)

    p_tc = team_sub.add_parser("create", help="建 team")
    p_tc.add_argument("target", help="team_id")
    p_tc.add_argument("--org", required=True, help="所属 org_id")
    p_tc.add_argument("--name", default=None, help="team 显示名")
    p_tc.set_defaults(func=cmd_org, action="team-create", user=None, principal=None,
                      kind="user", role="member")

    p_tm = team_sub.add_parser("add-member", help="team 成员授角色")
    p_tm.add_argument("target", help="team_id")
    p_tm.add_argument("user", help="user_id")
    p_tm.add_argument("--role", default="member", help="admin|member|viewer (默认 member)")
    p_tm.set_defaults(func=cmd_org, action="team-add-member", name=None, principal=None,
                      org=None, kind="user")

    p_proj = sub.add_parser("project", help="项目管理 (set-org / grant)")
    proj_sub = p_proj.add_subparsers(dest="_proj", required=True)

    p_ps = proj_sub.add_parser("set-org", help="项目归属 org")
    p_ps.add_argument("target", help="project_id")
    p_ps.add_argument("--org", required=True, help="所属 org_id")
    p_ps.add_argument("--name", default=None, help="项目显示名")
    p_ps.set_defaults(func=cmd_org, action="project-set-org", user=None, principal=None,
                      kind="user", role="member")

    p_pg = proj_sub.add_parser("grant", help="授项目访问权")
    p_pg.add_argument("target", help="project_id")
    p_pg.add_argument("principal", help="user_id 或 team_id")
    p_pg.add_argument("--kind", default="user", help="principal 种类 user|team (默认 user)")
    p_pg.add_argument("--role", default="member", help="admin|member|viewer (默认 member)")
    p_pg.set_defaults(func=cmd_org, action="project-grant", name=None, user=None, org=None)

    p_list = sub.add_parser("list", help="列已建 org + 成员")
    p_list.set_defaults(func=cmd_org, action="list", target=None, user=None, principal=None,
                        name=None, org=None, kind="user", role="member")
