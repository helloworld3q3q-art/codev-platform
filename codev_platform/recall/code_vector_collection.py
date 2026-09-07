"""从多仓 codegraph 收集 code_vec chunk；主流程只负责轻量聚合。"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path

from codev_platform.core.repo_input_guard import PROVEN_REINDEX_INPUT_ENV

_Chunker = Callable[[dict, Path | None], list[tuple[str, str]]]
_Hasher = Callable[[str], str]


def _proven_mode() -> bool:
    marker = os.environ.get(PROVEN_REINDEX_INPUT_ENV)
    if marker is None:
        return False
    if marker != "1":
        raise RuntimeError("受证明 code_vec 输入门禁无效")
    return True


def _node_records(
    spec,
    node: dict,
    skip_kinds: frozenset,
    repo_root: Path | None,
    chunker: _Chunker,
) -> Iterator[tuple[str, str, dict]]:
    node_id = node.get("id")
    kind = str(node.get("kind") or "").lower()
    if not node_id or kind in skip_kinds:
        return
    node_id = str(node_id)
    ref = spec.local_ref(node_id)
    for chunk_id, text in chunker(node, repo_root):
        if not text.strip():
            continue
        localized = spec.local_ref(chunk_id)
        yield localized, text, {
            "name": node.get("name") or "",
            "kind": node.get("kind") or "",
            "file": spec.local_file(node.get("filePath")) or "",
            "node": ref,
            "repo_tag": spec.tag,
            "repo_root": str(spec.root) if repo_root is not None else "",
        }


def _repo_records(
    client_type,
    spec,
    skip_kinds: frozenset,
    *,
    project_id: str | None,
    enrich: bool,
    chunker: _Chunker,
) -> Iterator[tuple[str, str, dict]]:
    client = (
        client_type(project_id=project_id)
        if project_id and not enrich and spec.is_main
        else client_type(db_path=spec.codegraph_db)
    )
    repo_root = spec.root if enrich else None
    with client as codegraph:
        for node in codegraph.iter_nodes():
            yield from _node_records(spec, node, skip_kinds, repo_root, chunker)


def collect_node_chunks(
    repo_specs,
    skip_kinds: frozenset,
    *,
    project_id: str | None,
    chunker: _Chunker,
    hasher: _Hasher,
    logger: logging.Logger,
) -> tuple[dict, dict, dict]:
    """逐仓收集 chunk；隔离输入任一仓失败关闭，legacy extra 仓保持降级。"""
    from codev_platform.web.integrations.codegraph_client import CodegraphClient

    enrich = bool(repo_specs)
    sources = list(repo_specs)
    if not sources and project_id:
        from codev_platform.core.repos import RepoSpec
        sources = [RepoSpec(root=Path("."), tag="", is_main=True, source_project_id=project_id)]
    manifest: dict = {}
    text_by_id: dict[str, str] = {}
    meta_by_id: dict[str, dict] = {}
    proven = _proven_mode()
    for spec in sources:
        try:
            for chunk_id, text, meta in _repo_records(
                CodegraphClient, spec, skip_kinds,
                project_id=project_id, enrich=enrich, chunker=chunker,
            ):
                manifest[chunk_id] = hasher(text)
                text_by_id[chunk_id] = text
                meta_by_id[chunk_id] = meta
        except Exception as exc:  # noqa: BLE001 - legacy extra 仓可降级，隔离输入必须完整
            if proven:
                raise RuntimeError("受证明 code_vec 仓图谱不可用") from None
            if spec.is_main:
                raise
            logger.warning("[code_vec] extra repo %s codegraph 不可用, 跳过: %s", spec.root, exc)
    return manifest, text_by_id, meta_by_id


__all__ = ["collect_node_chunks"]
