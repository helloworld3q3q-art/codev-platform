"""code_vec Chroma collection 的写入安全配置。"""

from __future__ import annotations

from codev_platform.recall.code_vector_paths import code_vec_collection_name


CODE_VEC_CHROMA_STORAGE_POLICY_VERSION = 2
_SAFE_HNSW_BATCH_SIZE = 50_000


def code_vec_collection_metadata() -> dict[str, str]:
    """返回兼容旧 Chroma 客户端的余弦距离元数据。"""
    return {"hnsw:space": "cosine"}


def code_vec_collection_configuration() -> dict[str, dict[str, int | str]]:
    """延后 HNSW compaction，避免小 checkpoint 写入触发 Chroma 1.5.9 缺陷。"""
    return {
        "hnsw": {
            "space": "cosine",
            "batch_size": _SAFE_HNSW_BATCH_SIZE,
            "sync_threshold": _SAFE_HNSW_BATCH_SIZE,
        }
    }


def open_code_vec_collection(client: object, project_id: str):
    """以唯一安全策略获取项目专属 collection。"""
    return client.get_or_create_collection(
        name=code_vec_collection_name(project_id),
        metadata=code_vec_collection_metadata(),
        configuration=code_vec_collection_configuration(),
    )


__all__ = [
    "CODE_VEC_CHROMA_STORAGE_POLICY_VERSION",
    "code_vec_collection_configuration",
    "code_vec_collection_metadata",
    "open_code_vec_collection",
]
