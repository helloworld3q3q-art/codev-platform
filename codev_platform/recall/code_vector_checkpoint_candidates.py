"""未发布 code_vec side-build 的候选选择与 checkpoint 状态。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from codev_platform.core.index_handoff import read_pointer
from codev_platform.recall.code_vector_checkpoint import (
    checkpoint_fingerprint_digest,
    checkpoint_matches,
    load_checkpoint_meta,
)
from codev_platform.recall.code_vector_manifest import load_incremental_manifest
from codev_platform.recall.code_vector_paths import MANIFEST_META_NAME, MANIFEST_NAME


def _candidate_order(path: Path) -> tuple[int, str]:
    """用 checkpoint 文件代次排序；路径名提供确定性平局规则。"""
    try:
        updated = max(
            (path / MANIFEST_META_NAME).stat().st_mtime_ns,
            (path / MANIFEST_NAME).stat().st_mtime_ns,
        )
    except OSError:
        updated = -1
    return updated, path.name


def _side_build_candidates(base: Path) -> list[Path]:
    root = base / "builds"
    if not root.is_dir():
        return []
    current_id = read_pointer(base)
    candidates = [
        path for path in root.iterdir() if path.is_dir() and path.name != current_id
    ]
    return sorted(candidates, key=_candidate_order, reverse=True)


def _checkpoint_state(
    path: Path,
    health_check: Callable[[Path], bool],
) -> tuple[dict, int] | None:
    meta = load_checkpoint_meta(path / MANIFEST_META_NAME)
    if meta is None:
        return None
    manifest, valid = load_incremental_manifest(path / MANIFEST_NAME)
    entries = int(meta["checkpoint_entries"])
    if not valid or len(manifest) != entries or not health_check(path):
        return None
    return meta, entries


def find_resumable_checkpoint(
    base: Path,
    expected_fingerprint: dict[str, object] | None,
    *,
    health_check: Callable[[Path], bool],
) -> Path | None:
    """按统一最新顺序返回第一个指纹匹配且可恢复的 side-build。"""
    for path in _side_build_candidates(base):
        if expected_fingerprint is not None and not checkpoint_matches(
            path / MANIFEST_META_NAME, expected_fingerprint,
        ):
            continue
        if _checkpoint_state(path, health_check) is not None:
            return path
    return None


def checkpoint_progress(
    base: Path,
    *,
    health_check: Callable[[Path], bool],
) -> dict[str, int]:
    """每个严格指纹只返回 finder 同序下最新可恢复候选的条目数。"""
    latest: dict[str, int] = {}
    for path in _side_build_candidates(base):
        state = _checkpoint_state(path, health_check)
        if state is None:
            continue
        meta, entries = state
        digest = checkpoint_fingerprint_digest(meta["fingerprint"])
        latest.setdefault(digest, entries)
    return latest


__all__ = ["checkpoint_progress", "find_resumable_checkpoint"]
