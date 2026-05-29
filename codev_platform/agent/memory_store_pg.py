"""SqlMemoryStore —— MemoryStore 的 PostgreSQL 实现(memory M2 核心)。

memory_entries 表(plan §3.2)+ 读写分离接缝(同 session_pg:写主库 / 读副本可选)。
作用域隔离:所有读写带 (org_id, scope, scope_ref)。schema 首次连接幂等建。
"""
from __future__ import annotations

import json
import uuid

from codev_platform.agent.memory_store import MemoryEntry, MemoryStore, _DEFAULT_ORG

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
  id            UUID PRIMARY KEY,
  org_id        TEXT NOT NULL DEFAULT 'default',
  scope         TEXT NOT NULL,                  -- org | team | project | personal
  scope_ref     TEXT NOT NULL,                  -- org='org' / team_id / project_id / user_id
  owner_user_id TEXT NOT NULL,
  content       TEXT NOT NULL,
  kind          TEXT,
  topic_key     TEXT,                            -- 冲突检测键(M3 用)
  is_redline    BOOLEAN NOT NULL DEFAULT FALSE,  -- org 硬约束
  status        TEXT NOT NULL DEFAULT 'active',  -- active | superseded | archived | forgotten
  supersedes    UUID REFERENCES memory_entries(id),
  extra         JSONB NOT NULL DEFAULT '{}',
  ttl_at        TIMESTAMPTZ,                     -- 遗忘:到期自动 archive(M4)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mem_scope
  ON memory_entries (org_id, scope, scope_ref, status);
CREATE INDEX IF NOT EXISTS ix_mem_topic
  ON memory_entries (org_id, topic_key, status);
"""

_COLS = "id, org_id, scope, scope_ref, owner_user_id, content, kind, topic_key, is_redline, status, supersedes, extra"


def _row_to_entry(r: tuple) -> MemoryEntry:
    return MemoryEntry(
        id=str(r[0]), org_id=r[1], scope=r[2], scope_ref=r[3], owner_user_id=r[4],
        content=r[5], kind=r[6], topic_key=r[7], is_redline=r[8], status=r[9],
        supersedes=str(r[10]) if r[10] else None, extra=r[11] or {},
    )


class SqlMemoryStore(MemoryStore):
    def __init__(self, dsn: str, read_dsn: str | None = None, *, min_size: int = 1, max_size: int = 4) -> None:
        """读写分离接缝(同 SqlSessionStore):写走 dsn 主库,读走 read_dsn 副本(None=同池)。"""
        from psycopg_pool import ConnectionPool
        self._write_pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)
        if read_dsn and read_dsn != dsn:
            self._read_pool = ConnectionPool(read_dsn, min_size=min_size, max_size=max_size, open=False)
            self._split = True
        else:
            self._read_pool = self._write_pool
            self._split = False
        self._schema_ready = False

    def _ensure(self) -> None:
        if not self._schema_ready:
            self._write_pool.open()
            if self._split:
                self._read_pool.open()
            with self._write_pool.connection() as conn:
                conn.execute(_SCHEMA)
            self._schema_ready = True

    # ---- 写路径(主库)----

    def write(self, entry: MemoryEntry) -> str:
        self._ensure()
        # 用 str(uuid4) 规范 dashed 形式:与 _row_to_entry 的 str(UUID) 读回形式一致,
        # 保证 write() 返回的 id 能与 list_scope 读回的 id 字符串相等(否则 hex 与 dashed 不等)。
        eid = entry.id or str(uuid.uuid4())
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO memory_entries "
                "(id, org_id, scope, scope_ref, owner_user_id, content, kind, topic_key, "
                " is_redline, status, supersedes, ttl_at, extra) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                (eid, entry.org_id, entry.scope, entry.scope_ref, entry.owner_user_id,
                 entry.content, entry.kind, entry.topic_key, entry.is_redline, entry.status,
                 entry.supersedes, entry.ttl_at, json.dumps(entry.extra or {}, ensure_ascii=False)),
            )
        return eid

    def supersede(self, old_id: str, new_entry: MemoryEntry) -> str:
        """新条目取代旧条目:旧 status→superseded,新条目 supersedes=old_id(留痕,不物删)。

        旧条目须存在且与新条目同 org(防跨 org 串接 supersede 链);找不到 → 抛 ValueError
        回滚事务,不写孤儿新条目。
        """
        self._ensure()
        new_entry.supersedes = old_id
        with self._write_pool.connection() as conn:
            with conn.transaction():
                cur = conn.execute(
                    "UPDATE memory_entries SET status='superseded', updated_at=now() "
                    "WHERE id=%s AND org_id=%s",
                    (old_id, new_entry.org_id),
                )
                if cur.rowcount == 0:
                    raise ValueError(f"supersede 目标不存在或跨 org: id={old_id} org={new_entry.org_id}")
                eid = new_entry.id or str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO memory_entries "
                    "(id, org_id, scope, scope_ref, owner_user_id, content, kind, topic_key, "
                    " is_redline, status, supersedes, ttl_at, extra) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    (eid, new_entry.org_id, new_entry.scope, new_entry.scope_ref, new_entry.owner_user_id,
                     new_entry.content, new_entry.kind, new_entry.topic_key, new_entry.is_redline,
                     new_entry.status, old_id, new_entry.ttl_at,
                     json.dumps(new_entry.extra or {}, ensure_ascii=False)),
                )
        return eid

    def forget(self, entry_id: str) -> bool:
        """显式遗忘:status→forgotten(不物删,recall 只查 active)。"""
        self._ensure()
        with self._write_pool.connection() as conn:
            cur = conn.execute(
                "UPDATE memory_entries SET status='forgotten', updated_at=now() "
                "WHERE id=%s AND status<>'forgotten'",
                (entry_id,),
            )
            return cur.rowcount > 0

    def archive(self, entry_id: str) -> bool:
        """显式归档单条(status→archived)。压缩融合归档原条用(留痕,不物删)。"""
        self._ensure()
        with self._write_pool.connection() as conn:
            cur = conn.execute(
                "UPDATE memory_entries SET status='archived', updated_at=now() "
                "WHERE id=%s AND status='active'",
                (entry_id,),
            )
            return cur.rowcount > 0

    def archive_expired(self, org_id: str | None = None) -> int:
        """TTL 到期批量归档(ttl_at 已过 且 active → archived)。org_id=None 跨全 org。返回条数。"""
        self._ensure()
        sql = ("UPDATE memory_entries SET status='archived', updated_at=now() "
               "WHERE status='active' AND ttl_at IS NOT NULL AND ttl_at < now()")
        params: tuple = ()
        if org_id is not None:
            sql += " AND org_id=%s"
            params = (org_id,)
        with self._write_pool.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount

    # ---- 读路径(副本,若配置)----

    def list_scope(self, scope: str, scope_ref: str, org_id: str = _DEFAULT_ORG,
                   limit: int = 100) -> list[MemoryEntry]:
        """列某作用域的 active 记忆(按 org_id 隔离)。

        防御性排除已过期(ttl_at < now):即便 archive_expired job 还没跑,过期记忆也绝不被
        召回 —— TTL 语义在两次 job 之间也正确。
        """
        self._ensure()
        with self._read_pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLS} FROM memory_entries "
                "WHERE org_id=%s AND scope=%s AND scope_ref=%s AND status='active' "
                "AND (ttl_at IS NULL OR ttl_at > now()) "
                "ORDER BY created_at DESC LIMIT %s",
                (org_id, scope, scope_ref, limit),
            ).fetchall()
        return [_row_to_entry(r) for r in rows]
