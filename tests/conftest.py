"""pytest 共享夹具。

`_restore_web_singletons`(autouse): create_app(cfg) 会按 cfg 重绑 web 进程单例
(session_store / job_service / index_service / agent_client)。多个 web 测试共享同一进程时,
一个测试调 `create_app(cfg)`(如 test_web_foundation / test_web_contract)会把这些**活动单例**
换成该 cfg 专属后端并**持续到后续测试**, 而消费方(deps/authenticator 经 get_session_store())
读的是活动单例 → 后续测试(如 test_web_users 用 import 期 session_store 造登录态)出现 token 落
A 库、handler 查 B 库 → 403。account_store 已靠各测试的 reset_account_stores() 自愈, 本夹具给
**其余 web 单例**补同样的隔离: 每测试快照 + 还原。

⚠️ **勿把 account_store 加进 `_WEB_SINGLETONS`**: 它的 `_active` 是**原地 mutate 的 dict**
(`bind_account_stores`/`reset_account_stores` 都 `_active.update(...)`, 不重新赋值), 而本夹具的
getattr/setattr 快照只对"重新赋值"的单例有效 —— 对原地 mutate 的 dict, 快照拿到同一引用、setattr
还原是 no-op = **假覆盖**。account_store 的隔离只能走 `reset_account_stores()`(故意不在此)。

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


@pytest.fixture(autouse=True)
def _isolate_host_config(tmp_path_factory, monkeypatch):
    """测试与宿主机 ~/.codev-platform/config.json 隔离。

    部署机(如 WSL 多组织服务器)全局 config 是 auth_mode=token, 而 web 测试按 passthrough 写
    (只给 build_app 注入 _CFG, 但 handler 的 require_project_access / acl.can_access 故意重读
    **全局** load_config() 作单一真值源)→ 在 token 机上 web 请求全 403(56 web 测试假红, 与代码无关)。
    指向不存在路径 → load_config() 回落默认(passthrough)→ 套件 host-independent(dev/CI/WSL 一致)。
    测试体内若显式 setenv 覆盖(意图测 token 模式)其 monkeypatch 后跑、胜出, 不被本默认压住。"""
    absent = tmp_path_factory.mktemp("nocfg") / "absent.json"   # 不创建 = 不存在 → 默认
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(absent))


@pytest.fixture(autouse=True)
def _isolate_recall_trace(tmp_path_factory, monkeypatch):
    """recall_code 每次查询 best-effort 写 trace 到 data_root/recall_trace(Phase 8 观测)。测试重定向到
    tmp, 防 recall service/weights 等测试把垃圾 trace 写进**真实平台 baseline**(measure-first 数据被污染)。
    recall.observability 仅 stdlib 依赖(不引 web/重栈), 直接 patch 其 _trace_dir 安全、不影响非 recall 测试。"""
    d = tmp_path_factory.mktemp("recall_trace")
    monkeypatch.setattr("codev_platform.recall.observability._trace_dir", lambda: d)
