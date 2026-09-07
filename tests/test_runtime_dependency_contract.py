"""发布运行时依赖的包元数据契约。"""
from __future__ import annotations

import re
from pathlib import Path


_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _extra_requirements(name: str) -> tuple[str, ...]:
    """读取简单数组 extra，保持 Python 3.10 可运行而不引入 TOML 解析依赖。"""
    content = _PYPROJECT.read_text(encoding="utf-8")
    matched = re.search(
        rf"^{re.escape(name)}\s*=\s*\[(?P<body>.*?)^\]",
        content,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert matched is not None, f"缺少 [{name}] extra"
    return tuple(re.findall(r'^\s*"([^"]+)"', matched.group("body"), flags=re.MULTILINE))


def test_runtime_extra直接声明mcp_sdk():
    """三类 MCP 服务直接导入 SDK，不能依赖 mcp-proxy 的传递依赖。"""
    requirements = _extra_requirements("runtime")
    assert any(requirement.startswith("mcp>=") for requirement in requirements)


def test_mcp_sdk依赖锁定在兼容的v1主版本():
    """现有服务使用 v1 API，新建 release 不能被未来的 v2 自动破坏。"""
    for extra in ("runtime", "agent"):
        requirements = _extra_requirements(extra)
        assert "mcp>=1.0.0,<2" in requirements
