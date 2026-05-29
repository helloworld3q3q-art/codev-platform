# -*- coding: utf-8 -*-
r"""memory PG 真机验证脚本(M2 闭环)。

前提(用户先做):
  1. 建库:psql -U postgres -f scripts/init-memory-db.sql(改好密码)
  2. config ~/.codev-platform/config.json 填 memory.pg_dsn(密码在这,不进 git)
  3. 装驱动:pip install -e .[agent](含 psycopg)

跑法(任选有 psycopg 的解释器):
  .venv\Scripts\python.exe scripts\verify_memory_pg.py
  或  D:\ProgramFiles\Python314\python.exe scripts\verify_memory_pg.py

它会:连库 → 建表(幂等)→ 写/读记忆 → supersede/forget → 冲突消解 →
会话 round-trip → **新建一个 store 实例模拟"重启"** 验证持久化。全程不删你别的数据,
用独立测试 user/session(verify-* 前缀),跑完清理自己造的数据。
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
    from codev_platform.core.config import load_config, get
    from codev_platform.core.config import env_or_config
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

    from codev_platform.agent.memory_store import MemoryEntry
    from codev_platform.agent.memory_store_pg import SqlMemoryStore
    from codev_platform.agent.session_pg import SqlSessionStore
    from codev_platform.agent.memory_recall import resolve_conflicts
    from codev_platform.agent.brain import Message, ToolCall

    uid = f"verify-{uuid.uuid4().hex[:8]}"
    fails = 0

    # ---- 1. 记忆 write / list ----
    print("\n[1] 记忆 write / list")
    mem = SqlMemoryStore(dsn, read_dsn=read_dsn)
    e = MemoryEntry(id="", scope="personal", scope_ref=uid, owner_user_id=uid,
                    content="我喜欢简洁回答", kind="preference", topic_key="style")
    mid = mem.write(e)
    _ok(f"write -> id={mid[:8]}")
    got = mem.list_scope("personal", uid)
    if any(m.content == "我喜欢简洁回答" for m in got):
        _ok(f"list_scope 读回 {len(got)} 条,含刚写的")
    else:
        _fail("list_scope 没读到刚写的"); fails += 1

    # ---- 2. supersede(留痕)----
    print("\n[2] supersede")
    e2 = MemoryEntry(id="", scope="personal", scope_ref=uid, owner_user_id=uid,
                     content="我改主意了,要详细", kind="preference", topic_key="style")
    mem.supersede(mid, e2)
    active = mem.list_scope("personal", uid)
    if any(m.content == "我改主意了,要详细" for m in active) and \
       not any(m.content == "我喜欢简洁回答" for m in active):
        _ok("supersede 后只见新值,旧值 superseded 不在 active")
    else:
        _fail("supersede 语义不对"); fails += 1

    # ---- 3. forget ----
    print("\n[3] forget")
    for m in active:
        mem.forget(m.id)
    if not mem.list_scope("personal", uid):
        _ok("forget 后 active 为空")
    else:
        _fail("forget 后仍有 active"); fails += 1

    # ---- 4. 冲突消解(纯逻辑,顺带验)----
    print("\n[4] 冲突消解 policy")
    es = [MemoryEntry(id="a", scope="org", scope_ref="org", owner_user_id=uid, content="org值", topic_key="t"),
          MemoryEntry(id="b", scope="personal", scope_ref=uid, owner_user_id=uid, content="个人值", topic_key="t")]
    win = resolve_conflicts(es, policy="personal_first")
    if len(win) == 1 and win[0].content == "个人值":
        _ok("personal_first: 个人值胜")
    else:
        _fail("冲突消解结果不对"); fails += 1

    # ---- 4b. 跨作用域召回端到端(M3,直查真库)----
    print("\n[4b] 跨作用域召回(M3 真机)")
    from codev_platform.agent.recall_service import LocalRecallService
    proj = f"projverify-{uid}"
    mem.write(MemoryEntry(id="", scope="org", scope_ref="org", owner_user_id=uid,
                          content="提交不带 AI 痕迹", is_redline=True, topic_key="commit-style"))
    mem.write(MemoryEntry(id="", scope="project", scope_ref=proj, owner_user_id=uid,
                          content="本项目用 conventional commits"))
    mem.write(MemoryEntry(id="", scope="personal", scope_ref=uid, owner_user_id=uid,
                          content="我喜欢中文 commit message"))
    recalled = LocalRecallService(mem).recall(
        org_id="default", user_id=uid, project_id=proj, query="commit", limit=8)
    rc = [m.content for m in recalled]
    if len(recalled) == 3 and recalled[0].is_redline and recalled[0].content == "提交不带 AI 痕迹":
        _ok(f"召回 3 作用域合并,redline 居首:{rc}")
    else:
        _fail(f"召回结果不对:{rc}"); fails += 1

    # ---- 5. 会话 round-trip + 模拟重启持久化 ----
    print("\n[5] 会话持久化 + 模拟重启")
    s1 = SqlSessionStore(dsn, read_dsn=read_dsn)
    sid = s1.new(uid)
    s1.append(sid, uid,
              Message(role="user", content="第一句"),
              Message(role="assistant", content="回答一",
                      tool_calls=[ToolCall(id="c1", name="echo", args={"v": "x"})]))
    # 模拟"重启":全新 store 实例(等价新进程),只凭 sid 读回
    s2 = SqlSessionStore(dsn, read_dsn=read_dsn)
    hist = s2.get(sid, uid)
    if len(hist) == 2 and hist[0].content == "第一句" and hist[1].tool_calls and hist[1].tool_calls[0].name == "echo":
        _ok(f"新 store 实例读回 {len(hist)} 条(含 tool_calls)→ 跨重启持久化 OK")
    else:
        _fail(f"重启后历史不对: {[(m.role, m.content) for m in hist]}"); fails += 1
    # user 隔离
    if not s2.has(sid, "other-user"):
        _ok("user 隔离:别的 user 看不到此会话")
    else:
        _fail("user 隔离失效"); fails += 1

    # ---- 清理自造数据 ----
    print("\n[6] 清理测试数据")
    try:
        with mem._write_pool.connection() as conn:
            conn.execute("DELETE FROM memory_entries WHERE owner_user_id=%s", (uid,))
            conn.execute("DELETE FROM agent_messages WHERE user_id=%s", (uid,))
            conn.execute("DELETE FROM agent_sessions WHERE user_id=%s", (uid,))
        _ok(f"已清理 user={uid} 的测试数据")
    except Exception as ex:  # noqa: BLE001
        print(f"  [WARN] 清理失败(不影响验证结论): {ex}")

    print(f"\n{'='*48}")
    print("结果:" + ("全部通过 ✅ memory PG 真机验证 OK" if fails == 0 else f"{fails} 项失败 ❌"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
