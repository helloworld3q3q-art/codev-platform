"""把 *.md 人肉记忆导入 PG memory store(memory plan 难点 #7;dev-agent-memory P2 迁移)。

逻辑住进包内(而非仅 scripts/),`codev-platform memory import-md` CLI 与 wheel 安装都可用;
`scripts/migrate_memory_md.py` 退化为薄 shim 调本模块。

分流(plan §3.5):
  - codev-platform/memory/(跨项目通用)→ org 作用域(团队接入即继承)
  - 业务仓 docs/memory/(业务专属)→ project 作用域(scope_ref=<project_id>)
红线:frontmatter `is_redline: true` 或在内置"禁止"集合里 → is_redline=True。
kind:reference_* → reference,其余 → preference。topic_key = make_topic_key(文件名 stem)(去重键,
三写入端统一 slug)。幂等:同 (scope, scope_ref, topic_key) 已存在则跳过。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

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


def iter_entries(source_dir: Path):
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


def run_import(source_dir: Path, *, scope: str = "org", scope_ref: str = "org",
               owner: str = "local", apply: bool = False,
               echo: Callable[[str], None] = print) -> int:
    """导入 source_dir 的人肉记忆到 PG。apply=False(默认)= dry-run 仅打印。

    返回退出码:0 成功 / 2 源目录不存在或环境缺(pg_dsn / psycopg)。echo 可注入捕获输出。
    幂等靠 make_topic_key(stem) 与已存在 topic_key 比对。
    """
    if not source_dir.is_dir():
        echo(f"[ABORT] 源目录不存在:{source_dir}")
        return 2
    rows = list(iter_entries(source_dir))
    echo(f"源:{source_dir}")
    echo(f"发现 {len(rows)} 条人肉记忆(feedback_*.md + reference_*.md)")
    for r in rows:
        flag = "RL" if r["is_redline"] else "-"
        echo(f"  [{flag}] {r['kind']:<11} {r['stem']}: {r['content'][:80]}")
    echo(f"全部落 {scope} 作用域(scope_ref='{scope_ref}', org_id='default', owner='{owner}')。")

    if not apply:
        echo("[DRY-RUN] 未写库。确认无误后加 --apply 真写入。")
        return 0

    from codev_platform.core.config import env_or_config, load_config
    cfg = load_config()
    dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
    if not dsn:
        echo("[ABORT] memory.pg_dsn 未配。")
        return 2
    try:
        import psycopg  # noqa: F401
    except ImportError:
        echo("[ABORT] 当前解释器没装 psycopg。")
        return 2

    from codev_platform.agent.memory_authz import make_topic_key
    from codev_platform.agent.memory_store import MemoryEntry
    from codev_platform.agent.memory_store_pg import SqlMemoryStore
    read_dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN_READ", cfg, "memory.pg_dsn_read")
    store = SqlMemoryStore(dsn, read_dsn=read_dsn)

    # 幂等:已存在的 topic_key 跳过。topic_key 经 make_topic_key 归一(三写入端同一 slug)。
    existing = {e.topic_key for e in store.list_scope(scope, scope_ref, org_id="default", limit=500)}
    written = skipped = 0
    for r in rows:
        tk = make_topic_key(r["stem"])
        if tk in existing:
            echo(f"  [skip] {r['stem']}(已存在)")
            skipped += 1
            continue
        eid = store.write(MemoryEntry(
            id="", scope=scope, scope_ref=scope_ref, owner_user_id=owner,
            content=r["content"], org_id="default", kind=r["kind"],
            topic_key=tk, is_redline=r["is_redline"], extra=r["extra"],
        ))
        echo(f"  [写入] {r['stem']} -> {eid[:8]}{'  RL' if r['is_redline'] else ''}")
        written += 1
    total = len(store.list_scope(scope, scope_ref, org_id="default", limit=500))
    echo(f"完成:写入 {written} 条,跳过 {skipped} 条(已存在)。{scope}/{scope_ref} 现有 {total} 条。")
    return 0
