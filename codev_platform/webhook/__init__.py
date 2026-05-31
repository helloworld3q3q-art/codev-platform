"""VCS webhook 接收 —— push 即 enqueue reindex (与本地 hook 同源, 都进写队列)。

分层 (依赖单向, 解耦):
  providers.py  适配层  WebhookProvider 协议 + Gitea/GitLab provider + registry
                        (加 VCS = 写一个 provider + register; 验签/payload 差异全封在此)
  server.py     编排层  ASGI 接收器: verify→parse→repo映射project→分scope→reindex.enqueue

不碰 reindex 怎么跑 (reindex worker); webhook 自带验签, 与 MCP gateway token 鉴权独立。
"""
from __future__ import annotations

from codev_platform.webhook.providers import PushEvent, WebhookProvider, get_provider, names, register

__all__ = ["PushEvent", "WebhookProvider", "get_provider", "names", "register"]
