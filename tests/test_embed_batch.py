"""Embedder.encode_batch: 默认逐条兜底 + RemoteEmbedder 真批量(子批 /embed, 省往返)。

code_vec 大项目索引 (sample-project-beta ~19万节点) 从"每节点一次 /embed HTTP"改成批量, 小时级 → 分钟级。
"""
from __future__ import annotations

import pytest

from codev_platform.agent.memory_vector import Embedder
from codev_platform.agent.embed import remote as remote_mod
from codev_platform.agent.embed.remote import RemoteEmbedder, _EMBED_HTTP_BATCH


class _StubEmbedder(Embedder):
    """只实现 encode; encode_batch 走 ABC 默认(逐条)。"""
    def __init__(self):
        self.calls = 0

    def encode(self, text: str):
        self.calls += 1
        return [float(len(text))]


def test_default_encode_batch_falls_back_per_item():
    e = _StubEmbedder()
    out = e.encode_batch(["a", "bb", "ccc"])
    assert out == [[1.0], [2.0], [3.0]]
    assert e.calls == 3  # 默认实现逐条


def test_default_encode_batch_empty():
    assert _StubEmbedder().encode_batch([]) == []


def test_remote_encode_batch_one_call_per_subbatch(monkeypatch):
    """子批 <= _EMBED_HTTP_BATCH 时只发一次 /embed, 带 texts 列表。"""
    seen = []

    def fake_post(url, payload, timeout, token, internal_call=None):
        seen.append(payload)
        return {"vectors": [[1.0, 2.0]] * len(payload["texts"])}

    monkeypatch.setattr(remote_mod, "_post_json", fake_post)
    emb = RemoteEmbedder("http://x/embed")
    out = emb.encode_batch(["a", "b", "c"])
    assert len(out) == 3 and all(v == [1.0, 2.0] for v in out)
    assert len(seen) == 1                      # 一次 HTTP
    assert seen[0]["texts"] == ["a", "b", "c"]  # 带 texts 批量, 非逐条 text


def test_remote_encode_batch_chunks_large_input(monkeypatch):
    """超过子批阈值 → 切多次 /embed, 每次 <= _EMBED_HTTP_BATCH, 向量顺序拼回。"""
    calls = []

    def fake_post(url, payload, timeout, token, internal_call=None):
        n = len(payload["texts"])
        calls.append(n)
        # 用文本内容回填可校验顺序
        return {"vectors": [[float(t)] for t in payload["texts"]]}

    monkeypatch.setattr(remote_mod, "_post_json", fake_post)
    emb = RemoteEmbedder("http://x/embed")
    n = _EMBED_HTTP_BATCH * 2 + 5
    texts = [str(i) for i in range(n)]
    out = emb.encode_batch(texts)
    assert len(out) == n
    assert out == [[float(i)] for i in range(n)]      # 顺序正确
    assert calls == [_EMBED_HTTP_BATCH, _EMBED_HTTP_BATCH, 5]  # 切 3 批
    assert max(calls) <= _EMBED_HTTP_BATCH             # 单请求收口


def test_remote_encode_batch_uses_configured_batch_and_timeout(monkeypatch):
    """CPU daemon 可把 code_vec 单请求降批量并延长超时，避免 64 条/30s 在 CPU 上反复 timeout。"""
    calls = []

    def fake_post(url, payload, timeout, token, internal_call=None):
        calls.append((list(payload["texts"]), timeout))
        return {"vectors": [[1.0]] * len(payload["texts"])}

    monkeypatch.setattr(remote_mod, "_post_json", fake_post)
    emb = RemoteEmbedder("http://x/embed", timeout=123.0, batch_size=2)

    assert emb.encode_batch(["a", "b", "c"]) == [[1.0], [1.0], [1.0]]
    assert calls == [(["a", "b"], 123.0), (["c"], 123.0)]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"batch_size": 0},
        {"batch_size": True},
        {"batch_size": "2"},
        {"timeout": 0},
        {"timeout": float("inf")},
        {"timeout": True},
    ],
)
def test_remote_embedder_rejects_invalid_runtime_limits(kwargs):
    """错误限流配置必须构造期失败，不能到长任务中途才以切片或网络异常暴露。"""
    with pytest.raises((TypeError, ValueError)):
        RemoteEmbedder("http://x/embed", **kwargs)


def test_remote_encode_batch_mismatch_raises(monkeypatch):
    """daemon 返回向量数与请求文本数不符 → 抛 (防静默错配)。"""
    monkeypatch.setattr(remote_mod, "_post_json",
                        lambda *a, **k: {"vectors": [[1.0]]})  # 只返 1 个
    import pytest
    with pytest.raises(ValueError):
        RemoteEmbedder("http://x/embed").encode_batch(["a", "b", "c"])


def test_remote_encode_batch_retries_transient_timeout(monkeypatch):
    """前两次 /embed 超时, 第三次成功 → 重试吃掉瞬时卡顿, 不让整个索引失败。"""
    monkeypatch.setattr(remote_mod.time, "sleep", lambda *_: None)  # 不真睡
    calls = {"n": 0}

    def flaky(url, payload, timeout, token, internal_call=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("timed out")
        return {"vectors": [[9.0]] * len(payload["texts"])}

    monkeypatch.setattr(remote_mod, "_post_json", flaky)
    out = RemoteEmbedder("http://x/embed").encode_batch(["a", "b"])
    assert out == [[9.0], [9.0]]
    assert calls["n"] == 3  # 失败2次 + 成功1次


def test_remote_encode_batch_raises_after_exhausting_retries(monkeypatch):
    """持续超时 → 重试用尽后上抛 (由上层 checkpoint 保住已完成进度)。"""
    monkeypatch.setattr(remote_mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(remote_mod, "_post_json",
                        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timed out")))
    import pytest
    with pytest.raises(RuntimeError):
        RemoteEmbedder("http://x/embed").encode_batch(["a"])


def test_remote_embedder_sends_internal_call_header(monkeypatch):
    """配了 internal_call → /embed 请求带 X-Internal-Call 信物(daemon loopback 豁免据此放行,
    防同机反代白嫖)。不配 → 不带头(单机 passthrough 纯 loopback)。验真实 urlopen 收到的 header。"""
    seen = {}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"vectors": [[1.0]]}'

    def fake_urlopen(req, timeout=None):
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _Resp()

    monkeypatch.setattr(remote_mod.urllib.request, "urlopen", fake_urlopen)
    RemoteEmbedder("http://x/embed", internal_call="s3cr3t").encode("hi")
    assert seen["headers"].get("X-internal-call".lower()) == "s3cr3t"
    seen.clear()
    RemoteEmbedder("http://x/embed").encode("hi")   # 不配 → 不带头
    assert "x-internal-call" not in seen["headers"]


def test_qwen_local_init_sets_model_none_for_lazy_load():
    """回归: QwenLocalEmbedder.__init__ 必须设 self._model=None。否则 _ensure() 首次 encode 读
    `self._model is None` 即 AttributeError → qwen-local embedder 全路径崩(code_vec query 侧 /
    agent-memory 向量打分)。712588e 加 batch_size 时曾误删该行, 且原测试只覆盖 RemoteEmbedder 漏网。
    init 不 load 模型(lazy), 故无需 sentence_transformers 即可断言此不变量。"""
    from codev_platform.agent.embed.qwen import QwenLocalEmbedder
    e = QwenLocalEmbedder("/nonexistent/model", device="cpu", batch_size=8)
    assert e._model is None       # lazy-load 前置不变量(被删则此处 AttributeError)
    assert e._batch_size == 8
