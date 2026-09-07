"""Chroma 索引文件读取与摘要；隔离输入失败时统一撤销 manifest。"""
from __future__ import annotations

import logging
import os
import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path

from codev_platform.core.repo_input_guard import PROVEN_REINDEX_INPUT_ENV


def proven_index_input() -> bool:
    marker = os.environ.get(PROVEN_REINDEX_INPUT_ENV)
    if marker is None:
        return False
    if marker != "1":
        raise RuntimeError("受证明索引输入门禁无效")
    return True


def _invalidate_and_raise(manifest_path: Path | None, message: str) -> None:
    if manifest_path is not None:
        manifest_path.unlink(missing_ok=True)
    raise RuntimeError(message) from None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_index_snapshot(path: Path, *, manifest_path: Path | None) -> tuple[str, str]:
    """一次读取同一 bytes，同时生成摘要和解码文本，消除 hash/read TOCTOU。"""
    try:
        data = path.read_bytes()
    except Exception:  # noqa: BLE001 - 受证明输入必须失败关闭
        _invalidate_and_raise(manifest_path, "受证明索引文件读取失败")
    try:
        digest = _sha256(data)
    except Exception:  # noqa: BLE001 - 摘要失败不得发布
        _invalidate_and_raise(manifest_path, "受证明索引文件摘要失败")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("gbk")
        except Exception:  # noqa: BLE001 - 兼容解码仍失败则拒绝
            _invalidate_and_raise(manifest_path, "受证明索引文件无法解码")
    return digest, text


def read_verified_index_text(
    path: Path,
    *,
    label: str,
    expected_sha: Mapping[str, str],
    manifest_path: Path | None,
) -> str:
    """写入前用同一 bytes 复核扫描摘要并解码，变化即失败关闭。"""
    digest, text = read_index_snapshot(path, manifest_path=manifest_path)
    if expected_sha.get(label) != digest:
        _invalidate_and_raise(manifest_path, "受证明索引文件在扫描后发生变化")
    return text


def read_index_text(
    path: Path,
    *,
    label: str,
    logger: logging.Logger,
    manifest_path: Path | None,
) -> str | None:
    """UTF-8 优先、GBK 兼容；受证明输入失败关闭，legacy 保持跳过。"""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="gbk")
        except Exception as exc:  # noqa: BLE001 - legacy 需兼容历史跳过语义
            if proven_index_input():
                _invalidate_and_raise(manifest_path, "受证明索引文件无法解码")
            logger.warning("跳过无法解码文件 %s: %s", label, exc)
            return None
    except Exception as exc:  # noqa: BLE001 - legacy 需兼容历史跳过语义
        if proven_index_input():
            _invalidate_and_raise(manifest_path, "受证明索引文件读取失败")
        logger.warning("跳过读取失败文件 %s: %s", label, exc)
        return None


def hash_index_file(
    path: Path,
    *,
    label: str,
    hasher: Callable[[Path], str],
    logger: logging.Logger,
    manifest_path: Path | None,
) -> str | None:
    """计算文件摘要；受证明输入失败关闭，legacy 保持跳过。"""
    try:
        return hasher(path)
    except Exception as exc:  # noqa: BLE001 - legacy 需兼容历史跳过语义
        if proven_index_input():
            _invalidate_and_raise(manifest_path, "受证明索引文件摘要失败")
        logger.warning("跳过 sha256 失败 %s: %s", label, exc)
        return None


__all__ = [
    "hash_index_file",
    "proven_index_input",
    "read_index_snapshot",
    "read_index_text",
    "read_verified_index_text",
]
