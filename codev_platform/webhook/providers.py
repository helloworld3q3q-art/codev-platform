"""webhook 来源适配 —— 按 VCS 协议族分 provider (策略接口 + registry)。

加一个 VCS (GitLab / GitHub / Gitee...) = 写一个 WebhookProvider + register() 一行,
server / queue / 项目映射 全零改 (同 agent-provider / reindex-runners 铁律, 零 if-else)。

provider 只管两件事, 不碰队列 / project 映射 / scope 分类 (那些在 server 层):
  1. verify(): 验签 (各 VCS 机制不同: Gitea=HMAC body / GitLab=明文 token / GitHub=HMAC)
  2. parse():  把 push payload 解析成中性 PushEvent (repo 标识 + 改动文件)
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class PushEvent:
    """中性 push 事件 —— 各 VCS provider 解析后的统一产物 (server 只认它)。"""
    repo: str               # 仓标识 owner/name, 供 server 映射 project_id
    changed_files: list[str]


@runtime_checkable
class WebhookProvider(Protocol):
    name: str  # URL 路径段 + registry key: gitea / gitlab / github

    def verify(self, headers: Mapping[str, str], body: bytes, secret: str) -> bool:
        """纯验签 (各 VCS 机制不同)。secret 空时 fail-closed (HMAC/token 比对自然失败 → False);
        放行策略 (本机信任 opt-out) 在 server 层 (webhook.allow_insecure), provider 不做策略。"""
        ...

    def parse(self, headers: Mapping[str, str], payload: dict) -> "PushEvent | None":
        """push 事件 → PushEvent; 非 push (ping / 其它事件) 返回 None。"""
        ...


_REGISTRY: dict[str, "WebhookProvider"] = {}


def register(provider: "WebhookProvider") -> None:
    _REGISTRY[provider.name] = provider


def get_provider(name: str) -> "WebhookProvider | None":
    return _REGISTRY.get(name)


def names() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def _hdr(headers: Mapping[str, str], name: str) -> str | None:
    if hasattr(headers, "get"):
        return headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
    return None


def _union_changed(commits: list | None) -> list[str]:
    """合并各 commit 的 added/modified/removed (保序去重)。Gitea/GitLab 字段名一致。"""
    seen: list[str] = []
    for c in commits or []:
        for key in ("added", "modified", "removed"):
            for f in (c.get(key) or []):
                if f not in seen:
                    seen.append(f)
    return seen


class GiteaProvider:
    name = "gitea"

    def verify(self, headers: Mapping[str, str], body: bytes, secret: str) -> bool:
        sig = _hdr(headers, "X-Gitea-Signature") or ""
        mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, sig)

    def parse(self, headers: Mapping[str, str], payload: dict) -> "PushEvent | None":
        if (_hdr(headers, "X-Gitea-Event") or "").lower() != "push":
            return None
        repo = ((payload.get("repository") or {}).get("full_name") or "").strip()
        if not repo:
            return None
        return PushEvent(repo=repo, changed_files=_union_changed(payload.get("commits")))


class GitlabProvider:
    """GitLab 适配 (现在服务器是 Gitea, 此 provider 为后续切 GitLab 预备; 部署 GitLab 时验一次)。

    与 Gitea 的差异全封在此: 验签是 X-Gitlab-Token **明文比对** (非 HMAC), event 头
    'Push Hook', repo 取 project.path_with_namespace。证明 provider 抽象可无痛扩展。
    """
    name = "gitlab"

    def verify(self, headers: Mapping[str, str], body: bytes, secret: str) -> bool:
        token = _hdr(headers, "X-Gitlab-Token") or ""
        return hmac.compare_digest(token, secret)

    def parse(self, headers: Mapping[str, str], payload: dict) -> "PushEvent | None":
        if (_hdr(headers, "X-Gitlab-Event") or "") != "Push Hook":
            return None
        repo = ((payload.get("project") or {}).get("path_with_namespace") or "").strip()
        if not repo:
            return None
        return PushEvent(repo=repo, changed_files=_union_changed(payload.get("commits")))


register(GiteaProvider())
register(GitlabProvider())
