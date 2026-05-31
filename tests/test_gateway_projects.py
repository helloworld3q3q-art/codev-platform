"""gateway token-add --projects 白名单解析 + token-list 展示单测。

cmd_gateway 内部 `from codev_platform.core.config import load_config, save_config`,
用 CODEV_PLATFORM_CONFIG 指向 tmp config 隔离, 不污染真 config。
"""
from __future__ import annotations

import argparse
import json

import pytest

from codev_platform.ops.gateway import cmd_gateway


@pytest.fixture()
def tmp_cfg(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"gateway": {"auth_mode": "token", "tokens": {}}}) + "\n", encoding="utf-8")
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(p))
    return p


def _add(arg="alice", org="acme", projects=None):
    return argparse.Namespace(action="token-add", arg=arg, org=org, projects=projects,
                              repo=None, env="PLATFORM_TOKEN", remove=False)


def _tokens(cfg_path):
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    return data["gateway"]["tokens"]


def test_projects_star_writes_star(tmp_cfg):
    assert cmd_gateway(_add(projects="*")) == 0
    meta = next(iter(_tokens(tmp_cfg).values()))
    assert meta["projects"] == "*"


def test_projects_csv_writes_list(tmp_cfg):
    assert cmd_gateway(_add(projects="pid1, pid2 ,pid3")) == 0
    meta = next(iter(_tokens(tmp_cfg).values()))
    assert meta["projects"] == ["pid1", "pid2", "pid3"]


def test_projects_absent_not_written_and_warns(tmp_cfg, capsys):
    assert cmd_gateway(_add(projects=None)) == 0
    meta = next(iter(_tokens(tmp_cfg).values()))
    assert "projects" not in meta  # 缺省 = 不写入 (安全默认: 无权)
    out = capsys.readouterr().out
    assert "WARN" in out and "无任何项目访问权" in out


def test_projects_empty_string_not_written(tmp_cfg):
    assert cmd_gateway(_add(projects="   ")) == 0
    meta = next(iter(_tokens(tmp_cfg).values()))
    assert "projects" not in meta


def test_token_list_shows_projects(tmp_cfg, capsys):
    cmd_gateway(_add(arg="bob", projects="pid1,pid2"))
    cmd_gateway(_add(arg="carol", projects=None))
    capsys.readouterr()  # 清掉 add 的输出
    rc = cmd_gateway(argparse.Namespace(action="token-list", arg=None, org="default",
                                        projects=None, repo=None, env="PLATFORM_TOKEN", remove=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "pid1" in out
    assert "(无项目权)" in out
