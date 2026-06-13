"""RemoteEmbedder —— 调 chroma daemon 的 /embed 端点拿向量(共享那份 GPU 模型,不再 load 第二份)。

GPU 提速 payoff:agent-memory 自己不 load 模型,把文本发给已加载 Qwen 的 chroma daemon,GPU 上只一份。
stdlib urllib(无新依赖)。daemon 不可达 / 端点错 → encode 抛 → 上层(VectorScorer 吞 → 退关键词;
写时 embed 由 VectorSyncMemoryStore 吞)优雅降级。鉴权:passthrough(本机 loopback)无需头;token 模式
需带 Authorization(后续补,见 memory.embed.token)。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from codev_platform.agent.memory_vector import Embedder, RerankModel


def _post_json(url: str, payload: dict, timeout: float, token: str | None,
               internal_call: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if internal_call:
        # 本机 loopback 内部调用信物: daemon 配了 internal_secret 时, /embed /rerank 的 loopback 豁免
        # 额外要求此头(防同机反代把远程请求伪装成 loopback 白嫖 GPU)。secret 只在本机内部传, 不过网络。
        headers["X-Internal-Call"] = internal_call
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# 单次 /embed 请求最多带多少文本: 既省 HTTP 往返(code_vec 大项目索引), 又把 daemon 那次
# GPU encode + 持锁时间收口。64 平衡: 省掉绝大多数往返, 单次 GPU encode 仍快(实测 ~1.6s),
# 且频繁释放共享 GPU 信号量不长时间饿死并发 search_docs。
_EMBED_HTTP_BATCH = 64
# 单次 /embed 调用超时 + 重试: 正常 ~1.6s, 30s 给足余量又能在 daemon 偶发卡顿时快速失败转重试。
_EMBED_CALL_TIMEOUT = 30.0
_EMBED_RETRIES = 4
_EMBED_RETRY_BACKOFF = 3.0  # 秒, 线性递增 (3/6/9)


class RemoteEmbedder(Embedder):
    def __init__(self, url: str, *, timeout: float = 30.0, token: str | None = None,
                 internal_call: str | None = None) -> None:
        self._url = url
        self._timeout = timeout
        self._token = token
        self._internal_call = internal_call

    def encode(self, text: str) -> list[float]:
        data = _post_json(self._url, {"text": text}, self._timeout, self._token, self._internal_call)
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
            vecs = self._post_embed_retry({"texts": chunk}, expect=len(chunk))
            out.extend(vecs)
        return out

    def _post_embed_retry(self, payload: dict, *, expect: int) -> list[list[float]]:
        """单次 /embed 带重试: daemon 偶发卡顿 (GPU 信号量被并发 search_docs 长占) 不该让整个
        大项目索引 (~19万节点) 失败。短超时快速失败 + 退避重试; 瞬时卡顿在重试时已恢复
        (实测 daemon 连发 100 次稳定, 卡是瞬时的)。彻底失败才上抛, 由 checkpoint 保住已完成进度。"""
        last_exc: Exception | None = None
        for attempt in range(_EMBED_RETRIES):
            try:
                data = _post_json(self._url, payload, _EMBED_CALL_TIMEOUT, self._token, self._internal_call)
                vecs = data.get("vectors")
                if not vecs or len(vecs) != expect:
                    raise ValueError(f"remote embed 返回向量数不符: 期望 {expect} 得 {len(vecs) if vecs else 0}")
                return vecs
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt < _EMBED_RETRIES - 1:
                    time.sleep(_EMBED_RETRY_BACKOFF * (attempt + 1))
        raise RuntimeError(f"remote embed 重试 {_EMBED_RETRIES} 次仍失败: {last_exc}") from last_exc


class RemoteRerankModel(RerankModel):
    """调 chroma daemon /rerank,复用那份 GPU reranker。失败抛 → QwenReranker 吞(不动序,降级)。"""

    def __init__(self, url: str, *, timeout: float = 30.0, token: str | None = None,
                 internal_call: str | None = None) -> None:
        self._url = url
        self._timeout = timeout
        self._token = token
        self._internal_call = internal_call

    def score(self, query: str, docs: list[str]) -> list[float]:
        data = _post_json(self._url, {"query": query, "docs": docs}, self._timeout,
                          self._token, self._internal_call)
        scores = data.get("scores")
        if scores is None:
            raise ValueError(f"remote rerank 返回无 scores: {data}")
        return scores
