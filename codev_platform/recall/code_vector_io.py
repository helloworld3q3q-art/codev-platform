"""代码向量索引使用的原子 JSON 写入。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import uuid


def _fsync_directory(path: Path) -> None:
    """刷新目录项；Windows 不支持目录句柄时保留文件级耐久保证。"""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json_atomic(path: Path, value: object) -> None:
    """刷新同目录临时文件后原子替换，并在支持的平台刷新目录项。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    created = False
    try:
        with temporary.open("xb") as stream:
            created = True
            stream.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if created:
            temporary.unlink(missing_ok=True)
