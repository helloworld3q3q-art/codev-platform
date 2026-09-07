"""代码向量查询侧：连接缓存、结果归一与语义召回。"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path

from codev_platform.core.index_handoff import evict_stale_build_clients, resolve_current
from codev_platform.recall.code_vector_paths import (
    MANIFEST_META_NAME,
    MANIFEST_NAME,
    code_vec_collection_name,
    code_vec_persist_dir,
)
from codev_platform.recall.code_vector_text import _node_id_of


logger = logging.getLogger(__name__)
_QUERY_CLIENTS: dict = {}
_LEGACY_META_GENERATION = b"<missing-meta>"


def _published_generation(persist: Path) -> tuple[object, ...] | None:
    """稳定采样成功 marker；manifest 缺失或采样中变化时拒绝读取。"""
    manifest = persist / MANIFEST_NAME
    meta = persist / MANIFEST_META_NAME
    try:
        before = manifest.stat()
        meta_payload = meta.read_bytes() if meta.is_file() else _LEGACY_META_GENERATION
        after = manifest.stat()
    except OSError:
        return None
    def identity(stat):
        return (
            stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size,
        )
    before_identity = identity(before)
    if before_identity != identity(after):
        return None
    return (*before_identity, meta_payload)


def _get_query_client(persist_path: str, *, clients: dict | None = None):
    """按持久化目录复用 Chroma 客户端。"""
    import chromadb

    cache = _QUERY_CLIENTS if clients is None else clients
    client = cache.get(persist_path)
    if client is None:
        client = chromadb.PersistentClient(path=persist_path)
        cache[persist_path] = client
    return client


def _parse_query_result(result: dict) -> tuple[list[str], dict]:
    """把 Chroma 子块结果去重并还原为 codegraph 节点。"""
    ids_outer = result.get("ids") or [[]]
    metas_outer = result.get("metadatas") or [[]]
    chunk_ids = list(ids_outer[0]) if ids_outer else []
    metadatas = metas_outer[0] if metas_outer else []
    ranked: list[str] = []
    details: dict = {}
    seen: set[str] = set()
    for index, chunk_id in enumerate(chunk_ids):
        metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
        node_id = metadata.get("node") or _node_id_of(chunk_id)
        if node_id in seen:
            continue
        seen.add(node_id)
        ranked.append(node_id)
        details[node_id] = {
            "name": metadata.get("name"),
            "kind": metadata.get("kind"),
            "file": metadata.get("file"),
        }
    return ranked, details


def query_code_vectors(
    project_id: str,
    query: str,
    k: int,
    *,
    persist_dir_resolver: Callable[[str], object] = code_vec_persist_dir,
    collection_name_resolver: Callable[[str], str] = code_vec_collection_name,
    client_getter: Callable[[str], object] | None = None,
    result_parser: Callable[[dict], tuple[list[str], dict]] = _parse_query_result,
    clients: dict | None = None,
) -> tuple[list[str], dict]:
    """按语义相似度召回 codegraph 节点；索引或嵌入器缺失时空返。"""
    if k <= 0:
        return [], {}
    cache = _QUERY_CLIENTS if clients is None else clients
    base_dir = persist_dir_resolver(project_id)
    persist = resolve_current(base_dir)
    evict_stale_build_clients(base_dir, persist, cache)
    if not (persist / MANIFEST_NAME).is_file():
        return [], {}

    from codev_platform.agent.embed.registry import build_embedder
    from codev_platform.core.config import load_config

    embedder = build_embedder(load_config())
    if embedder is None:
        logger.warning("[code_vec] embedder 不可用(memory.embed.backend), 向量 lane 退化")
        return [], {}
    query_vector = embedder.encode(query)
    overfetch = min(max(k * 3, k), 200)
    try:
        get_client = client_getter or (lambda path: _get_query_client(path, clients=cache))
        client = get_client(str(persist))
        collection = client.get_collection(collection_name_resolver(project_id))
        generation = _published_generation(persist)
        if generation is None:
            return [], {}
        result = collection.query(
            query_embeddings=[query_vector],
            n_results=overfetch,
            include=["metadatas"],
        )
    except Exception as exc:  # noqa: BLE001 - 并发切换期间查询必须 fail-soft
        logger.warning("[code_vec] 查询期间索引不可读，向量 lane 退化: %s", type(exc).__name__)
        return [], {}
    if _published_generation(persist) != generation:
        return [], {}
    ranked, details = result_parser(result)
    ranked = ranked[:k]
    return ranked, {node_id: details[node_id] for node_id in ranked}
