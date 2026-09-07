"""code_vec 后验完整性证明必须使用真实的独立 Chroma 进程。"""
from __future__ import annotations

import os

import pytest

from codev_platform.recall import code_vector_store

chromadb = pytest.importorskip("chromadb")


def test_collection_proof_runs_in_different_process_with_real_chroma(tmp_path) -> None:
    database = tmp_path / "code-vec"
    collection_name = "demo__code_vec"
    client = chromadb.PersistentClient(path=str(database))
    collection = client.get_or_create_collection(collection_name)
    collection.upsert(
        ids=["node-1", "node-2"],
        embeddings=[[0.1, 0.2], [0.2, 0.1]],
    )
    try:
        result = code_vector_store._probe_collection_ids_in_subprocess(
            database,
            collection_name,
            {"node-1": "a" * 40, "node-2": "b" * 40},
        )
    finally:
        client.close()

    assert result.count == 2
    assert result.pid != os.getpid()
