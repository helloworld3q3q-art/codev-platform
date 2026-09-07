"""本地项目登记表在源码与 wheel 环境中的解析契约。"""
from __future__ import annotations

import argparse
import json

import codev_platform.cli as cli
from codev_platform.core import repos
from codev_platform.core.platform_meta import platform_meta_projects_dir


def test_资源包不内置实际项目登记表() -> None:
    projects = platform_meta_projects_dir(environment={})

    assert projects.is_dir()
    assert not list(projects.glob("*/meta.json"))


def test_显式登记表目录覆盖内置资源(tmp_path, monkeypatch) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setenv("CODEV_PLATFORM_META", str(projects))

    assert platform_meta_projects_dir() == projects


def test_cli与仓解析共用显式登记表(tmp_path, monkeypatch, capsys) -> None:
    projects = tmp_path / "projects"
    target = projects / "demo" / "meta.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "display_name": "演示项目",
                "repo_path": "/srv/demo",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEV_PLATFORM_META", str(projects))
    monkeypatch.setattr(cli, "PLATFORM_META_PROJECTS", platform_meta_projects_dir())

    assert cli.cmd_list_projects(argparse.Namespace()) == 0
    assert "demo" in capsys.readouterr().out
    assert repos._read_meta("demo")["display_name"] == "演示项目"
