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


def test_remote_embedder_encode(monkeypatch):
    import json
    import urllib.request

    from codev_platform.agent.embed.remote import RemoteEmbedder
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"vectors": [[0.1, 0.2, 0.3]]}).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    v = RemoteEmbedder("http://x/embed").encode("hello")
    assert v == [0.1, 0.2, 0.3]
    assert captured["url"] == "http://x/embed" and captured["body"] == {"text": "hello"}


def test_registry_remote_default_url_from_daemon_port():
    from codev_platform.agent.embed.remote import RemoteEmbedder
    emb = build_embedder({"memory": {"embed": {"backend": "remote"}}, "daemon": {"port": 19083}})
    assert isinstance(emb, RemoteEmbedder) and emb._url == "http://127.0.0.1:19083/embed"


def test_registry_remote_explicit_url():
    emb = build_embedder({"memory": {"embed": {"backend": "remote", "url": "http://h:9/embed"}}})
    assert emb._url == "http://h:9/embed"


# ---- rerank model(P3)----

def test_remote_rerank_model_score(monkeypatch):
    import json
    import urllib.request

    from codev_platform.agent.embed.remote import RemoteRerankModel
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"scores": [0.9, 0.1]}).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    s = RemoteRerankModel("http://x/rerank").score("q", ["a", "b"])
    assert s == [0.9, 0.1]
    assert captured["url"] == "http://x/rerank" and captured["body"] == {"query": "q", "docs": ["a", "b"]}


def test_build_rerank_model_remote_default_url():
    from codev_platform.agent.embed.registry import build_rerank_model
    from codev_platform.agent.embed.remote import RemoteRerankModel
    m = build_rerank_model({"daemon": {"port": 19083}})       # 默认 backend=remote
    assert isinstance(m, RemoteRerankModel) and m._url == "http://127.0.0.1:19083/rerank"


def test_build_rerank_model_unknown_backend_none():
    from codev_platform.agent.embed.registry import build_rerank_model
    assert build_rerank_model({"memory": {"rerank_model": {"backend": "bogus"}}}) is None


def test_build_index_none_when_chromadb_missing(monkeypatch):
    # chromadb 缺 → 直接 None(短路,不进 embedder)→ 召回退 local(降级)
    import importlib.util as iu
    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec",
                        lambda name: None if name == "chromadb" else real(name))
    from codev_platform.agent.memory_vector_chroma import build_memory_vector_index
    assert build_memory_vector_index({}) is None
