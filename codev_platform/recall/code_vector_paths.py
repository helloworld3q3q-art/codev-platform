"""代码向量索引的目录与 collection 命名约定。"""

from __future__ import annotations

from codev_platform.core.paths import chroma_collection_name, chroma_dir


CODE_VEC_SUBDIR = "code_vec"
MANIFEST_NAME = ".manifest.json"
MANIFEST_META_NAME = ".manifest.meta.json"


def code_vec_collection_name(project_id: str) -> str:
    """返回项目独占的代码向量 collection 名。"""
    return chroma_collection_name(project_id, "code_vec")


def code_vec_persist_dir(project_id: str):
    """返回项目独占的代码向量持久化目录。"""
    return chroma_dir() / CODE_VEC_SUBDIR / project_id
