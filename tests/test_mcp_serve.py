"""平台 MCP 端点编排 (mcp_serve) —— 纯函数单测: 端点枚举 / 命令构造 / 探测。

spawn 副作用不在此测 (需真 mcp-proxy + codegraph); 只验"从 config 推导出哪些端点 +
命令长什么样 + 探测分支正确",这正是服务化路由逻辑的核心。
"""
from __future__ import annotations

from codev_platform import mcp_serve as ms
from codev_platform.mcp_serve import MCPEndpoint


def test_build_codegraph_proxy_cmd_has_separator_and_mcp():
    cmd = ms.build_codegraph_proxy_cmd("mcp-proxy", 18091)
    assert cmd[:5] == ["mcp-proxy", "--port", "18091", "--host", "127.0.0.1"]
    # `--` 分隔后才是 stdio 命令, 否则 --mcp 被 argparse 当 proxy 选项吃掉
    assert "--" in cmd
    sep = cmd.index("--")
    assert cmd[sep + 1:] == ["codegraph", "serve", "--mcp"]


def test_build_cross_link_cmd():
    cmd = ms.build_cross_link_cmd("py", 18086)
    assert cmd == ["py", "-m", "codev_platform.cross_link.server", "--http", "--port", "18086"]


def test_endpoint_health_url_only_for_http_kinds():
    chroma = MCPEndpoint(name="platform-docs", kind="chroma", port=18083)
    cl = MCPEndpoint(name="cross-link", kind="cross_link", port=18086)
    cg = MCPEndpoint(name="codegraph:x", kind="codegraph", port=18090)
    assert chroma.health_url.endswith(":18083/health")
    assert cl.health_url.endswith(":18086/health")
    assert cg.health_url is None  # mcp-proxy 无 /health → TCP 探
    assert cg.sse_url == "http://127.0.0.1:18090/sse"


def test_iter_endpoints_always_has_chroma_and_cross_link(tmp_path):
    cfg = {"daemon": {"port": 18083}, "mcp": {"cross_link_sse_port": 18086}, "projects": {}}
    eps = ms.iter_endpoints(cfg)
    kinds = {e.kind: e for e in eps}
    # chroma 现在也可由 serve-mcp start 拉起作常驻 (cutover 后失去 launcher auto-spawn);
    # self_spawned 仅表示它也能被业务仓 Claude 会话经 launcher 拉起
    assert kinds["chroma"].self_spawned is True and kinds["chroma"].cmd is not None
    assert "codev_platform.chroma.server" in kinds["chroma"].cmd
    assert kinds["cross_link"].port == 18086 and kinds["cross_link"].cmd is not None


def test_iter_endpoints_codegraph_per_project_with_existing_repo(tmp_path):
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    cfg = {
        "daemon": {"port": 18083},
        "projects": {
            "proj-a": {"repo_path": str(repo_a), "codegraph_sse_port": 18095},
            "proj-missing": {"repo_path": str(tmp_path / "nope")},  # 仓不存在 → 跳过
            "proj-norepo": {"codegraph_api_url": "http://x"},        # 无 repo_path → 跳过
        },
    }
    eps = ms.iter_endpoints(cfg)
    cg = [e for e in eps if e.kind == "codegraph"]
    assert len(cg) == 1
    assert cg[0].project_id == "proj-a"
    assert cg[0].port == 18095
    assert cg[0].cwd == str(repo_a)
    assert cg[0].name == "codegraph:proj-a"


def test_iter_endpoints_auto_assigns_port_when_unset(tmp_path):
    r1 = tmp_path / "r1"; r1.mkdir()
    r2 = tmp_path / "r2"; r2.mkdir()
    cfg = {"projects": {
        "p1": {"repo_path": str(r1)},   # 无显式端口 → auto base
        "p2": {"repo_path": str(r2)},   # 无显式端口 → auto base+1
    }}
    cg = [e for e in ms.iter_endpoints(cfg) if e.kind == "codegraph"]
    ports = sorted(e.port for e in cg)
    assert ports == [ms.DEFAULT_CODEGRAPH_BASE_PORT, ms.DEFAULT_CODEGRAPH_BASE_PORT + 1]


def test_probe_http_kind_uses_health(monkeypatch):
    monkeypatch.setattr(ms, "_http_health", lambda url, timeout=2.0: True)
    monkeypatch.setattr(ms, "_tcp_open", lambda h, p, timeout=2.0: False)  # 不应被调用
    ep = MCPEndpoint(name="cross-link", kind="cross_link", port=18086)
    assert ms.probe(ep) == "ok"


def test_probe_codegraph_uses_tcp(monkeypatch):
    monkeypatch.setattr(ms, "_http_health", lambda url, timeout=2.0: True)  # 不应被调用
    monkeypatch.setattr(ms, "_tcp_open", lambda h, p, timeout=2.0: False)
    ep = MCPEndpoint(name="codegraph:x", kind="codegraph", port=18090)
    assert ms.probe(ep) == "down"


def test_probe_all_shape(monkeypatch):
    monkeypatch.setattr(ms, "_http_health", lambda url, timeout=2.0: False)
    monkeypatch.setattr(ms, "_tcp_open", lambda h, p, timeout=2.0: False)
    rows = ms.probe_all({"daemon": {"port": 18083}, "projects": {}})
    assert all({"name", "kind", "port", "status", "sse_url", "self_spawned"} <= set(r) for r in rows)
    assert any(r["kind"] == "cross_link" for r in rows)
