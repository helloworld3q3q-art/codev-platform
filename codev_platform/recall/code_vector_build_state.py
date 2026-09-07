"""代码向量构建的配置解析与既有存储状态读取。"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3


DEFAULT_SKIP_KINDS = frozenset({"import", "file", "variable"})


def resolve_skip_kinds(cfg: dict) -> frozenset:
    """读取索引排除种类；未配置时保留历史默认值。"""
    from codev_platform.core.config import get as get_config

    raw = get_config(cfg, "recall.code_vec.skip_kinds", None)
    if raw is None:
        return DEFAULT_SKIP_KINDS
    if isinstance(raw, (list, tuple, set)):
        return frozenset(str(kind).strip().lower() for kind in raw if str(kind).strip())
    return DEFAULT_SKIP_KINDS


def existing_chroma_healthy(persist: Path) -> bool:
    """要求 SQLite 存在且通过只读 quick_check，才允许续写。"""
    database = persist / "chroma.sqlite3"
    if not database.exists():
        return False
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
            return bool(row) and str(row[0]).lower() == "ok"
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 -- 无法证明健康就转干净全量构建
        return False


def read_enrich_mode(meta_path: Path) -> bool | None:
    """读取上次构建的源码富化模式；缺失或损坏时返回未知。"""
    try:
        if not meta_path.exists():
            return None
        value = json.loads(meta_path.read_text(encoding="utf-8"))
        if type(value) is not dict:
            return None
        if type(value.get("enrich")) is bool:
            return value["enrich"]
        fingerprint = value.get("fingerprint")
        if type(fingerprint) is dict and type(fingerprint.get("enrich")) is bool:
            return fingerprint["enrich"]
        return None
    except Exception:  # noqa: BLE001 -- 旧元数据损坏时强制走更安全的全量路径
        return None
