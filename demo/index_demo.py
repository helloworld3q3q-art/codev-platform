#!/usr/bin/env python3
"""把 demo/docs 索引成 project_id="demo" 的 Chroma collection。

自包含 demo 的一键索引脚本 —— 用户无需真实代码仓即可试平台检索。

用法(在仓库根, 平台 venv):
    .venv/bin/python demo/index_demo.py            # 索引(增量)
    .venv/bin/python demo/index_demo.py --force    # 删旧重建
    .venv/bin/python demo/index_demo.py --dry-run   # 只数文件不入库

环境:
    默认 device=cpu(无 GPU 也能跑);有 GPU 可 PLATFORM_EMBED_DEVICE=cuda 覆盖。
    数据落在 <repo>/data/chroma/ 的 collection `demo__platform_docs`,按 project_id
    与其它项目隔离。

为什么不直接调 `python -m codev_platform.chroma.indexer`:
    indexer 会把 sqlite 切到 WAL 模式(reindex 写时 search 读不阻塞)。一次性
    进程跑完即退出时, chromadb 1.5.9 rust binding 的新 collection 元数据可能停留在
    WAL 未 checkpoint, 下一个独立进程读不到。本脚本在同进程内索引后显式
    checkpoint(TRUNCATE), 保证 demo collection 落盘可被后续 search 命中。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = REPO_ROOT / "demo"

# 指定索引目标 = demo/(其 .claude/project.json 解析出 project_id="demo")
os.environ["PLATFORM_ROOT"] = str(DEMO_DIR)
# 数据根 = <repo>/data(与平台其它项目共享 chroma DB, 按 collection 名隔离)
os.environ.setdefault("PLATFORM_DATA_DIR", str(REPO_ROOT / "data"))
# 默认 CPU, 便于无 GPU 机器试用
os.environ.setdefault("PLATFORM_EMBED_DEVICE", "cpu")

import codev_platform.chroma as _cc  # noqa: E402
import codev_platform.chroma.indexer as ix  # noqa: E402


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    if dry_run:
        files = ix.discover_files()
        print(f"[index_demo] project_id={ix.PROJECT_ID} collection={ix.COLLECTION_NAME}")
        print(f"[index_demo] dry-run: {len(files)} files under {DEMO_DIR}")
        for f in files:
            print("  -", f.relative_to(DEMO_DIR))
        return 0

    # 一次性索引: 关掉 indexer 的 WAL 切换, 避免新 collection 停在未 checkpoint 的 WAL。
    ix.ensure_wal = lambda *a, **k: None
    _cc.ensure_wal = ix.ensure_wal

    n_files, n_chunks = ix.index(force=force)
    print(f"[index_demo] indexed {n_files} files / {n_chunks} chunks "
          f"into {ix.COLLECTION_NAME} @ {ix.PERSIST_DIR}")

    # 兜底 checkpoint(若环境已是 WAL), 保证落盘。
    db = Path(ix.PERSIST_DIR) / "chroma.sqlite3"
    if db.exists():
        try:
            con = sqlite3.connect(str(db))
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.commit()
            con.close()
        except Exception:  # noqa: BLE001 - checkpoint best-effort
            pass

    print("[index_demo] done. 用 platform-docs MCP search_docs(project_id='demo') 检索。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
