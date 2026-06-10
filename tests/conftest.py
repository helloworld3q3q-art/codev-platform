"""pytest 共享夹具。

`_restore_web_singletons`(autouse): create_app(cfg) 会按 cfg 重绑 web 进程单例
(session_store / job_service / index_service / agent_client)。多个 web 测试共享同一进程时,
一个测试调 `create_app(cfg)`(如 test_web_foundation / test_web_contract)会把这些**活动单例**
换成该 cfg 专属后端并**持续到后续测试**, 而消费方(deps/authenticator 经 get_session_store())
读的是活动单例 → 后续测试(如 test_web_users 用 import 期 session_store 造登录态)出现 token 落
A 库、handler 查 B 库 → 403。account_store 已靠各测试的 reset_account_stores() 自愈, 本夹具给
**其余 web 单例**补同样的隔离: 每测试快照 + 还原。

**仅当相关模块已 import 时才动**(Windows 无 fastapi → web 模块从未 import → snap 为空 → 纯 no-op,
绝不影响非 web 测试的收集 / 运行)。
"""
from __future__ import annotations

import sys

import pytest

# 模块全限定名 → 该模块上承载活动单例的属性名。
_WEB_SINGLETONS = {
    "codev_platform.web.security.sessions": "session_store",
    "codev_platform.web.routes.jobs": "job_service",
    "codev_platform.web.routes.indexes": "index_service",
    "codev_platform.web.routes.agent": "agent_client",
    "codev_platform.web.routes.memory": "agent_client",
}


@pytest.fixture(autouse=True)
def _restore_web_singletons():
    snap = []
    for mod_name, attr in _WEB_SINGLETONS.items():
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, attr):
            snap.append((mod, attr, getattr(mod, attr)))
    yield
    for mod, attr, val in snap:
        setattr(mod, attr, val)
