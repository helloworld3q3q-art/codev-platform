"""gateway client-auth --query-token —— 把 token 明文内联进 .mcp.json 各 sse url 的 ?token=。

给无法设 Authorization header 的 MCP 客户端(url 一定能填); 服务端 auth._query_token 接它。
"""
from __future__ import annotations

import argparse
import json

from codev_platform.ops.gateway import cmd_gateway


def _mcp_json(tmp_path):
    p = tmp_path / ".mcp.json"
    data = {
        "mcpServers": {
            "graph": {"type": "sse", "url": "http://127.0.0.1:19092/sse?project_id=demo"},
            "codegraph": {"type": "sse", "url": "http://127.0.0.1:19091/sse"},
            "local-stdio": {"type": "stdio", "command": "foo"},
        }
    }
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _args(repo, *, remove=False, token="tok-xyz"):
    return argparse.Namespace(action="client-auth", arg=None, org="default", projects=None,
                              repo=str(repo), env="PLATFORM_TOKEN", remove=remove, base=None,
                              query_token=True, token=token)


def _servers(repo):
    return json.loads((repo / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]


def test_inlines_token_into_sse_url(tmp_path):
    _mcp_json(tmp_path)
    assert cmd_gateway(_args(tmp_path)) == 0
    s = _servers(tmp_path)
    # 保留原 query(project_id)+ 追加 token
    assert "project_id=demo" in s["graph"]["url"] and "token=tok-xyz" in s["graph"]["url"]
    assert "token=tok-xyz" in s["codegraph"]["url"]
    # 非 sse 不动
    assert s["local-stdio"]["command"] == "foo" and "url" not in s["local-stdio"]


def test_remove_strips_token(tmp_path):
    _mcp_json(tmp_path)
    cmd_gateway(_args(tmp_path))
    cmd_gateway(_args(tmp_path, remove=True))
    s = _servers(tmp_path)
    assert "token=" not in s["graph"]["url"]
    assert "project_id=demo" in s["graph"]["url"]   # 原 query 保留


def test_reapply_does_not_duplicate_token(tmp_path):
    _mcp_json(tmp_path)
    cmd_gateway(_args(tmp_path, token="t1"))
    cmd_gateway(_args(tmp_path, token="t2"))   # 重应用换新 token, 不叠加
    url = _servers(tmp_path)["graph"]["url"]
    assert url.count("token=") == 1 and "token=t2" in url


def test_missing_token_value_returns_nonzero(tmp_path, monkeypatch):
    _mcp_json(tmp_path)
    monkeypatch.delenv("PLATFORM_TOKEN", raising=False)
    assert cmd_gateway(_args(tmp_path, token=None)) != 0   # 无 --token 无 env → 拒
