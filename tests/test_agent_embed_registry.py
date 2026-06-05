"""嵌入模型 registry(P2a)—— config 选 backend + device 别名 + 缺依赖降级 + index 经 registry。"""
from __future__ import annotations

from codev_platform.agent.embed.registry import build_embedder


def test_default_backend_qwen_local():
    from codev_platform.agent.embed.qwen import QwenLocalEmbedder
    emb = build_embedder({})                       # 默认 qwen-local;WSL venv 有 sentence-transformers
    assert isinstance(emb, QwenLocalEmbedder)


def test_unknown_backend_returns_none():
    assert build_embedder({"memory": {"embed": {"backend": "bogus-model"}}}) is None


def test_device_precedence_new_over_alias_over_default():
    assert build_embedder({"memory": {"embed": {"device": "cuda"}}})._device == "cuda"
    assert build_embedder({"memory": {"embed_device": "cuda:1"}})._device == "cuda:1"   # 旧别名
    assert build_embedder({})._device == "cpu"                                          # 默认


def test_embed_path_from_config():
    emb = build_embedder({"models": {"embed_path": "/custom/model"}})
    assert emb._model_path == "/custom/model"


def test_qwen_local_degrades_without_sentence_transformers(monkeypatch):
    import importlib.util as iu
    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec",
                        lambda name: None if name == "sentence_transformers" else real(name))
    assert build_embedder({}) is None              # 依赖缺 → None(调用方退回 local)


def test_build_index_uses_registry_unknown_backend_none():
    # build_memory_vector_index 经 registry 选 embedder;backend 未知 → embedder None → index None
    from codev_platform.agent.memory_vector_chroma import build_memory_vector_index
    assert build_memory_vector_index({"memory": {"embed": {"backend": "bogus-model"}}}) is None


def test_build_index_none_when_chromadb_missing(monkeypatch):
    # chromadb 缺 → 直接 None(短路,不进 embedder)→ 召回退 local(降级)
    import importlib.util as iu
    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec",
                        lambda name: None if name == "chromadb" else real(name))
    from codev_platform.agent.memory_vector_chroma import build_memory_vector_index
    assert build_memory_vector_index({}) is None
