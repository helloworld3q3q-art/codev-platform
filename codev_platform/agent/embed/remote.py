"""RemoteEmbedder —— 调 chroma daemon 的 /embed 端点拿向量(共享那份 GPU 模型,不再 load 第二份)。

GPU 提速 payoff:agent-memory 自己不 load 模型,把文本发给已加载 Qwen 的 chroma daemon,GPU 上只一份。
stdlib urllib(无新依赖)。daemon 不可达 / 端点错 → encode 抛 → 上层(VectorScorer 吞 → 退关键词;
写时 embed 由 VectorSyncMemoryStore 吞)优雅降级。鉴权:passthrough(本机 loopback)无需头;token 模式
需带 Authorization(后续补,见 memory.embed.token)。
"""
from __future__ import annotations

import json
import urllib.request

from codev_platform.agent.memory_vector import Embedder, RerankModel


def _post_json(url: str, payload: dict, timeout: float, token: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# 单次 /embed 请求最多带多少文本: 既省 HTTP 往返(code_vec 大项目索引), 又把 daemon 那次
# GPU encode + 持锁时间收口 (太大批会长占共享 GPU 信号量, 拖慢并发 search_docs)。
_EMBED_HTTP_BATCH = 256


class RemoteEmbedder(Embedder):
    def __init__(self, url: str, *, timeout: float = 30.0, token: str | None = None) -> None:
        self._url = url
        self._timeout = timeout
        self._token = token

    def encode(self, text: str) -> list[float]:
        data = _post_json(self._url, {"text": text}, self._timeout, self._token)
        vecs = data.get("vectors")
        if not vecs:
            raise ValueError(f"remote embed 返回无 vectors: {data}")
        return vecs[0]

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """一次 /embed 带一子批文本 → daemon 一次 GPU encode 返回整批向量。
        子批 _EMBED_HTTP_BATCH 收口单请求大小, 不 N 次往返也不长占 GPU。"""
        out: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_HTTP_BATCH):
            chunk = texts[i:i + _EMBED_HTTP_BATCH]
            data = _post_json(self._url, {"texts": chunk}, self._timeout, self._token)
            vecs = data.get("vectors")
            if not vecs or len(vecs) != len(chunk):
                raise ValueError(f"remote embed 批量返回向量数不符: 期望 {len(chunk)} 得 {len(vecs) if vecs else 0}")
            out.extend(vecs)
        return out


class RemoteRerankModel(RerankModel):
    """调 chroma daemon /rerank,复用那份 GPU reranker。失败抛 → QwenReranker 吞(不动序,降级)。"""

    def __init__(self, url: str, *, timeout: float = 30.0, token: str | None = None) -> None:
        self._url = url
        self._timeout = timeout
        self._token = token

    def score(self, query: str, docs: list[str]) -> list[float]:
        data = _post_json(self._url, {"query": query, "docs": docs}, self._timeout, self._token)
        scores = data.get("scores")
        if scores is None:
            raise ValueError(f"remote rerank 返回无 scores: {data}")
        return scores
