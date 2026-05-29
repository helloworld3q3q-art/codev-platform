# -*- coding: utf-8 -*-
r"""把 *.md 人肉记忆迁进 PG memory store(memory plan 难点 #7)。可指定作用域。

分流(plan §3.5):
  - codev-platform/memory/(跨项目通用)→ org 作用域(团队接入即继承)
  - 业务仓 docs/memory/(业务专属)→ project 作用域(scope_ref=<project_id>,不污染其它项目)
红线:frontmatter `is_redline: true` 或在内置"禁止"集合里 → is_redline=True。
kind:feedback_* → preference;reference_* → reference。topic_key = 文件名 stem(去重键)。
extra 存 source / 原 type / 全文 body(可回溯)。幂等:同 (scope, scope_ref, topic_key) 已存在则跳过。

跑法(需 config.memory.pg_dsn + psycopg):
  # codev-platform 跨项目偏好 → org(默认)
  .venv\Scripts\python.exe scripts\migrate_memory_md.py [--apply]
  # 业务仓 platform 业务专属 → project
  .venv\Scripts\python.exe scripts\migrate_memory_md.py --source D:\WorkSpace\platform\docs\memory \
      --scope project --scope-ref openclaw-stock [--apply]
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 内置"禁止"类硬规则 stem → 红线(codev-platform org 那批;业务仓可用 frontmatter is_redline 标)
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


def _is_redline(stem: str, meta: dict) -> bool:
    v = str(meta.get("is_redline", "")).strip().lower()
    return v in ("true", "1", "yes") or stem in _REDLINE


def _entries(source_dir: Path):
    """yield source_dir 下 feedback_*.md + reference_*.md 每条记忆的字段 dict。"""
    files = sorted(source_dir.glob("feedback_*.md")) + sorted(source_dir.glob("reference_*.md"))
    for f in files:
        stem = f.stem
        meta, body = _parse_front_matter(f.read_text(encoding="utf-8"))
        name = meta.get("name") or stem
        desc = meta.get("description") or (body.splitlines()[0] if body else "")
        kind = "reference" if stem.startswith("reference_") else "preference"
        content = f"{name} — {desc}" if desc else name
        yield {
            "stem": stem, "content": content, "kind": kind,
            "is_redline": _is_redline(stem, meta),
            "extra": {"source": str(f), "type": meta.get("type"), "body": body},
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="记忆 .md 源目录(默认 codev-platform/memory)")
    ap.add_argument("--scope", default="org", help="org | team | project | personal(默认 org)")
    ap.add_argument("--scope-ref", default="org", help="作用域 ref:org='org' / project=project_id / personal=user_id(默认 org)")
    ap.add_argument("--owner", default="local", help="owner_user_id(默认 local)")
    ap.add_argument("--apply", action="store_true", help="真写入 PG(默认 dry-run)")
    args = ap.parse_args()

    source_dir = Path(args.source).resolve() if args.source else Path(__file__).resolve().parent.parent / "memory"
    if not source_dir.is_dir():
        print(f"[ABORT] 源目录不存在:{source_dir}", file=sys.stderr)
        return 2
    scope, scope_ref, owner, apply = args.scope, args.scope_ref, args.owner, args.apply
    rows = list(_entries(source_dir))
    print(f"源:{source_dir}")
    print(f"发现 {len(rows)} 条人肉记忆(feedback_*.md + reference_*.md)\n")
    print(f"{'redline':<8} {'kind':<11} topic_key / content")
    print("-" * 92)
    for r in rows:
        flag = "🔴 RL" if r["is_redline"] else "  -"
        print(f"{flag:<8} {r['kind']:<11} {r['stem']}")
        print(f"{'':<20} {r['content'][:80]}")
    print(f"\n全部落 {scope} 作用域(scope_ref='{scope_ref}', org_id='default', owner='{owner}')。")

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

    # 幂等:已存在的 (scope, scope_ref) topic_key 跳过
    existing = {e.topic_key for e in store.list_scope(scope, scope_ref, org_id="default", limit=500)}
    written = skipped = 0
    print()
    for r in rows:
        if r["stem"] in existing:
            print(f"  [skip] {r['stem']}(已存在)")
            skipped += 1
            continue
        eid = store.write(MemoryEntry(
            id="", scope=scope, scope_ref=scope_ref, owner_user_id=owner,
            content=r["content"], org_id="default", kind=r["kind"],
            topic_key=r["stem"], is_redline=r["is_redline"], extra=r["extra"],
        ))
        print(f"  [写入] {r['stem']} -> {eid[:8]}{'  🔴红线' if r['is_redline'] else ''}")
        written += 1
    print(f"\n完成:写入 {written} 条,跳过 {skipped} 条(已存在)。")
    total = len(store.list_scope(scope, scope_ref, org_id="default", limit=500))
    print(f"{scope}/{scope_ref} 作用域现有 active 记忆:{total} 条。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
