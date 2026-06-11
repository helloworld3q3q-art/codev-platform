"""account_store 密码 set + `org set-password` CLI(web 控制台登录密码,不造 fork)。

双 store 关系: users.password_hash 一列, 只 account_store(A)写; set_password 定向 UPDATE 该列,
保住 RbacStore(B)add-user 设的 display_name 等(同表互补)。MCP token 接入不需要密码。
"""
from __future__ import annotations

import argparse

from codev_platform.web.domain.accounts import User
from codev_platform.web.repositories import account_store as acc
from codev_platform.web.repositories.account_store import UserStore
from codev_platform.web.security.passwords import hash_password, verify_password


def test_set_password_updates_and_preserves_other_fields():
    s = UserStore()
    s.create(User(username="alice", password_hash=hash_password("old"), org_id="acme",
                  display_name="Alice", email="a@x"))
    assert s.set_password("alice", hash_password("new")) is True
    u = s.get("alice")
    assert verify_password("new", u.password_hash) and not verify_password("old", u.password_hash)
    assert u.display_name == "Alice" and u.email == "a@x"   # 其它字段保住(非全量覆盖)


def test_set_password_missing_user_returns_false():
    assert UserStore().set_password("ghost", "h") is False


def test_cli_set_password_flow(monkeypatch):
    from codev_platform.ops import org as org_cli
    acc.reset_account_stores()   # mem 隔离
    # 模拟 B(org add-user)已建行(display_name 有, password 无)
    acc.get_user_store().create(User(username="bob", password_hash="", org_id="acme",
                                     display_name="Bob", email=""))
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})   # 强制 mem 后端

    rc = org_cli.cmd_org(argparse.Namespace(action="set-password", target="bob", password="newpw"))
    assert rc == 0
    u = acc.get_user_store().get("bob")
    assert verify_password("newpw", u.password_hash) and u.display_name == "Bob"   # 密码设上 + 名保住

    # 不存在的 user → 非零 + 提示先 add-user
    rc2 = org_cli.cmd_org(argparse.Namespace(action="set-password", target="ghost", password="x"))
    assert rc2 != 0
    acc.reset_account_stores()
