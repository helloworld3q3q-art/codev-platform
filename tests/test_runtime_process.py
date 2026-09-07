"""运行时构建子进程最小环境测试。"""

from __future__ import annotations

import pytest

from codev_platform.runtime_process import isolated_process_environment


def test_isolated_environment_drops_business_credentials(monkeypatch) -> None:
    monkeypatch.setenv("CODEV_PLATFORM_MEMORY_DSN", "不得继承")
    monkeypatch.setenv("OPENAI_API_KEY", "不得继承")
    monkeypatch.setenv("PYTHONPATH", "不得继承")

    environment = isolated_process_environment()

    assert "CODEV_PLATFORM_MEMORY_DSN" not in environment
    assert "OPENAI_API_KEY" not in environment
    assert environment["PYTHONPATH"] == ""
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_network_environment_only_adds_transport_configuration(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:18080")

    assert "HTTPS_PROXY" not in isolated_process_environment()
    assert isolated_process_environment(network=True)["HTTPS_PROXY"] == ("http://127.0.0.1:18080")


def test_environment_override_requires_string_mapping() -> None:
    with pytest.raises(TypeError, match="字符串映射"):
        isolated_process_environment(overrides={"KEY": 1})  # type: ignore[dict-item]
