"""MemoryStore / SqlMemoryStore 测试 —— 纯逻辑(row->entry, 默认值, 读写分离接缝)。
真 PG 的 write/list/supersede/forget 由用户建库后验;这里锁结构正确性。
"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry, SCOPES
from codev_platform.agent.memory_store_pg import _row_to_entry


def test_scopes_constant():
    assert SCOPES == ("org", "team", "project", "personal")


def test_entry_defaults():
    e = MemoryEntry(id="1", scope="project", scope_ref="p1", owner_user_id="alice", content="x")
    assert e.org_id == "default" and e.status == "active" and e.is_redline is False
    assert e.extra == {} and e.supersedes is None


def test_row_to_entry():
    row = ("uuid-1", "acme", "personal", "alice", "alice", "我喜欢简洁",
           "preference", "style", True, "active", None, {"k": "v"})
    e = _row_to_entry(row)
    assert e.id == "uuid-1" and e.org_id == "acme" and e.scope == "personal"
    assert e.owner_user_id == "alice" and e.is_redline is True and e.extra == {"k": "v"}
    assert e.supersedes is None


def test_row_to_entry_with_supersedes():
    row = ("new", "default", "project", "p1", "bob", "新值", None, None, False,
           "active", "old-id", {})
    e = _row_to_entry(row)
    assert e.supersedes == "old-id"


# ---- 读写分离接缝(需 psycopg, 缺则跳过)----

def test_memory_store_split_routing():
    import pytest
    pytest.importorskip("psycopg_pool")
    from codev_platform.agent.memory_store_pg import SqlMemoryStore
    s1 = SqlMemoryStore("postgresql://x/db")
    assert s1._read_pool is s1._write_pool and s1._split is False
    s2 = SqlMemoryStore("postgresql://primary/db", read_dsn="postgresql://replica/db")
    assert s2._read_pool is not s2._write_pool and s2._split is True
