"""PgTokenStore —— IDE agent 接入 token 的 PostgreSQL 存储(multi-org server Phase 2)。

闭合"config token 与 PG 用户表脱节"洞(plan §六): config `gateway.tokens` 是静态快照,
web 禁用用户对它无效。本 store 把 token→user 落 PG `agent_tokens` 表, **lookup 时 join
users.status 实时校验** —— web 把 users.status 置 DISABLED 后, 该用户所有 token 下一请求即失效。

职责切分(同 rbac_store_pg 范式):
  - 本模块         PG 取数: 签发(issue) / 吊销(revoke / revoke_user) / 查找(lookup) / 列出(list)。
  - gateway/auth   纯认证逻辑(PgTokenResolver 把本 store 接成 TokenResolver, hash 比对 + Identity 组装)。

构造期不连库(create_engine lazy), 首次用才建连接(同 RbacStore)。表定义收口 web/db/tables.py
单一真值源。token 明文不落库 —— 只存调用方算好的 sha256 hash(同 sessions 表)。
"""
from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from codev_platform.web.db import tables


class PgTokenStore:
    """`agent_tokens` 表的 PG 存储。读写分离接缝同 RbacStore(写主库 / 读副本)。

    也支持直接注入已建好的 engine —— 单测用 sqlite 内存 engine exercise SQL 逻辑(同 RbacStore)。
    """

    def __init__(
        self,
        dsn: str | None = None,
        read_dsn: str | None = None,
        *,
        max_size: int = 4,
        engine: Engine | None = None,
        read_engine: Engine | None = None,
    ) -> None:
        if engine is not None:
            self._write_engine = engine
            self._read_engine = read_engine if read_engine is not None else engine
        else:
            if dsn is None:
                raise ValueError("PgTokenStore 需要 dsn 或 engine 之一")
            from codev_platform.web.db.engine import make_engines

            self._write_engine, self._read_engine = make_engines(dsn, read_dsn, pool_size=max_size)
        self._schema_ready = False

    def ensure_schema(self) -> None:
        """幂等建表(metadata.create_all)。首次用时才连库。"""
        if self._schema_ready:
            return
        tables.metadata.create_all(self._write_engine)
        self._schema_ready = True

    # ---- 写路径(主库)----

    def issue(
        self, token_hash: str, user_id: str, org_id: str,
        *, projects: Any = None, label: str | None = None, expires_at: float | None = None,
    ) -> None:
        """登记一个 token(明文已由调用方 hash)。projects: "*" | list[str] | None。重复 hash 覆盖。"""
        self.ensure_schema()
        proj_json = json.dumps(projects) if projects is not None else None
        values = {
            "token_hash": token_hash, "user_id": user_id, "org_id": org_id,
            "projects": proj_json, "label": label, "status": "ACTIVE", "expires_at": expires_at,
        }
        from codev_platform.agent.rbac_store_pg import _upsert_stmt
        stmt = _upsert_stmt(
            tables.agent_tokens, values,
            index_elements=["token_hash"],
            update_cols=["user_id", "org_id", "projects", "label", "status", "expires_at"],
            dialect_name=self._write_engine.dialect.name,
        )
        with self._write_engine.begin() as conn:
            conn.execute(stmt)

    def revoke(self, token_hash_prefix: str) -> int:
        """吊销 token(status=REVOKED, 不删行留审计)。按完整 hash 或前缀匹配。返回受影响行数。"""
        self.ensure_schema()
        t = tables.agent_tokens
        with self._write_engine.begin() as conn:
            rows = conn.execute(select(t.c.token_hash).where(t.c.status == "ACTIVE")).all()
            victims = [r[0] for r in rows if r[0] == token_hash_prefix or r[0].startswith(token_hash_prefix)]
            if not victims:
                return 0
            conn.execute(update(t).where(t.c.token_hash.in_(victims)).values(status="REVOKED"))
            return len(victims)

    def revoke_user(self, user_id: str) -> int:
        """吊销某 user 的所有 active token(web 禁用/离职一键失效)。返回受影响行数。"""
        self.ensure_schema()
        t = tables.agent_tokens
        with self._write_engine.begin() as conn:
            rows = conn.execute(
                select(t.c.token_hash).where(t.c.user_id == user_id, t.c.status == "ACTIVE")
            ).all()
            if not rows:
                return 0
            conn.execute(
                update(t).where(t.c.user_id == user_id, t.c.status == "ACTIVE").values(status="REVOKED")
            )
            return len(rows)

    # ---- 读路径(副本)----

    def lookup(self, token_hash: str) -> dict[str, Any] | None:
        """按 hash 查 token meta, **join users + orgs 实时校验**: token.status=ACTIVE 且
        user.status=ACTIVE 且 token 所属 org.status=ACTIVE 才返回; 否则 None(= 认证失败)。

        "禁用即失效"的关键: web 置 users.status=DISABLED(停人)或 orgs.status=DISABLED(停整个
        组织)→ 此处 join 过滤掉 → 返回 None。两层都校验(审计 R1: 只 join users 会漏"停 org 但人还
        ACTIVE"→ 该 org 下 token 仍有效)。过期(expires_at)不在此判 —— 交给 gateway.token_expired
        纯函数(与 config token 同一判定路径)。
        """
        self.ensure_schema()
        t = tables.agent_tokens
        u = tables.users
        o = tables.orgs
        with self._read_engine.connect() as conn:
            row = conn.execute(
                select(t.c.user_id, t.c.org_id, t.c.projects, t.c.expires_at)
                .select_from(
                    t.join(u, u.c.user_id == t.c.user_id).join(o, o.c.org_id == t.c.org_id)
                )
                .where(t.c.token_hash == token_hash, t.c.status == "ACTIVE",
                       u.c.status == "ACTIVE", o.c.status == "ACTIVE")
            ).first()
        if row is None:
            return None
        meta: dict[str, Any] = {"user_id": row[0], "org_id": row[1]}
        if row[2] is not None:
            try:
                meta["projects"] = json.loads(row[2])
            except (ValueError, TypeError):
                meta["projects"] = None  # 坏 JSON → 无项目权(安全默认)
        if row[3] is not None:
            meta["expires_at"] = row[3]
        return meta

    def list_tokens(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """列 token(诊断/CLI 用): 不含明文(只有 hash)。user_id 给定则只列该用户。"""
        self.ensure_schema()
        t = tables.agent_tokens
        stmt = select(
            t.c.token_hash, t.c.user_id, t.c.org_id, t.c.projects,
            t.c.label, t.c.status, t.c.expires_at,
        )
        if user_id is not None:
            stmt = stmt.where(t.c.user_id == user_id)
        with self._read_engine.connect() as conn:
            rows = conn.execute(stmt.order_by(t.c.user_id)).all()
        out: list[dict[str, Any]] = []
        for r in rows:
            proj = None
            if r[3] is not None:
                try:
                    proj = json.loads(r[3])
                except (ValueError, TypeError):
                    proj = None
            out.append({
                "token_hash": r[0], "user_id": r[1], "org_id": r[2], "projects": proj,
                "label": r[4], "status": r[5], "expires_at": r[6],
            })
        return out
