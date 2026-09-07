"""首个平台管理员 bootstrap —— 装机播种 (plan §四 install-init + §5.2 platform_admin 两阶段)。

破鸡生蛋(建组织需超管 / 设超管需登录): 系统初次启动若该管理员用户尚不存在, 用 config/env 指定的
platform_admin 播种一个可登录用户(org + user[hashed pw] + org_member admin)。

幂等: 该用户已存在则跳过。密码明文不落库 / 不日志(security.md)。
口令来源: env CODEV_PLATFORM_ADMIN_PASSWORD > config.web.bootstrap_password > 默认 'admin'(loud warn)。
写入走 account_store 的 getter(内存或 PG, 由 bind_account_stores 决定)。
"""
from __future__ import annotations

import logging
import os

from codev_platform.core.config import get as _cfg_get
from codev_platform.core.platform_admin import platform_admin_ids
from codev_platform.web.domain.accounts import ROLE_ADMIN, STATUS_ACTIVE, Org, OrgMember, User
from codev_platform.web.repositories.account_store import (
    get_member_store,
    get_org_store,
    get_user_store,
)
from codev_platform.web.security.passwords import hash_password

_log = logging.getLogger("codev_platform.web")


def _admin_username(cfg: dict | None) -> str | None:
    explicit = _cfg_get(cfg or {}, "web.bootstrap_admin", None)
    if explicit:
        return str(explicit)
    ids = sorted(platform_admin_ids(cfg))
    return ids[0] if ids else None


def ensure_seed_admin(cfg: dict | None = None) -> str | None:
    """无该管理员用户时播种首个 platform_admin。返回播种的 username, 或 None(已存在/未配置)。"""
    username = _admin_username(cfg)
    if not username:
        return None
    users = get_user_store()
    if users.exists(username):
        return None  # 幂等
    org_id = str(_cfg_get(cfg or {}, "web.bootstrap_org", "default"))
    password = (
        os.environ.get("CODEV_PLATFORM_ADMIN_PASSWORD")
        or _cfg_get(cfg or {}, "web.bootstrap_password", None)
        or "admin"
    )
    if password == "admin":
        _log.warning(
            "[web] bootstrap admin '%s' 用默认口令 'admin' —— 生产务必设 CODEV_PLATFORM_ADMIN_PASSWORD",
            username,
        )
    orgs = get_org_store()
    if not orgs.exists(org_id):
        orgs.create(Org(code=org_id, name=org_id))
    users.create(User(
        username=username, password_hash=hash_password(password), org_id=org_id,
        status=STATUS_ACTIVE, display_name=username,
    ))
    get_member_store().upsert(OrgMember(org_id=org_id, username=username, role=ROLE_ADMIN))
    _log.info("[web] seeded platform_admin '%s' in org '%s'", username, org_id)
    return username
