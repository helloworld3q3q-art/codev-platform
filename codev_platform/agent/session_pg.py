"""SqlSessionStore —— SessionStore 的 PostgreSQL 实现(memory M2:会话跨重启持久化)。

设计要点(扛多人 / 一人多窗口并发):
- 主键 (org_id, user_id, session_id):org→user 物理隔离,所有读写带这三者(plan §3.4)。
- 消息顺序用 BIGSERIAL `id` 自增 + 按 id 排序,**不在应用层算 seq**(两窗口并发会 race)。
- 连接池(psycopg_pool):每请求独立连接,单连接非线程安全由池兜。
- append 一个短事务:user+assistant 一对要么都进要么都不进(不留"有问无答")。
- org_id 现填 'default'(单 org 期);M5 多 org 时传真实 org_id,表无需改。

依赖 psycopg[binary,pool](在 [agent] extra)。schema 首次连接幂等建(CREATE IF NOT EXISTS)。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict

from codev_platform.agent.brain import Message, ToolCall
from codev_platform.agent.session import SessionStore

_DEFAULT_ORG = "default"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_sessions (
  org_id     TEXT NOT NULL DEFAULT 'default',
  user_id    TEXT NOT NULL,
  session_id TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id, session_id)
);
CREATE TABLE IF NOT EXISTS agent_messages (
  id         BIGSERIAL PRIMARY KEY,          -- 自增 = 插入顺序,免应用层 seq race
  org_id     TEXT NOT NULL DEFAULT 'default',
  user_id    TEXT NOT NULL,
  session_id TEXT NOT NULL,
  role       TEXT NOT NULL,                  -- user | assistant | tool
  content    TEXT,
  payload    JSONB NOT NULL DEFAULT '{}',    -- tool_calls / tool_call_id / extra(结构不固定)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agent_msg_session
  ON agent_messages (org_id, user_id, session_id, id);
"""


def _msg_to_payload(m: Message) -> str:
    """Message 的非列字段(tool_calls/tool_call_id/extra)序列化进 JSONB。"""
    return json.dumps({
        "tool_calls": [asdict(tc) for tc in m.tool_calls],
        "tool_call_id": m.tool_call_id,
        "extra": m.extra or {},
    }, ensure_ascii=False)


def _row_to_msg(role: str, content: str | None, payload: dict) -> Message:
    payload = payload or {}
    return Message(
        role=role,
        content=content,
        tool_calls=[ToolCall(id=tc["id"], name=tc["name"], args=tc.get("args") or {})
                    for tc in (payload.get("tool_calls") or [])],
        tool_call_id=payload.get("tool_call_id"),
        extra=payload.get("extra") or {},
    )


class SqlSessionStore(SessionStore):
    """PG 持久化会话存储。org_id 现固定 'default'(单 org 期),多 org 时构造传入。"""

    def __init__(self, dsn: str, read_dsn: str | None = None, org_id: str = _DEFAULT_ORG,
                 *, min_size: int = 1, max_size: int = 4) -> None:
        """读写分离接缝:
        - dsn      → 写池(new/append/建表),指向主库。
        - read_dsn → 读池(get/has),指向只读副本;**None 或同 dsn 时读=写同一池**
          (单 PG 现状)。将来配只读副本只改 config,本类与上层零改。
        现在不预建副本(那是 infra),只留路由接缝(plan §3.9 留接缝不预建分布式)。
        """
        from psycopg_pool import ConnectionPool  # 延迟导入:没装 [agent] 不影响其它子命令
        self._org = org_id
        # open=False + 首次用时 open:没 PG 的机器 import 不崩;连不上在首次操作时报。
        self._write_pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=False)
        if read_dsn and read_dsn != dsn:
            self._read_pool = ConnectionPool(read_dsn, min_size=min_size, max_size=max_size, open=False)
            self._split = True
        else:
            self._read_pool = self._write_pool  # 单 PG:读写同池
            self._split = False
        self._schema_ready = False

    def _ensure(self) -> None:
        # schema 走写池(主库);若分离,读池(副本)由复制同步,不在副本建表。
        if not self._schema_ready:
            self._write_pool.open()
            if self._split:
                self._read_pool.open()
            with self._write_pool.connection() as conn:
                conn.execute(_SCHEMA)
            self._schema_ready = True

    # ---- 写路径(主库)----

    def new(self, user_id: str) -> str:
        self._ensure()
        sid = uuid.uuid4().hex
        with self._write_pool.connection() as conn:
            conn.execute(
                "INSERT INTO agent_sessions (org_id, user_id, session_id) VALUES (%s, %s, %s)",
                (self._org, user_id, sid),
            )
        return sid

    # ---- 读路径(副本,若配置;否则同主库)----

    def has(self, session_id: str, user_id: str) -> bool:
        self._ensure()
        with self._read_pool.connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM agent_sessions WHERE org_id=%s AND user_id=%s AND session_id=%s",
                (self._org, user_id, session_id),
            ).fetchone()
        return row is not None

    def get(self, session_id: str, user_id: str) -> list[Message]:
        self._ensure()
        with self._read_pool.connection() as conn:
            rows = conn.execute(
                "SELECT role, content, payload FROM agent_messages "
                "WHERE org_id=%s AND user_id=%s AND session_id=%s ORDER BY id",
                (self._org, user_id, session_id),
            ).fetchall()
        return [_row_to_msg(r[0], r[1], r[2]) for r in rows]

    def append(self, session_id: str, user_id: str, *messages: Message) -> None:
        if not messages:
            return
        self._ensure()
        # 写路径:一个短事务,多条消息原子写入(user+assistant 成对,不留半截)。
        with self._write_pool.connection() as conn:
            with conn.transaction():
                for m in messages:
                    conn.execute(
                        "INSERT INTO agent_messages (org_id, user_id, session_id, role, content, payload) "
                        "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
                        (self._org, user_id, session_id, m.role, m.content, _msg_to_payload(m)),
                    )
                conn.execute(
                    "UPDATE agent_sessions SET updated_at=now() "
                    "WHERE org_id=%s AND user_id=%s AND session_id=%s",
                    (self._org, user_id, session_id),
                )
