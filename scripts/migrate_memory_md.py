# -*- coding: utf-8 -*-
r"""把 memory/*.md 的人肉记忆迁进 PG memory store(memory plan 难点 #7)。

源:仓根 memory/feedback_*.md + reference_*.md(共 14 条,"跨项目通用偏好真值源")。
分流(plan §3.5):全部 → org 作用域(org-wide 工作约定,团队接入即继承);
  其中"禁止"类硬规则标 is_redline=True(不可被低层覆盖)。
kind:feedback_* → preference;reference_* → reference。
topic_key = 文件名 stem(冲突去重键)。extra 存 source / 原 type / 全文 body(可回溯)。

幂等:已存在同 (scope=org, topic_key) 的 active 条目则跳过,可重复跑不重复写。

跑法(需 config.memory.pg_dsn + psycopg):
  .venv\Scripts\python.exe scripts\migrate_memory_md.py            # dry-run,只打印将写什么
  .venv\Scripts\python.exe scripts\migrate_memory_md.py --apply    # 真写入 PG
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# "禁止"类硬规则 → org 红线(不可被低层覆盖)
_REDLINE = {
    "feedback_no_autonomous_push",
    "feedback_no_absolute_paths",
    "feedback_no_cargo_cult_dates",
    "feedback_no_wrapper_utils",
}


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """解析 --- frontmatter --- + body。返回 (meta, body)。"""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end].strip()
            body = text[end + 4:].strip()
            meta = {}
            for line in fm.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            return meta, body
    return {}, text.strip()


def _entries():
    """yield 每条待迁移记忆的字段 dict。"""
    mem_dir = Path(__file__).resolve().parent.parent / "memory"
    files = sorted(mem_dir.glob("feedback_*.md")) + sorted(mem_dir.glob("reference_*.md"))
    for f in files:
        stem = f.stem
        meta, body = _parse_front_matter(f.read_text(encoding="utf-8"))
        name = meta.get("name") or stem
        desc = meta.get("description") or (body.splitlines()[0] if body else "")
        kind = "reference" if stem.startswith("reference_") else "preference"
        content = f"{name} — {desc}" if desc else name
        yield {
            "stem": stem, "content": content, "kind": kind,
            "is_redline": stem in _REDLINE,
            "extra": {"source": f"memory/{f.name}", "type": meta.get("type"), "body": body},
        }


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    rows = list(_entries())
    print(f"发现 {len(rows)} 条人肉记忆(memory/feedback_*.md + reference_*.md)\n")
    print(f"{'redline':<8} {'kind':<11} topic_key / content")
    print("-" * 92)
    for r in rows:
        flag = "🔴 RL" if r["is_redline"] else "  -"
        print(f"{flag:<8} {r['kind']:<11} {r['stem']}")
        print(f"{'':<20} {r['content'][:80]}")
    print(f"\n全部落 org 作用域(scope_ref='org', org_id='default', owner='local')。")

    if not apply:
        print("\n[DRY-RUN] 未写库。确认无误后加 --apply 真写入。")
        return 0

    # ---- 真写入 PG ----
    from codev_platform.core.config import load_config, env_or_config
    cfg = load_config()
    dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
    if not dsn:
        print("\n[ABORT] memory.pg_dsn 未配。", file=sys.stderr)
        return 2
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("\n[ABORT] 当前解释器没装 psycopg。", file=sys.stderr)
        return 2

    from codev_platform.agent.memory_store import MemoryEntry
    from codev_platform.agent.memory_store_pg import SqlMemoryStore
    read_dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN_READ", cfg, "memory.pg_dsn_read")
    store = SqlMemoryStore(dsn, read_dsn=read_dsn)

    # 幂等:已存在的 org topic_key 跳过
    existing = {e.topic_key for e in store.list_scope("org", "org", org_id="default", limit=500)}
    written = skipped = 0
    print()
    for r in rows:
        if r["stem"] in existing:
            print(f"  [skip] {r['stem']}(已存在)")
            skipped += 1
            continue
        eid = store.write(MemoryEntry(
            id="", scope="org", scope_ref="org", owner_user_id="local",
            content=r["content"], org_id="default", kind=r["kind"],
            topic_key=r["stem"], is_redline=r["is_redline"], extra=r["extra"],
        ))
        print(f"  [写入] {r['stem']} -> {eid[:8]}{'  🔴红线' if r['is_redline'] else ''}")
        written += 1
    print(f"\n完成:写入 {written} 条,跳过 {skipped} 条(已存在)。")
    total = len(store.list_scope("org", "org", org_id="default", limit=500))
    print(f"org 作用域现有 active 记忆:{total} 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
