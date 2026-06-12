"""`codev-platform onboard` 编排命令测试 —— 一条命令完成登记(--no-index 不触索引)。

验证编排器把 config / project.json / meta.json 三处登记一次写齐, 软步(RBAC / codegraph)
不可用时优雅跳过且不阻断(rc=0)。复用既有能力, 不重造的接缝在此钉死。
"""
from __future__ import annotations

import argparse
import json

from codev_platform.ops.onboard import cmd_onboard


def _args(code, repo, **kw):
    base = dict(code=code, repo=str(repo), org="default", owner="root", name=None, no_index=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_onboard_no_index_writes_all_registration(tmp_path, monkeypatch):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    meta_root = tmp_path / "platform_meta" / "projects"
    cfg_store: dict = {"projects": {}}
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg_store)
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)  # cfg_store 原地改即可
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", meta_root)

    rc = cmd_onboard(_args("myproj", repo))
    assert rc == 0

    # 1. config projects.<code>
    assert cfg_store["projects"]["myproj"]["repo_path"] == str(repo.resolve())
    assert cfg_store["projects"]["myproj"]["org_id"] == "default"
    # 2. <repo>/.claude/project.json
    pj = json.loads((repo / ".claude" / "project.json").read_text(encoding="utf-8"))
    assert pj["project_id"] == "myproj" and pj["display_name"] == "myproj"
    # 3. meta.json
    meta = json.loads((meta_root / "myproj" / "meta.json").read_text(encoding="utf-8"))
    assert meta["project_id"] == "myproj"
    assert meta["repo_path"] == str(repo.resolve())


def test_onboard_rejects_missing_repo(tmp_path, monkeypatch):
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {"projects": {}})
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    rc = cmd_onboard(_args("p", tmp_path / "nope"))
    assert rc == 1  # repo 不存在 → 关键步失败即停


def test_onboard_rejects_bad_project_id(tmp_path):
    rc = cmd_onboard(_args("Bad ID!", tmp_path))
    assert rc == 1  # 非法 project_id


def test_onboard_custom_name_and_org(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    meta_root = tmp_path / "meta"
    cfg_store: dict = {"projects": {}}
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg_store)
    monkeypatch.setattr("codev_platform.core.config.save_config", lambda c: None)
    monkeypatch.setattr("codev_platform.cli.PLATFORM_META_PROJECTS", meta_root)

    rc = cmd_onboard(_args("proj2", repo, org="acme", name="My Project"))
    assert rc == 0
    assert cfg_store["projects"]["proj2"]["org_id"] == "acme"
    meta = json.loads((meta_root / "proj2" / "meta.json").read_text(encoding="utf-8"))
    assert meta["display_name"] == "My Project"
