"""platform_admin 判定 —— §5.2 方案 C 的两阶段真值源 (config 白名单 → PG 表)。

platform_admin = org-less 跨组织超管, 不进 scope×rank 角色阶梯, 而是身份级 bypass 标志
(与 acl.py 的 all_projects 同范式)。第一阶段: 装机由 config/env 白名单播种 (启动不依赖 DB,
破 "建组织需超管/设超管需登录" 鸡生蛋); 第二阶段: 迁 PG platform_admins 表 + Web 管理 (TODO)。

纯函数, 只读 cfg + user_id, 无 IO。
"""
from __future__ import annotations

import os

from codev_platform.core.config import get as _cfg_get

ENV_VAR = "CODEV_PLATFORM_ADMINS"  # 逗号分隔 user_id


def platform_admin_ids(cfg: dict | None = None) -> frozenset[str]:
    """平台超管 user_id 集合: env CODEV_PLATFORM_ADMINS > config.platform_admins。

    阶段二接 PG 后, 这里改为 (config 白名单) ∪ (PG platform_admins 表), 调用方无感。
    """
    ids: set[str] = set()
    raw_env = os.environ.get(ENV_VAR)
    if raw_env:
        ids.update(x.strip() for x in raw_env.split(",") if x.strip())
    raw_cfg = _cfg_get(cfg or {}, "platform_admins", []) or []
    if isinstance(raw_cfg, (list, tuple)):
        ids.update(str(x).strip() for x in raw_cfg if str(x).strip())
    return frozenset(ids)


def is_platform_admin(cfg: dict | None, user_id: str | None) -> bool:
    """user_id 是否平台超管。user_id 空 → False (安全默认)。"""
    if not user_id:
        return False
    return user_id in platform_admin_ids(cfg)
