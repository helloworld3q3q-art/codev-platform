"""RemoteEmbedder —— 调 chroma daemon 的 /embed 端点拿向量(共享那份 GPU 模型,不再 load 第二份)。

GPU 提速 payoff:agent-memory 自己不 load 模型,把文本发给已加载 Qwen 的 chroma daemon,GPU 上只一份。
stdlib urllib(无新依赖)。daemon 不可达 / 端点错 → encode 抛 → 上层(VectorScorer 吞 → 退关键词;
写时 embed 由 VectorSyncMemoryStore 吞)优雅降级。鉴权:passthrough(本机 loopback)无需头;token 模式
需带 Authorization(后续补,见 memory.embed.token)。
"""
from __future__ import annotations

import json
import urllib.request

from codev_platform.agent.memory_vector import Embedder


class RemoteEmbedder(Embedder):
    def __init__(self, url: str, *, timeout: float = 30.0, token: str | None = None) -> None:
        self._url = url
        self._timeout = timeout
        self._token = token

    def encode(self, text: str) -> list[float]:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(
            self._url, data=json.dumps({"text": text}).encode("utf-8"),
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vecs = data.get("vectors")
        if not vecs:
            raise ValueError(f"remote embed 返回无 vectors: {data}")
        return vecs[0]
