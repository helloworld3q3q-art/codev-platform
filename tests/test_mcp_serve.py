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


def test_build_graph_cmd():
    cmd = ms.build_graph_cmd("py", 18092)
    assert cmd == ["py", "-m", "codev_platform.graph.mcp_server", "--http", "--port", "18092"]


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
    gr = MCPEndpoint(name="graph", kind="graph", port=18092)
    cg = MCPEndpoint(name="codegraph", kind="codegraph", port=18091)
    # 审计 #4: 探活改打 PUBLIC /healthz (最小, 不泄敏); 详情面 /platform/status 改鉴权
    assert chroma.health_url.endswith(":18083/healthz")
    assert gr.health_url.endswith(":18092/healthz")
    # codegraph 改平台自写多租户代理后也自带 /healthz (不再 mcp-proxy 无 health → TCP)
    assert cg.health_url.endswith(":18091/healthz")
    assert cg.sse_url == "http://127.0.0.1:18091/sse"


def test_iter_endpoints_always_has_chroma_and_graph(tmp_path):
    cfg = {"daemon": {"port": 18083}, "mcp": {"graph_sse_port": 18092}, "projects": {}}
    eps = ms.iter_endpoints(cfg)
    kinds = {e.kind: e for e in eps}
    # chroma 现在也可由 serve-mcp start 拉起作常驻 (cutover 后失去 launcher auto-spawn);
    # self_spawned 仅表示它也能被业务仓 Claude 会话经 launcher 拉起
    assert kinds["chroma"].self_spawned is True and kinds["chroma"].cmd is not None
    assert "codev_platform.chroma.server" in kinds["chroma"].cmd
    assert kinds["graph"].port == 18092 and kinds["graph"].cmd is not None


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
    ep = MCPEndpoint(name="graph", kind="graph", port=18092)
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
    assert any(r["kind"] == "graph" for r in rows)


def test_serve_mcp_reindex_worker_status_line(monkeypatch):
    from codev_platform.cli_cmds import mcp
    monkeypatch.setattr(
        "codev_platform.reindex.status.summarize",
        lambda: {
            "severity": "WARN",
            "running": False,
            "worker": {"running": False, "pid": 123, "exit_reason": "idle"},
            "queue_backend": "FileSpoolQueue",
            "pending_count": 2,
            "oldest_pending_age_sec": 600,
            "stale_count": 2,
        },
    )

    mark, line = mcp._reindex_worker_status_line()

    assert mark == "WARN"
    assert "pending=2" in line and "stale=2" in line


# ---- P0: _bind_port 端口解析收敛(canonical > deprecated 别名 > 默认)----

def test_bind_port_canonical_key_wins():
    cfg = {"mcp": {"platform_docs_sse_port": 29083, "codegraph_sse_port": 29091,
                   "agent_memory_sse_port": 29087, "graph_sse_port": 29092}}
    assert ms._bind_port(cfg, "chroma") == 29083
    assert ms._bind_port(cfg, "codegraph") == 29091
    assert ms._bind_port(cfg, "agent_memory") == 29087
    assert ms._bind_port(cfg, "graph") == 29092


def test_bind_port_defaults_when_unset():
    assert ms._bind_port({}, "chroma") == ms.DEFAULT_CHROMA_PORT
    assert ms._bind_port({}, "codegraph") == ms.DEFAULT_CODEGRAPH_PORT
    assert ms._bind_port({}, "agent_memory") == ms.DEFAULT_AGENT_MEMORY_PORT
    assert ms._bind_port({}, "graph") == ms.DEFAULT_GRAPH_PORT


def test_bind_port_chroma_daemon_alias_works_and_warns_once(monkeypatch, caplog):
    # chroma 历史键 daemon.port 仍可读(别名),命中时一次性 warn
    monkeypatch.setattr(ms, "_warned_deprecated", set())  # 隔离一次性状态
    import logging
    with caplog.at_level(logging.WARNING):
        assert ms._bind_port({"daemon": {"port": 19083}}, "chroma") == 19083
        ms._bind_port({"daemon": {"port": 19083}}, "chroma")  # 第二次不再 warn
    warns = [r for r in caplog.records if "daemon.port" in r.getMessage()]
    assert len(warns) == 1 and "platform_docs_sse_port" in warns[0].getMessage()


def test_bind_port_canonical_beats_alias():
    # 同时配 canonical 与 daemon.port 别名 → canonical 胜,不 warn
    cfg = {"mcp": {"platform_docs_sse_port": 28083}, "daemon": {"port": 19083}}
    assert ms._bind_port(cfg, "chroma") == 28083


def test_iter_endpoints_uses_canonical_chroma_port():
    eps = ms.iter_endpoints({"mcp": {"platform_docs_sse_port": 27083}, "projects": {}})
    chroma = next(e for e in eps if e.kind == "chroma")
    assert chroma.port == 27083


# ---- P1: local 端口派生自 bind 口 + 一致性 WARN ----

def test_local_source_port_derives_from_bind_default():
    # 未配 mcp_sources.local → local 端口 == 该服务默认 bind 口(派生)
    for tool, default in [("platform-docs", ms.DEFAULT_CHROMA_PORT),
                          ("codegraph", ms.DEFAULT_CODEGRAPH_PORT),
                          ("agent-memory", ms.DEFAULT_AGENT_MEMORY_PORT),
                          ("graph", ms.DEFAULT_GRAPH_PORT)]:
        host, port = ms.mcp_source_endpoint({}, "local", tool)
        assert (host, port) == ("127.0.0.1", default)


def test_local_source_port_follows_bind_override():
    # 改 bind 口(canonical 键)→ local 派生自动跟随, 不用在 mcp_sources 再配一遍
    cfg = {"mcp": {"platform_docs_sse_port": 27083, "graph_sse_port": 27092}}
    assert ms.mcp_source_endpoint(cfg, "local", "platform-docs")[1] == 27083
    assert ms.mcp_source_endpoint(cfg, "local", "graph")[1] == 27092


def test_local_source_port_follows_daemon_alias():
    # chroma 走 daemon.port 别名时 local 也跟随派生
    assert ms.mcp_source_endpoint({"daemon": {"port": 19083}}, "local", "platform-docs")[1] == 19083


def test_local_source_explicit_override_wins():
    cfg = {"mcp_sources": {"local": {"graph": 31092}}, "mcp": {"graph_sse_port": 27092}}
    assert ms.mcp_source_endpoint(cfg, "local", "graph")[1] == 31092


def test_platform_source_ports_unchanged():
    assert ms.mcp_source_endpoint({}, "platform", "platform-docs") == ("127.0.0.1", 19083)
    assert ms.mcp_source_endpoint({}, "platform", "graph") == ("127.0.0.1", 19092)


def test_check_port_consistency_warns_on_mismatch():
    cfg = {"mcp_sources": {"local": {"graph": 31092}}, "mcp": {"graph_sse_port": 27092}}
    warns = ms.check_port_consistency(cfg)
    assert len(warns) == 1 and "graph" in warns[0] and "31092" in warns[0] and "27092" in warns[0]


def test_check_port_consistency_clean_when_derived():
    # 未显式配 local → 全派生 → 无 WARN
    assert ms.check_port_consistency({"mcp": {"graph_sse_port": 27092}}) == []


def test_check_port_consistency_clean_when_explicit_matches_bind():
    cfg = {"mcp_sources": {"local": {"graph": 27092}}, "mcp": {"graph_sse_port": 27092}}
    assert ms.check_port_consistency(cfg) == []


def test_build_mcp_servers_derives_all_tools():
    # 生成 .mcp.json 的 mcpServers 块: 4 套 sse, url 经 mcp_source_url 派生(不漂移)
    servers = ms.build_mcp_servers({}, "platform", "myproj")
    assert set(servers) == set(ms.MCP_SOURCE_TOOLS)
    for spec in servers.values():
        assert spec["type"] == "sse"
        assert spec["url"].startswith("http://") and "project_id=myproj" in spec["url"]
    assert "19083" in servers["platform-docs"]["url"]   # platform 源默认端口, 与 endpoint 一致
