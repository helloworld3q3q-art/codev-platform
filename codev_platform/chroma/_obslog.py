"""chroma daemon —— 日志 helper (从 server.py 抽出, file-discipline §1)。

mcp_server.log (滚动单备份) + search_recall.jsonl (召回质量分析)。只读模块级常量,
不持有可变共享态; _flog 被 server 各处调用 (import 即用, 无 rebinding 问题)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import codev_platform.core.runtime_artifacts as runtime_artifacts
from codev_platform.core.runtime_artifact_io import append_runtime_artifact_text

_LOG_MAX_BYTES = int(os.getenv("PLATFORM_LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5 MiB


def _log_file() -> Path:
    # 落 data_root/logs (非 import 包目录: wheel/只读安装也可写, 见 core.paths.logs_dir)。
    # 文件名加 chroma_ 前缀, 与 codegraph / graph daemon 的同名日志区分, 防多 daemon 碰撞。
    return runtime_artifacts.chroma_mcp_log_path()


def _recall_log_file() -> Path:
    # 召回质量分析日志:每次 search_docs 一行 JSON,后续可 jq 分析 top-5 distance 漂移。
    return runtime_artifacts.chroma_recall_usage_path()


def _maybe_rotate_log() -> None:
    """日志超过 _LOG_MAX_BYTES 时滚动到 .1 (单备份, 防长跑 daemon 撑爆磁盘)。失败静默。"""
    try:
        log_file = _log_file()
        if log_file.exists() and log_file.stat().st_size > _LOG_MAX_BYTES:
            bak = log_file.with_suffix(log_file.suffix + ".1")
            try:
                if bak.exists():
                    bak.unlink()
            except Exception:
                pass
            log_file.replace(bak)
    except Exception:
        pass


def _flog(msg: str) -> None:
    """同时写文件 + stderr。文件路径:data_root/logs/chroma_mcp_server.log"""
    import datetime

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        _maybe_rotate_log()
        append_runtime_artifact_text(_log_file(), line + "\n")
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

        append_runtime_artifact_text(
            _recall_log_file(),
            _json.dumps(record, ensure_ascii=False) + "\n",
        )
    except Exception:
        pass
