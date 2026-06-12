"""Embedder.encode_batch: 默认逐条兜底 + RemoteEmbedder 真批量(子批 /embed, 省往返)。

code_vec 大项目索引 (ideas-v2 ~19万节点) 从"每节点一次 /embed HTTP"改成批量, 小时级 → 分钟级。
"""
from __future__ import annotations

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

    def fake_post(url, payload, timeout, token):
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

    def fake_post(url, payload, timeout, token):
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


def test_remote_encode_batch_mismatch_raises(monkeypatch):
    """daemon 返回向量数与请求文本数不符 → 抛 (防静默错配)。"""
    monkeypatch.setattr(remote_mod, "_post_json",
                        lambda *a, **k: {"vectors": [[1.0]]})  # 只返 1 个
    import pytest
    with pytest.raises(ValueError):
        RemoteEmbedder("http://x/embed").encode_batch(["a", "b", "c"])
