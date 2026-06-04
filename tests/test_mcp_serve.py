"""平台 MCP 端点编排 (mcp_serve) —— 纯函数单测: 端点枚举 / 命令构造 / 探测。

spawn 副作用不在此测 (需真 mcp-proxy + codegraph); 只验"从 config 推导出哪些端点 +
命令长什么样 + 探测分支正确",这正是服务化路由逻辑的核心。
"""
from __future__ import annotations

from codev_platform import mcp_serve as ms
from codev_platform.mcp_serve import MCPEndpoint


def test_build_codegraph_cmd():
    # codegraph 已从 mcp-proxy 包 stdio 改为平台自写多租户 HTTP 代理 (codev_platform.codegraph.server)
    cmd = ms.build_codegraph_cmd("py", 18091)
    assert cmd == ["py", "-m", "codev_platform.codegraph.server", "--http", "--port", "18091"]


def test_build_cross_link_cmd():
    cmd = ms.build_cross_link_cmd("py", 18086)
    assert cmd == ["py", "-m", "codev_platform.cross_link.server", "--http", "--port", "18086"]


def test_build_agent_memory_cmd():
    cmd = ms.build_agent_memory_cmd("py", 18087)
    assert cmd == ["py", "-m", "codev_platform.agent.memory_mcp", "--http", "--port", "18087"]


def test_iter_endpoints_includes_agent_memory(tmp_path):
    # 默认端口 + config 覆盖
    eps = ms.iter_endpoints({"daemon": {"port": 18083}, "projects": {}})
    mem = [e for e in eps if e.kind == "agent_memory"]
    assert len(mem) == 1 and mem[0].name == "agent-memory"
    assert mem[0].port == ms.DEFAULT_AGENT_MEMORY_PORT
    assert "codev_platform.agent.memory_mcp" in mem[0].cmd
    eps2 = ms.iter_endpoints({"mcp": {"agent_memory_sse_port": 19087}, "projects": {}})
    assert [e for e in eps2 if e.kind == "agent_memory"][0].port == 19087


def test_agent_memory_in_source_url():
    cfg = {"projects": {}}
    url = ms.mcp_source_url(cfg, "platform", "agent-memory", "openclaw-stock")
    assert url == "http://127.0.0.1:19087/sse?project_id=openclaw-stock"


def test_agent_memory_db_present_needs_dsn():
    ep = MCPEndpoint(name="agent-memory", kind="agent_memory", port=18087)
    assert ms._db_present(ep, {"memory": {"pg_dsn": "postgresql://x"}, "projects": {}}) is True
    assert ms._db_present(ep, {"projects": {}}) is False


def test_agent_memory_systemd_unit_generated():
    from codev_platform import mcp_systemd
    units = mcp_systemd.render_systemd_units({"projects": {}}, "deployer")
    assert "codev-mcp-agent-memory.service" in units
    assert "codev_platform.agent.memory_mcp" in units["codev-mcp-agent-memory.service"]


def test_memory_maintenance_systemd_units():
    # B2: M4 cron — 每日 TTL 归档 + 向量 GC 的 oneshot service + timer
    from codev_platform import mcp_systemd
    u = mcp_systemd.render_memory_maintenance_units({"projects": {}}, "deployer")
    assert "codev-memory-maintenance.service" in u and "codev-memory-maintenance.timer" in u
    svc = u["codev-memory-maintenance.service"]
    assert "run_memory_maintenance.py" in svc and "Type=oneshot" in svc
    assert "OnCalendar" in u["codev-memory-maintenance.timer"]


def test_endpoint_health_url_for_all_kinds():
    chroma = MCPEndpoint(name="platform-docs", kind="chroma", port=18083)
    cl = MCPEndpoint(name="cross-link", kind="cross_link", port=18086)
    cg = MCPEndpoint(name="codegraph", kind="codegraph", port=18091)
    # 审计 #4: 探活改打 PUBLIC /healthz (最小, 不泄敏); 详情面 /platform/status 改鉴权
    assert chroma.health_url.endswith(":18083/healthz")
    assert cl.health_url.endswith(":18086/healthz")
    # codegraph 改平台自写多租户代理后也自带 /healthz (不再 mcp-proxy 无 health → TCP)
    assert cg.health_url.endswith(":18091/healthz")
    assert cg.sse_url == "http://127.0.0.1:18091/sse"


def test_iter_endpoints_always_has_chroma_and_cross_link(tmp_path):
    cfg = {"daemon": {"port": 18083}, "mcp": {"cross_link_sse_port": 18086}, "projects": {}}
    eps = ms.iter_endpoints(cfg)
    kinds = {e.kind: e for e in eps}
    # chroma 现在也可由 serve-mcp start 拉起作常驻 (cutover 后失去 launcher auto-spawn);
    # self_spawned 仅表示它也能被业务仓 Claude 会话经 launcher 拉起
    assert kinds["chroma"].self_spawned is True and kinds["chroma"].cmd is not None
    assert "codev_platform.chroma.server" in kinds["chroma"].cmd
    assert kinds["cross_link"].port == 18086 and kinds["cross_link"].cmd is not None


def test_iter_endpoints_codegraph_single_multitenant(tmp_path):
    # codegraph 已从 per-project 多端点改为单端点多租户代理 (按 ?project_id= 路由 per-repo 后端)
    cfg = {
        "daemon": {"port": 18083},
        "mcp": {"codegraph_sse_port": 18095},
        "projects": {
            "proj-a": {"repo_path": str(tmp_path / "repo_a")},
            "proj-b": {"repo_path": str(tmp_path / "repo_b")},
        },
    }
    eps = ms.iter_endpoints(cfg)
    cg = [e for e in eps if e.kind == "codegraph"]
    # 不管几个项目, 只产出一个 codegraph 端点
    assert len(cg) == 1
    assert cg[0].project_id is None          # 多租户单端点, 业务仓走 ?project_id=
    assert cg[0].port == 18095               # config mcp.codegraph_sse_port 覆盖默认
    assert cg[0].cwd is None                 # repo_path 由代理内部从 config.projects 解析
    assert cg[0].name == "codegraph"
    assert "codev_platform.codegraph.server" in cg[0].cmd


def test_iter_endpoints_codegraph_default_port(tmp_path):
    # 未配 mcp.codegraph_sse_port → 落 DEFAULT_CODEGRAPH_PORT
    cfg = {"daemon": {"port": 18083}, "projects": {}}
    cg = [e for e in ms.iter_endpoints(cfg) if e.kind == "codegraph"]
    assert len(cg) == 1
    assert cg[0].port == ms.DEFAULT_CODEGRAPH_PORT


def test_probe_http_kind_uses_health(monkeypatch):
    monkeypatch.setattr(ms, "_http_health", lambda url, timeout=2.0: True)
    monkeypatch.setattr(ms, "_tcp_open", lambda h, p, timeout=2.0: False)  # 不应被调用
    ep = MCPEndpoint(name="cross-link", kind="cross_link", port=18086)
    assert ms.probe(ep) == "ok"


def test_probe_codegraph_uses_health(monkeypatch):
    # codegraph 改平台自写代理后自带 /health, probe 走 _http_health (不再 TCP)
    monkeypatch.setattr(ms, "_http_health", lambda url, timeout=2.0: True)
    monkeypatch.setattr(ms, "_tcp_open", lambda h, p, timeout=2.0: False)  # 不应被调用
    ep = MCPEndpoint(name="codegraph", kind="codegraph", port=18091)
    assert ms.probe(ep) == "ok"


def test_probe_all_shape(monkeypatch):
    monkeypatch.setattr(ms, "_http_health", lambda url, timeout=2.0: False)
    monkeypatch.setattr(ms, "_tcp_open", lambda h, p, timeout=2.0: False)
    rows = ms.probe_all({"daemon": {"port": 18083}, "projects": {}})
    assert all({"name", "kind", "port", "status", "sse_url", "self_spawned"} <= set(r) for r in rows)
    assert any(r["kind"] == "cross_link" for r in rows)
