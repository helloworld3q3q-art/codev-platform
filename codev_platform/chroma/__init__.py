"""codev_platform.chroma — chroma 多租户 MCP daemon + BM25 hybrid recall。

模块组织:
    server   chroma daemon (MCP SSE server, multi-tenant routing via X-Project-Id / ?project_id=)
    bm25     BM25 倒排索引 (jieba + rank_bm25), 与 chroma 向量召回做 RRF 融合

入口: python -m codev_platform.chroma.server [--http] [--port 18083]
依赖: chromadb, sentence-transformers (Qwen3-Embedding-0.6B), torch, optional jieba+rank_bm25
"""
from __future__ import annotations

from pathlib import Path


def ensure_wal(persist_dir) -> str | None:
    """把 chroma sqlite 切 WAL —— reindex 写时 search 读不被阻塞。

    chromadb 默认 `delete`(回滚日志)模式: 写者拿排他锁, 期间读者阻塞 / 报 database is locked。
    WAL 下读写互不阻塞。journal_mode=WAL 是 db 级持久设置, 设一次即生效。db 不存在
    (首次索引前) 则跳过, 返回 None; 否则返回最终 journal_mode。失败静默 (不阻断索引/服务)。
    """
    import sqlite3
    db = Path(persist_dir) / "chroma.sqlite3"
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(str(db))
        try:
            row = con.execute("PRAGMA journal_mode=WAL").fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except Exception:
        return None
