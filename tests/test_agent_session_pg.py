"""SqlSessionStore 的纯逻辑测试 —— Message <-> JSONB payload 往返(不连真 PG)。

PG 连接/查询的真机验证由用户建库后做(round-trip + 重启持久化);
这里只锁死序列化正确性(tool_calls/tool_call_id/extra 全字段无损往返)。
"""
from __future__ import annotations

import json

from codev_platform.agent.brain import Message, ToolCall
from codev_platform.agent.session_pg import _msg_to_payload, _row_to_msg


def test_plain_message_roundtrip():
    m = Message(role="user", content="hello")
    payload = json.loads(_msg_to_payload(m))
    back = _row_to_msg("user", "hello", payload)
    assert back.role == "user" and back.content == "hello"
    assert back.tool_calls == [] and back.tool_call_id is None and back.extra == {}


def test_assistant_with_tool_calls_roundtrip():
    m = Message(role="assistant", content="t",
                tool_calls=[ToolCall(id="c1", name="echo", args={"v": "x"})],
                extra={"reasoning_content": "rc"})
    payload = json.loads(_msg_to_payload(m))
    back = _row_to_msg("assistant", "t", payload)
    assert len(back.tool_calls) == 1
    tc = back.tool_calls[0]
    assert tc.id == "c1" and tc.name == "echo" and tc.args == {"v": "x"}
    assert back.extra == {"reasoning_content": "rc"}


def test_tool_result_message_roundtrip():
    m = Message(role="tool", content="r1", tool_call_id="c1")
    payload = json.loads(_msg_to_payload(m))
    back = _row_to_msg("tool", "r1", payload)
    assert back.role == "tool" and back.tool_call_id == "c1"


def test_row_to_msg_tolerates_empty_payload():
    back = _row_to_msg("user", "q", {})
    assert back.tool_calls == [] and back.extra == {}


# ---- 读写分离接缝(不连真 PG,只验池路由)----

def _store(dsn, read_dsn=None):
    # 构造需 psycopg_pool(池对象,open=False 不触发连接);缺则跳过(序列化测试不受影响)
    import pytest
    pytest.importorskip("psycopg_pool")
    from codev_platform.agent.session_pg import SqlSessionStore
    return SqlSessionStore(dsn, read_dsn=read_dsn)


def test_single_pg_read_equals_write_pool():
    s = _store("postgresql://x/db")
    assert s._read_pool is s._write_pool and s._split is False  # 单 PG:读写同池


def test_read_replica_splits_pools():
    s = _store("postgresql://primary/db", read_dsn="postgresql://replica/db")
    assert s._read_pool is not s._write_pool and s._split is True  # 配副本:读写分池


def test_same_read_dsn_not_split():
    s = _store("postgresql://x/db", read_dsn="postgresql://x/db")
    assert s._read_pool is s._write_pool and s._split is False  # read_dsn==dsn 视为不分
