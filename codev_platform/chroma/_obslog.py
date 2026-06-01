"""chroma daemon —— 日志 helper (从 server.py 抽出, file-discipline §1)。

mcp_server.log (滚动单备份) + search_recall.jsonl (召回质量分析)。只读模块级常量,
不持有可变共享态; _flog 被 server 各处调用 (import 即用, 无 rebinding 问题)。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 额外把启动 / 每次 query 日志写到固定文件，便于"观察模型起作用"
_LOG_FILE = Path(__file__).resolve().parent / "mcp_server.log"
# 召回质量分析日志:每次 search_docs 一行 JSON,后续可 jq 分析 top-5 distance 漂移
_RECALL_LOG = Path(__file__).resolve().parent / "search_recall.jsonl"

_LOG_MAX_BYTES = int(os.getenv("PLATFORM_LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MiB


def _maybe_rotate_log() -> None:
    """日志超过 _LOG_MAX_BYTES 时滚动到 .1 (单备份, 防长跑 daemon 撑爆磁盘)。失败静默。"""
    try:
        if _LOG_FILE.exists() and _LOG_FILE.stat().st_size > _LOG_MAX_BYTES:
            bak = _LOG_FILE.with_suffix(_LOG_FILE.suffix + ".1")
            try:
                if bak.exists():
                    bak.unlink()
            except Exception:
                pass
            _LOG_FILE.replace(bak)
    except Exception:
        pass


def _flog(msg: str) -> None:
    """同时写文件 + stderr。文件路径：tools/chroma/mcp_server.log"""
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        _maybe_rotate_log()
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # Windows MCP clients may close or replace stderr during startup. Logging
    # must never poison model initialization or stdio protocol handling.
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass


def _log_recall(record: dict) -> None:
    """JSONL 召回日志:每行一个 query。失败静默(不阻塞查询)。"""
    try:
        import json as _json
        with _RECALL_LOG.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
