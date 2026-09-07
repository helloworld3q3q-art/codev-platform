"""user 身份解析 — 与 project_id 对称(memory 权限模型 M1 地基).

agent 请求上下文 = (user_id, project_id)。本模块只解析"谁",不做鉴权(M5+ 才拦截)。
单人期默认 'local',接入多人时通过 header / env / config 区分。
"""
from __future__ import annotations

import os
import re

ENV_VAR = "CODEV_USER_ID"
DEFAULT_USER = "local"
DEFAULT_ORG = "default"
_VALID = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


def validate(user_id: str) -> str:
    uid = (user_id or "").strip()
    if not _VALID.match(uid):
        raise ValueError(f"非法 user_id: {user_id!r}(允许 字母/数字/.-_,1-64 位)")
    return uid


def resolve_local(default: str = DEFAULT_USER) -> str:
    """client / 单进程端解析:env > default。"""
    v = os.environ.get(ENV_VAR)
    return validate(v) if v else default


def resolve_from_request(headers, default: str = DEFAULT_USER) -> str:
    """server 端解析:X-User-Id header > env > default。

    单人期缺 header 不报错(回退 default);M5 起鉴权时再强制。
    headers 大小写不敏感(对齐 project_id.resolve_from_request 的处理)。
    """
    uid = None
    if hasattr(headers, "get"):
        for key in ("X-User-Id", "x-user-id", "X-USER-ID"):
            v = headers.get(key)
            if v:
                uid = v
                break
    elif headers:
        for k, v in dict(headers).items():
            if k.lower() == "x-user-id" and v:
                uid = v
                break
    if uid:
        return validate(uid)
    return resolve_local(default)


def resolve_org_from_request(headers, default: str = DEFAULT_ORG) -> str:
    """server 端解析当前 org:X-Org-Id header > default。

    org 是请求级(plan §3.4:一人多 org 无法从 user 推,故每请求带)。chat / memory 路由
    共用本函数,避免各自复制解析逻辑(M5 鉴权在此单点加 (org,user)∈org_members 校验)。
    单人期缺 header 回退 'default';多 org 启用时改为强制(缺失拒绝)。
    """
    if hasattr(headers, "get"):
        for key in ("X-Org-Id", "x-org-id", "X-ORG-ID"):
            v = headers.get(key)
            if v:
                return validate(v)
    elif headers:
        for k, v in dict(headers).items():
            if k.lower() == "x-org-id" and v:
                return validate(v)
    return default
