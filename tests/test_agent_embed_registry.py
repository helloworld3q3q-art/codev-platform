"""嵌入模型 registry(P2a)—— config 选 backend + device 别名 + 缺依赖降级 + index 经 registry。"""
from __future__ import annotations

import importlib.util

import pytest

from codev_platform.agent.embed.registry import build_embedder

# 默认 qwen-local 需 sentence-transformers(runtime extra); 轻量 dev 缺则 skip 而非 fail(codex 审计 #9)。
_needs_st = pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="需 sentence-transformers(runtime extra), 轻量 dev 跳过",
)


@_needs_st
def test_default_backend_qwen_local():
    from codev_platform.agent.embed.qwen import QwenLocalEmbedder
    emb = build_embedder({})                       # 默认 qwen-local;WSL venv 有 sentence-transformers
    assert isinstance(emb, QwenLocalEmbedder)


def test_unknown_backend_returns_none():
    assert build_embedder({"memory": {"embed": {"backend": "bogus-model"}}}) is None


@_needs_st
def test_device_precedence_new_over_alias_over_default():
    assert build_embedder({"memory": {"embed": {"device": "cuda"}}})._device == "cuda"
    assert build_embedder({"memory": {"embed_device": "cuda:1"}})._device == "cuda:1"   # 旧别名
    assert build_embedder({})._device == "cpu"                                          # 默认


@_needs_st
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


# ---- 端口统一: remote 走 _bind_port(canonical 键)+ timeout config 驱动 ----

def test_remote_embedder_url_honors_canonical_port():
    # 只配 canonical mcp.platform_docs_sse_port(无 daemon.port)→ remote url 用它,不回落 18083
    from codev_platform.agent.embed.remote import RemoteEmbedder
    emb = build_embedder({"memory": {"embed": {"backend": "remote"}},
                          "mcp": {"platform_docs_sse_port": 29083}})
    assert isinstance(emb, RemoteEmbedder) and emb._url == "http://127.0.0.1:29083/embed"


def test_remote_rerank_url_honors_canonical_port():
    from codev_platform.agent.embed.registry import build_rerank_model
    from codev_platform.agent.embed.remote import RemoteRerankModel
    m = build_rerank_model({"mcp": {"platform_docs_sse_port": 29083}})
    assert isinstance(m, RemoteRerankModel) and m._url == "http://127.0.0.1:29083/rerank"


def test_remote_timeout_config_driven():
    emb = build_embedder({"memory": {"embed": {"backend": "remote", "timeout": 5.0}}})
    assert emb._timeout == 5.0
    assert build_embedder({"memory": {"embed": {"backend": "remote"}}})._timeout == 30.0  # 默认


def test_remote_rerank_failure_swallowed_by_qwen_reranker(monkeypatch):
    # NIT: 真实 RemoteRerankModel 远端失败(urlopen 抛)→ QwenReranker 吞 → 不动序(端到端降级)
    import urllib.error
    import urllib.request

    from codev_platform.agent.embed.remote import RemoteRerankModel
    from codev_platform.agent.memory_store import MemoryEntry
    from codev_platform.agent.recall.reranker import QwenReranker

    def boom(req, timeout=None):
        raise urllib.error.URLError("daemon down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    entries = [MemoryEntry(id="a", scope="personal", scope_ref="u", owner_user_id="u", content="x"),
               MemoryEntry(id="b", scope="personal", scope_ref="u", owner_user_id="u", content="y")]
    out = QwenReranker(RemoteRerankModel("http://127.0.0.1:1/rerank")).rerank("q", entries, top_k=8)
    assert [e.id for e in out] == ["a", "b"]   # 远端崩 → 原序返回, 不丢候选不报错


# ---- code_vec 专用 embedder: 索引侧用本机 GPU, 不经共享 daemon(防大批量死锁 daemon) ----

@_needs_st
def test_code_vec_embedder_defaults_local():
    """默认走本机 qwen-local(不经 daemon /embed), 与 agent-memory 默认 remote 区分。"""
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.agent.embed.qwen import QwenLocalEmbedder
    emb = build_code_vec_embedder({})
    assert isinstance(emb, QwenLocalEmbedder)


def test_code_vec_embedder_remote_override():
    """显式配 remote 仍可回退共享 daemon(单机省显存等场景)。"""
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.agent.embed.remote import RemoteEmbedder
    emb = build_code_vec_embedder({"recall": {"code_vec": {"embed_backend": "remote"}}})
    assert isinstance(emb, RemoteEmbedder)


@_needs_st
def test_code_vec_embedder_device_override():
    """recall.code_vec.embed_device 显式覆盖自动 cuda/cpu 探测。"""
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    emb = build_code_vec_embedder({"recall": {"code_vec": {"embed_device": "cpu"}}})
    assert emb._device == "cpu"
