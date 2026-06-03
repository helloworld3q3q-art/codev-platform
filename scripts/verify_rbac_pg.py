# -*- coding: utf-8 -*-
r"""RBAC PG 真机验证脚本(M5 ACL 底座闭环)。

前提(用户先做,同 memory M2):
  1. 建库 + role(见 docs/plans/roadmap-2026-05-29/memory-permission-model §3.2c)
  2. config ~/.codev-platform/config.json 填 memory.pg_dsn(RBAC 表与 memory_entries 同库)
  3. 装驱动:pip install -e .[agent](含 psycopg)

跑法(任选有 psycopg 的解释器,平台 venv 无 psycopg 跑不了):
  .venv\Scripts\python.exe scripts\verify_rbac_pg.py
  或  D:\ProgramFiles\Python314\python.exe scripts\verify_rbac_pg.py

它会:连库 → ensure_schema(幂等建七表)→ 造 org/user/member/team/project/grant →
fetch_membership 断言 → compute_visible_scopes 断言 → 清理自造数据(verify-* 前缀)。
全程不动你别的数据。打印 PASS/FAIL。
"""
from __future__ import annotations

import io
import sys
import uuid

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def main() -> int:
    from codev_platform.core.config import load_config, env_or_config
    cfg = load_config()
    dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
    if not dsn:
        print("[ABORT] memory.pg_dsn 未配。先建库 + 在 ~/.codev-platform/config.json 填 memory.pg_dsn")
        return 2
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("[ABORT] 当前解释器没装 psycopg。先 pip install -e .[agent]")
        print(f"        当前解释器: {sys.executable}")
        return 2

    print(f"连接: {dsn.rsplit('@', 1)[-1]}  (解释器 {sys.executable})")
    read_dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN_READ", cfg, "memory.pg_dsn_read")

    from codev_platform.agent.rbac_store_pg import RbacStore
    from codev_platform.core.rbac import compute_visible_scopes, role_allows

    sfx = uuid.uuid4().hex[:8]
    org = f"verify-org-{sfx}"
    org2 = f"verify-org2-{sfx}"
    uid = f"verify-user-{sfx}"
    tid = f"verify-team-{sfx}"
    pid = f"verify-proj-{sfx}"
    pid2 = f"verify-proj2-{sfx}"  # 通过 team 授权的 project
    fails = 0

    store = RbacStore(dsn, read_dsn=read_dsn)

    # ---- 1. ensure_schema(幂等)----
    print("\n[1] ensure_schema 幂等")
    store.ensure_schema()
    store.ensure_schema()  # 第二次不应报错
    _ok("ensure_schema 幂等(七表 CREATE IF NOT EXISTS)")

    # ---- 2. 造身份 + 成员 + 团队 + 项目 + 授权 ----
    print("\n[2] 写入 org/user/member/team/project/grant")
    store.add_org(org, "Verify Org")
    store.add_org(org2, "Verify Org2")
    store.add_user(uid, "Verify User")
    store.add_org_member(org, uid, role="member")
    store.add_team(tid, org, "Verify Team")
    store.add_team_member(tid, uid, role="admin")
    store.upsert_project(pid, org, "Verify Project")       # user 直授
    store.upsert_project(pid2, org, "Verify Project2")     # team 授权
    store.grant_project(pid, uid, "user", role="member")
    store.grant_project(pid2, tid, "team", role="viewer")
    # 幂等复跑一次(ON CONFLICT 不报错 / 不重复)
    store.add_org_member(org, uid, role="member")
    store.grant_project(pid, uid, "user", role="member")
    _ok("写入完成(含幂等复跑)")

    # ---- 3. fetch_membership(user 直授 project)----
    print("\n[3] fetch_membership — user 直授 project")
    m = store.fetch_membership(org, uid, pid)
    chk = (m.org_role == "member"
           and (tid, "admin") in m.teams
           and m.project_role == "member")
    if chk:
        _ok(f"org_role=member, teams={m.teams}, project_role=member")
    else:
        _fail(f"membership 不对: org_role={m.org_role} teams={m.teams} project_role={m.project_role}")
        fails += 1

    # ---- 3b. fetch_membership(经 team 授权的 project)----
    print("\n[3b] fetch_membership — team 授权 project")
    m2 = store.fetch_membership(org, uid, pid2)
    if m2.project_role == "viewer":
        _ok("经 team 命中 project_access → project_role=viewer")
    else:
        _fail(f"team 授权未命中: project_role={m2.project_role}"); fails += 1

    # ---- 3c. 跨 org 不可见 ----
    print("\n[3c] 跨 org 不可见")
    m3 = store.fetch_membership(org2, uid, pid)
    cross_iso = (m3.org_role is None and m3.teams == () and m3.project_role is None)
    if cross_iso:
        _ok("另一个 org 视角:org_role/teams/project_role 全空(跨 org 硬隔离)")
    else:
        _fail(f"跨 org 隔离失效: {m3}"); fails += 1

    # ---- 4. compute_visible_scopes(纯逻辑套真 membership)----
    print("\n[4] compute_visible_scopes")
    scopes = compute_visible_scopes(org, uid, pid, m)
    want = {("org", "org"), ("team", tid), ("project", pid), ("personal", uid)}
    if set(scopes) == want:
        _ok(f"可见作用域 = {scopes}")
    else:
        _fail(f"visible_scopes 不对: got={scopes} want={want}"); fails += 1

    # ---- 4b. role_allows 矩阵抽样 ----
    print("\n[4b] role_allows 矩阵")
    matrix_ok = (role_allows(m.org_role, "read")          # member 可读
                 and role_allows(m.org_role, "write")     # member 可写
                 and not role_allows(m.org_role, "admin") # member 不可 admin
                 and not role_allows(None, "read"))        # 无角色拒
    if matrix_ok:
        _ok("member: read/write 通过, admin 拒; None: 全拒(安全默认)")
    else:
        _fail("role_allows 矩阵不对"); fails += 1

    # ---- 5. 清理自造数据 ----
    print("\n[5] 清理测试数据")
    try:
        from sqlalchemy import delete
        from codev_platform.web.db import tables as _t
        with store._write_engine.begin() as conn:
            conn.execute(delete(_t.project_access).where(_t.project_access.c.project_id.in_([pid, pid2])))
            conn.execute(delete(_t.projects).where(_t.projects.c.project_id.in_([pid, pid2])))
            conn.execute(delete(_t.team_members).where(_t.team_members.c.team_id == tid))
            conn.execute(delete(_t.teams).where(_t.teams.c.team_id == tid))
            conn.execute(delete(_t.org_members).where(_t.org_members.c.user_id == uid))
            conn.execute(delete(_t.users).where(_t.users.c.user_id == uid))
            conn.execute(delete(_t.orgs).where(_t.orgs.c.org_id.in_([org, org2])))
        _ok("已清理 verify-* 测试数据")
    except Exception as ex:  # noqa: BLE001
        print(f"  [WARN] 清理失败(不影响验证结论): {ex}")

    print(f"\n{'='*48}")
    print("结果:" + ("全部通过 PASS RBAC PG 真机验证 OK" if fails == 0 else f"{fails} 项失败 FAIL"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
