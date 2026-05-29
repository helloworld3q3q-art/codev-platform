"""MemoryMaintenance(M4 压缩 + TTL)编排测试 —— 用注入 fuse_fn,纯逻辑不需 PG/LLM。

真 PG 的 TTL 归档 + 压缩端到端在 scripts/verify_memory_pg.py。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from codev_platform.agent.memory_store import MemoryEntry, MemoryStore, _DEFAULT_ORG
from codev_platform.agent.memory_maintenance import MemoryMaintenance


class FakeStore(MemoryStore):
    """内存版,支持 write/list_scope(排除非 active + 过期)/archive/archive_expired。"""

    def __init__(self):
        self._db: dict[str, MemoryEntry] = {}
        self._seq = 0

    def write(self, entry):
        self._seq += 1
        eid = entry.id or f"id{self._seq}"
        entry.id = eid
        self._db[eid] = entry
        return eid

    def list_scope(self, scope, scope_ref, org_id=_DEFAULT_ORG, limit=100):
        now = datetime.now(timezone.utc)
        out = [e for e in self._db.values()
               if e.org_id == org_id and e.scope == scope and e.scope_ref == scope_ref
               and e.status == "active" and (e.ttl_at is None or e.ttl_at > now)]
        return out[:limit]

    def supersede(self, old_id, new_entry):
        return self.write(new_entry)

    def forget(self, entry_id):
        if entry_id in self._db:
            self._db[entry_id].status = "forgotten"
            return True
        return False

    def archive(self, entry_id):
        e = self._db.get(entry_id)
        if e and e.status == "active":
            e.status = "archived"
            return True
        return False

    def archive_expired(self, org_id=None):
        now = datetime.now(timezone.utc)
        n = 0
        for e in self._db.values():
            if e.status == "active" and e.ttl_at is not None and e.ttl_at < now \
               and (org_id is None or e.org_id == org_id):
                e.status = "archived"
                n += 1
        return n


def _e(content, *, scope="personal", ref="alice", topic_key=None, is_redline=False,
       org="default", ttl_at=None, status="active"):
    return MemoryEntry(id="", scope=scope, scope_ref=ref, owner_user_id="alice",
                       content=content, org_id=org, topic_key=topic_key, is_redline=is_redline,
                       ttl_at=ttl_at, status=status)


_CONCAT = lambda cs: "融合:" + " / ".join(cs)  # noqa: E731 — 确定性 fuse_fn 替身


# ---- 压缩 ----

def test_compress_below_threshold_noop():
    s = FakeStore()
    s.write(_e("a", topic_key="t")); s.write(_e("b", topic_key="t"))
    out = MemoryMaintenance(s, min_entries=3).compress_topic("personal", "alice", "t", _CONCAT)
    assert out is None
    assert len(s.list_scope("personal", "alice")) == 2  # 没动


def test_compress_at_threshold_fuses_and_archives():
    s = FakeStore()
    ids = [s.write(_e(c, topic_key="t")) for c in ("a", "b", "c")]
    nid = MemoryMaintenance(s, min_entries=3).compress_topic("personal", "alice", "t", _CONCAT)
    assert nid is not None
    active = s.list_scope("personal", "alice")
    assert len(active) == 1                                  # 原 3 条归档,只剩 summary
    summary = active[0]
    assert summary.kind == "summary" and summary.content == "融合:a / b / c"
    assert set(summary.extra["fused_from"]) == set(ids)      # 留痕指向原条
    assert all(s._db[i].status == "archived" for i in ids)   # 原条 archived 非删


def test_compress_excludes_redline():
    s = FakeStore()
    s.write(_e("a", topic_key="t")); s.write(_e("b", topic_key="t"))
    s.write(_e("红线", topic_key="t", is_redline=True))
    # 非 redline 只 2 条 < 阈值 3 → 不压;redline 不计入
    assert MemoryMaintenance(s, min_entries=3).compress_topic("personal", "alice", "t", _CONCAT) is None
    s.write(_e("c", topic_key="t"))  # 非 redline 凑够 3
    nid = MemoryMaintenance(s, min_entries=3).compress_topic("personal", "alice", "t", _CONCAT)
    assert nid is not None
    contents = {m.content for m in s.list_scope("personal", "alice")}
    assert "红线" in contents                  # redline 逐条保留,未被融合
    assert "融合:a / b / c" in contents        # 融合不含 redline 文本
    assert "融合:" not in "红线"


def test_compress_empty_fuse_noop():
    s = FakeStore()
    [s.write(_e(c, topic_key="t")) for c in ("a", "b", "c")]
    out = MemoryMaintenance(s, min_entries=3).compress_topic("personal", "alice", "t", lambda cs: "  ")
    assert out is None and len(s.list_scope("personal", "alice")) == 3  # 融合空串不动数据


def test_compress_scope_iterates_topics():
    s = FakeStore()
    for c in ("a", "b", "c"):
        s.write(_e(c, topic_key="style"))
    for c in ("x", "y", "z"):
        s.write(_e(c, topic_key="rule"))
    s.write(_e("孤", topic_key="lonely"))  # 单条不压
    out = MemoryMaintenance(s, min_entries=3).compress_scope("personal", "alice", _CONCAT)
    assert set(out.keys()) == {"style", "rule"}


# ---- TTL ----

def test_archive_expired_passthrough():
    s = FakeStore()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    s.write(_e("过期", topic_key=None, ttl_at=past))
    s.write(_e("未过期", topic_key=None, ttl_at=future))
    s.write(_e("永久", topic_key=None, ttl_at=None))
    n = MemoryMaintenance(s).archive_expired()
    assert n == 1
    contents = {m.content for m in s.list_scope("personal", "alice")}
    assert contents == {"未过期", "永久"}  # 过期的被归档移出 active


def test_list_scope_excludes_expired_even_before_archive():
    # 防御:archive job 没跑,过期记忆也不该出现在 active 列表
    s = FakeStore()
    s.write(_e("过期", ttl_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    assert s.list_scope("personal", "alice") == []
