"""Chroma 配置叶子的导入期静默回归。"""

from __future__ import annotations

from codev_platform.chroma import _config
from codev_platform.core.project_id import ProjectIdError


def test_可选默认项目解析失败时静默返回none(monkeypatch, capsys) -> None:
    def unresolved_project() -> str:
        raise ProjectIdError("测试中没有默认项目")

    monkeypatch.setattr(_config, "resolve_local", unresolved_project)

    assert _config._resolve_optional_project_id() is None

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
