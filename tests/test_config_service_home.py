"""显式服务账号 HOME 配置上下文测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codev_platform.core import config


def test_显式_home_只改变动态默认值和默认配置位置(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_home = tmp_path / "service-user"
    monkeypatch.delenv("CODEV_PLATFORM_CONFIG", raising=False)

    loaded = config.load_config(home=service_home)

    assert config.config_path(home=service_home) == service_home / ".codev-platform" / "config.json"
    assert loaded["models"]["embed_path"] == str(service_home / "models" / "Qwen3-Embedding-0.6B")
    assert loaded["models"]["reranker_path"] == str(service_home / "models" / "Qwen3-Reranker-0.6B")


def test_显式_home_仍优先使用受管配置文件覆盖(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_home = tmp_path / "service-user"
    config_file = tmp_path / "managed-config.json"
    config_file.write_text(
        json.dumps({"models": {"embed_device": "cpu"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(config_file))

    loaded = config.load_config(home=service_home)

    assert config.config_path(home=service_home) == config_file
    assert loaded["models"]["embed_device"] == "cpu"
    assert loaded["models"]["embed_path"] == str(service_home / "models" / "Qwen3-Embedding-0.6B")


def test_显式_home_拒绝相对路径() -> None:
    with pytest.raises(ValueError, match="绝对路径"):
        config.load_config(home=Path("relative-home"))
