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


# ---- code_vec 专用 embedder: 默认 remote 复用 daemon; 专用 GPU 节点可 opt-in qwen-local ----

def test_code_vec_embedder_defaults_remote(monkeypatch):
    """默认走 remote(复用共享 daemon GPU, 不在 worker 塞第二份模型; 死锁已由 daemon wait_for 根治)。"""
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.agent.embed.remote import RemoteEmbedder
    from codev_platform.agent.embed import remote_ready

    calls = []

    def fake_ensure(cfg):
        calls.append(cfg)
        return {"name": "platform-docs", "action": "already-up", "status": "ok"}

    monkeypatch.setattr(remote_ready, "ensure_code_vec_remote_daemon", fake_ensure)
    assert isinstance(build_code_vec_embedder({}), RemoteEmbedder)
    assert calls == [{}]


def test_code_vec_remote_cpu_defaults_reduce_batch_and_extend_timeout(monkeypatch):
    """daemon 明确走 CPU 时，code_vec remote 默认降批量/延长超时，避免重建反复 timeout。"""
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.agent.embed import remote_ready

    monkeypatch.setenv("PLATFORM_EMBED_DEVICE", "cpu")
    monkeypatch.setattr(
        remote_ready,
        "ensure_code_vec_remote_daemon",
        lambda cfg: {"name": "platform-docs", "action": "already-up", "status": "ok"},
    )

    emb = build_code_vec_embedder({})

    assert emb._batch_size == 2
    assert emb._timeout == 180.0


def test_code_vec_embedder_unknown_backend_returns_none(monkeypatch):
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.agent.embed import remote_ready

    monkeypatch.setattr(remote_ready, "ensure_code_vec_remote_daemon",
                        lambda cfg: (_ for _ in ()).throw(AssertionError("should not guard")))

    assert build_code_vec_embedder({"recall": {"code_vec": {"embed_backend": "bogus"}}}) is None


def test_code_vec_embedder_qwen_local_does_not_guard_without_dependency(monkeypatch):
    import importlib.util as iu

    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.agent.embed import remote_ready

    real = iu.find_spec
    monkeypatch.setattr(remote_ready, "ensure_code_vec_remote_daemon",
                        lambda cfg: (_ for _ in ()).throw(AssertionError("should not guard")))
    monkeypatch.setattr(iu, "find_spec",
                        lambda name: None if name == "sentence_transformers" else real(name))

    assert build_code_vec_embedder({"recall": {"code_vec": {"embed_backend": "qwen-local"}}}) is None


@_needs_st
def test_code_vec_embedder_local_optin():
    """专用 GPU 索引节点 opt-in qwen-local: 本机直跑、与服务隔离。"""
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    from codev_platform.agent.embed.qwen import QwenLocalEmbedder
    emb = build_code_vec_embedder({"recall": {"code_vec": {"embed_backend": "qwen-local"}}})
    assert isinstance(emb, QwenLocalEmbedder)


@_needs_st
def test_code_vec_embedder_device_override():
    """qwen-local opt-in 下 recall.code_vec.embed_device 覆盖自动 cuda/cpu 探测。"""
    from codev_platform.agent.embed.registry import build_code_vec_embedder
    emb = build_code_vec_embedder({"recall": {"code_vec": {"embed_backend": "qwen-local", "embed_device": "cpu"}}})
    assert emb._device == "cpu"


def test_code_vec_remote_daemon_guard_disabled():
    from codev_platform.agent.embed.remote_ready import ensure_code_vec_remote_daemon

    result = ensure_code_vec_remote_daemon(
        {"recall": {"code_vec": {"ensure_remote_daemon": False}}}
    )

    assert result["action"] == "disabled"


def test_code_vec_remote_daemon_guard_external_url(monkeypatch):
    from codev_platform.agent.embed.remote_ready import ensure_code_vec_remote_daemon
    from codev_platform.mcp_serve import MCPEndpoint

    ep = MCPEndpoint(name="platform-docs", kind="chroma", port=18083, cmd=["py"])
    monkeypatch.setattr("codev_platform.mcp_serve.iter_endpoints", lambda cfg: [ep])
    monkeypatch.setattr("codev_platform.mcp_serve.probe",
                        lambda endpoint: (_ for _ in ()).throw(AssertionError("should not probe")))

    result = ensure_code_vec_remote_daemon({"memory": {"embed": {"url": "http://gpu-host:18083/embed"}}})

    assert result["action"] == "external-url"


def test_code_vec_remote_daemon_guard_manages_explicit_local_url(monkeypatch):
    from codev_platform.agent.embed import remote_ready
    from codev_platform.mcp_serve import MCPEndpoint

    ep = MCPEndpoint(name="platform-docs", kind="chroma", port=28083, cmd=["py"])
    probes = iter(["down", "ok"])
    monkeypatch.setattr("codev_platform.mcp_serve.iter_endpoints", lambda cfg: [ep])
    monkeypatch.setattr("codev_platform.mcp_serve.probe", lambda endpoint: next(probes))
    monkeypatch.setattr(remote_ready, "spawn_endpoint",
                        lambda endpoint: {"name": endpoint.name,
                                          "action": "spawned",
                                          "status": "starting",
                                          "pid": 456})

    result = remote_ready.ensure_code_vec_remote_daemon({
        "mcp": {"platform_docs_sse_port": 28083},
        "memory": {"embed": {"url": "http://localhost:28083/embed"}},
    })

    assert result["action"] == "spawned"
    assert result["status"] == "ok"


def test_code_vec_remote_daemon_guard_spawns_and_waits(monkeypatch):
    from codev_platform.agent.embed import remote_ready
    from codev_platform.mcp_serve import MCPEndpoint

    ep = MCPEndpoint(name="platform-docs", kind="chroma", port=18083, cmd=["py"])
    probes = iter(["down", "ok"])
    monkeypatch.setattr("codev_platform.mcp_serve.iter_endpoints", lambda cfg: [ep])
    monkeypatch.setattr("codev_platform.mcp_serve.probe", lambda endpoint: next(probes))
    monkeypatch.setattr(remote_ready, "spawn_endpoint",
                        lambda endpoint: {"name": endpoint.name,
                                          "action": "spawned",
                                          "status": "starting",
                                          "pid": 123})

    result = remote_ready.ensure_code_vec_remote_daemon({})

    assert result["action"] == "spawned"
    assert result["status"] == "ok"
    assert result["pid"] == 123


def test_code_vec隔离attempt禁止自启常驻daemon(monkeypatch, tmp_path):
    """attempt cgroup 内的后台服务会阻止 containment 收口，必须失败关闭。"""
    from codev_platform.agent.embed import remote_ready
    from codev_platform.mcp_serve import MCPEndpoint

    ep = MCPEndpoint(name="platform-docs", kind="chroma", port=18083, cmd=["py"])
    monkeypatch.setenv("CODEV_REINDEX_CGROUP_ROOT", str(tmp_path))
    monkeypatch.setattr("codev_platform.mcp_serve.iter_endpoints", lambda cfg: [ep])
    monkeypatch.setattr("codev_platform.mcp_serve.probe", lambda endpoint: "down")
    monkeypatch.setattr(
        remote_ready,
        "spawn_endpoint",
        lambda endpoint: (_ for _ in ()).throw(AssertionError("隔离 attempt 不得 spawn")),
    )

    with pytest.raises(RuntimeError, match="隔离 attempt.*不得自启"):
        remote_ready.ensure_code_vec_remote_daemon({})


def test_code_vec隔离attempt复用已就绪daemon(monkeypatch, tmp_path):
    """隔离执行只禁止创建常驻子进程，不得拒绝复用受管服务。"""
    from codev_platform.agent.embed import remote_ready
    from codev_platform.mcp_serve import MCPEndpoint

    ep = MCPEndpoint(name="platform-docs", kind="chroma", port=18083, cmd=["py"])
    monkeypatch.setenv("CODEV_REINDEX_CGROUP_ROOT", str(tmp_path))
    monkeypatch.setattr("codev_platform.mcp_serve.iter_endpoints", lambda cfg: [ep])
    monkeypatch.setattr("codev_platform.mcp_serve.probe", lambda endpoint: "ok")
    monkeypatch.setattr(
        remote_ready,
        "spawn_endpoint",
        lambda endpoint: (_ for _ in ()).throw(AssertionError("已就绪时不得 spawn")),
    )

    assert remote_ready.ensure_code_vec_remote_daemon({})["action"] == "already-up"


def test_code_vec_remote_daemon_guard_timeout(monkeypatch):
    from codev_platform.agent.embed import remote_ready
    from codev_platform.mcp_serve import MCPEndpoint

    ep = MCPEndpoint(name="platform-docs", kind="chroma", port=18083, cmd=["py"])
    monkeypatch.setattr("codev_platform.mcp_serve.iter_endpoints", lambda cfg: [ep])
    monkeypatch.setattr("codev_platform.mcp_serve.probe", lambda endpoint: "down")
    monkeypatch.setattr("codev_platform.mcp_serve.collect_facts",
                        lambda endpoint, cfg: {
                            "port_open": False,
                            "healthz_ok": False,
                            "unit_active": None,
                            "dep_ok": True,
                            "db_present": True,
                        })
    monkeypatch.setattr("codev_platform.mcp_serve.diagnose_down",
                        lambda endpoint, **facts: "端口未监听")
    monkeypatch.setattr(remote_ready, "spawn_endpoint",
                        lambda endpoint: {"name": endpoint.name,
                                          "action": "spawned",
                                          "status": "starting",
                                          "pid": 123})

    with pytest.raises(RuntimeError, match="not ready"):
        remote_ready.ensure_code_vec_remote_daemon(
            {"recall": {"code_vec": {"remote_daemon_wait_sec": 0}}}
        )


@pytest.mark.parametrize(
    ("spawn_result", "match"),
    [
        ({"name": "platform-docs", "action": "fail", "status": "down", "error": "missing exe"}, "missing exe"),
        ({"name": "platform-docs", "action": "skip", "status": "down", "note": "external"}, "external"),
    ],
)
def test_code_vec_remote_daemon_guard_spawn_failure(monkeypatch, spawn_result, match):
    from codev_platform.agent.embed import remote_ready
    from codev_platform.mcp_serve import MCPEndpoint

    ep = MCPEndpoint(name="platform-docs", kind="chroma", port=18083, cmd=["py"])
    monkeypatch.setattr("codev_platform.mcp_serve.iter_endpoints", lambda cfg: [ep])
    monkeypatch.setattr("codev_platform.mcp_serve.probe", lambda endpoint: "down")
    monkeypatch.setattr(remote_ready, "spawn_endpoint", lambda endpoint: spawn_result)

    with pytest.raises(RuntimeError, match=match):
        remote_ready.ensure_code_vec_remote_daemon({})
