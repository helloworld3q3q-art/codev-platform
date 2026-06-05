"""gateway client-url —— 把 .mcp.json 各 sse server url 重写成远程反代地址。

按服务名做路径前缀: {base}/<server>/sse, 保留原 query (?project_id=...) + headers。
"""
from __future__ import annotations

import argparse
import json

import pytest

from codev_platform.ops.gateway import cmd_gateway


def _mcp_json(tmp_path):
    p = tmp_path / ".mcp.json"
    data = {
        "mcpServers": {
            "platform-docs": {
                "type": "sse",
                "url": "http://127.0.0.1:18083/sse?project_id=demo",
                "headers": {"Authorization": "Bearer ${PLATFORM_TOKEN}"},
            },
            "graph": {
                "type": "sse",
                "url": "http://127.0.0.1:18092/sse?project_id=demo",
            },
            "codegraph": {
                "type": "sse",
                "url": "http://127.0.0.1:19001/sse",
                "headers": {"Authorization": "Bearer ${PLATFORM_TOKEN}"},
            },
            "local-stdio": {
                "type": "stdio",
                "command": "foo",
            },
        }
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _args(repo, base):
    return argparse.Namespace(action="client-url", arg=None, org="default", projects=None,
                              repo=str(repo), env="PLATFORM_TOKEN", remove=False, base=base)


def _servers(repo):
    return json.loads((repo / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def test_rewrites_sse_url_by_server_name(tmp_path):
    _mcp_json(tmp_path)
    rc = cmd_gateway(_args(tmp_path, "https://platform.example.com"))
    assert rc == 0
    s = _servers(tmp_path)
    assert s["platform-docs"]["url"] == "https://platform.example.com/platform-docs/sse?project_id=demo"
    assert s["graph"]["url"] == "https://platform.example.com/graph/sse?project_id=demo"
    assert s["codegraph"]["url"] == "https://platform.example.com/codegraph/sse"


def test_preserves_headers_and_skips_stdio(tmp_path):
    _mcp_json(tmp_path)
    cmd_gateway(_args(tmp_path, "https://platform.example.com"))
    s = _servers(tmp_path)
    # headers (token) 保留
    assert s["platform-docs"]["headers"]["Authorization"] == "Bearer ${PLATFORM_TOKEN}"
    assert s["codegraph"]["headers"]["Authorization"] == "Bearer ${PLATFORM_TOKEN}"
    # 非 sse server 不动
    assert s["local-stdio"]["command"] == "foo"
    assert "url" not in s["local-stdio"]


def test_trailing_slash_base_normalized(tmp_path):
    _mcp_json(tmp_path)
    cmd_gateway(_args(tmp_path, "https://platform.example.com/"))
    s = _servers(tmp_path)
    assert s["graph"]["url"] == "https://platform.example.com/graph/sse?project_id=demo"


def test_invalid_base_returns_nonzero(tmp_path):
    _mcp_json(tmp_path)
    assert cmd_gateway(_args(tmp_path, "platform.example.com")) != 0  # 缺 scheme
    assert cmd_gateway(_args(tmp_path, "")) != 0
    assert cmd_gateway(_args(tmp_path, None)) != 0


def test_missing_mcp_json_returns_nonzero(tmp_path):
    assert cmd_gateway(_args(tmp_path / "nope", "https://platform.example.com")) != 0
