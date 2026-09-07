"""Remote MCP source client tests."""

from __future__ import annotations

import pytest

from codev_platform import mcp_serve
from codev_platform import mcp_source_client as source_client
from codev_platform.mcp_endpoint_catalog import mcp_source_kind
from codev_platform.mcp_source_client import MCPSourceEndpoint


def test_platform_source_endpoints_use_19xxx_without_local_spawn_commands():
    endpoints = source_client.iter_source_endpoints({"projects": {}}, "platform")

    assert {endpoint.name: endpoint.port for endpoint in endpoints} == {
        "platform-docs": 19083,
        "codegraph": 19091,
        "agent-memory": 19087,
        "graph": 19092,
    }
    assert all(not hasattr(endpoint, "cmd") for endpoint in endpoints)


def test_source_kind_mapping_fails_closed_for_unknown_tool():
    with pytest.raises(ValueError, match="未知 MCP source tool"):
        mcp_source_kind("unknown")


def test_remote_source_probe_never_accepts_unrelated_tcp_listener(monkeypatch):
    endpoint = MCPSourceEndpoint(
        name="platform-docs",
        kind="chroma",
        host="127.0.0.1",
        port=19083,
    )
    monkeypatch.setattr(source_client, "_http_health", lambda _url, *, timeout: False)
    monkeypatch.setattr(mcp_serve, "_http_health", lambda _url: False)
    monkeypatch.setattr(mcp_serve, "_tcp_open", lambda _host, _port: True)

    assert mcp_serve.probe(endpoint) == "ok"
    assert source_client.probe_source(endpoint) == "down"


def test_source_probe_can_assume_current_service_without_self_http_call(monkeypatch):
    endpoints = [
        MCPSourceEndpoint("platform-docs", "chroma", "127.0.0.1", 18083),
        MCPSourceEndpoint("codegraph", "codegraph", "127.0.0.1", 18091),
    ]
    probed: list[str] = []
    monkeypatch.setattr(source_client, "iter_source_endpoints", lambda _cfg, _target: endpoints)
    monkeypatch.setattr(
        source_client, "probe_source",
        lambda endpoint: probed.append(endpoint.kind) or "down",
    )

    rows = source_client.probe_source_all(
        {}, "local", assumed_healthy_kinds=frozenset({"chroma"}),
    )

    assert probed == ["codegraph"]
    assert {row["kind"]: row["status"] for row in rows} == {
        "chroma": "ok", "codegraph": "down",
    }


def test_malformed_http_listener_is_reported_down(monkeypatch):
    import http.client

    endpoint = MCPSourceEndpoint(
        name="platform-docs",
        kind="chroma",
        host="127.0.0.1",
        port=19083,
    )

    def malformed_listener(_url, *, timeout):
        assert timeout > 0
        raise http.client.BadStatusLine("garbage")

    monkeypatch.setattr(source_client.urllib.request, "urlopen", malformed_listener)

    assert source_client.probe_source(endpoint) == "down"


def test_ensure_source_serving_starts_only_down_managed_unit(monkeypatch):
    endpoints = [
        MCPSourceEndpoint("platform-docs", "chroma", "127.0.0.1", 19083),
        MCPSourceEndpoint("graph", "graph", "127.0.0.1", 19092),
    ]
    started: list[str] = []
    monkeypatch.setattr(source_client, "iter_source_endpoints", lambda _cfg, _target: endpoints)
    monkeypatch.setattr(
        source_client,
        "probe_source",
        lambda endpoint, **_kwargs: "ok" if endpoint.kind == "chroma" else "down",
    )

    rows = source_client.ensure_source_serving(
        {},
        "platform",
        start_unit=lambda unit: started.append(unit) or {"action": "started"},
    )

    assert started == ["codev-mcp-graph.service"]
    assert rows == [
        {"name": "platform-docs", "action": "already-up", "status": "ok"},
        {"name": "graph", "action": "started", "status": "starting"},
    ]


def test_source_wait_requires_all_endpoints_healthy_in_same_round(monkeypatch):
    endpoints = [
        MCPSourceEndpoint("platform-docs", "chroma", "127.0.0.1", 19083),
        MCPSourceEndpoint("graph", "graph", "127.0.0.1", 19092),
    ]
    now = [0.0]
    rounds = iter([
        {"platform-docs": "ok", "graph": "down"},
        {"platform-docs": "down", "graph": "ok"},
    ])
    current: dict[str, str] = {}
    round_number = 0

    def probe(endpoint, *, timeout):
        nonlocal current, round_number
        assert timeout > 0
        if endpoint.name == "platform-docs":
            current = next(rounds)
            round_number += 1
        if endpoint.name == "graph" and round_number == 2:
            now[0] = 1.0
        return current[endpoint.name]

    monkeypatch.setattr(source_client, "iter_source_endpoints", lambda _cfg, _target: endpoints)
    monkeypatch.setattr(source_client, "probe_source", probe)
    monkeypatch.setattr(source_client.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(source_client.time, "sleep", lambda _seconds: None)

    rows = source_client.wait_until_source_serving({}, "platform", timeout=1.0)

    assert {row["name"]: row["status"] for row in rows} == {
        "platform-docs": "timeout",
        "graph": "ok",
    }


def test_source_wait_partial_final_round_cannot_merge_previous_success(monkeypatch):
    endpoints = [
        MCPSourceEndpoint("platform-docs", "chroma", "127.0.0.1", 19083),
        MCPSourceEndpoint("graph", "graph", "127.0.0.1", 19092),
    ]
    now = [0.0]
    results = iter(("down", "ok", "ok"))

    def probe(_endpoint, *, timeout):
        assert timeout > 0
        result = next(results)
        if result == "ok" and now[0] > 0:
            now[0] = 1.0
        return result

    monkeypatch.setattr(source_client, "iter_source_endpoints", lambda _cfg, _target: endpoints)
    monkeypatch.setattr(source_client, "probe_source", probe)
    monkeypatch.setattr(source_client.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(source_client.time, "sleep", lambda _seconds: now.__setitem__(0, 0.5))

    rows = source_client.wait_until_source_serving({}, "platform", timeout=1.0)

    assert {row["name"]: row["status"] for row in rows} == {
        "platform-docs": "ok",
        "graph": "timeout",
    }


def test_source_wait_never_allocates_more_than_remaining_timeout(monkeypatch):
    endpoints = [
        MCPSourceEndpoint("platform-docs", "chroma", "127.0.0.1", 19083),
        MCPSourceEndpoint("graph", "graph", "127.0.0.1", 19092),
    ]
    now = [0.0]
    budgets: list[float] = []

    def consume_budget(_endpoint, *, timeout):
        budgets.append(timeout)
        now[0] += min(0.06, timeout)
        return "down"

    monkeypatch.setattr(source_client, "iter_source_endpoints", lambda _cfg, _target: endpoints)
    monkeypatch.setattr(source_client, "probe_source", consume_budget)
    monkeypatch.setattr(source_client.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(source_client.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    rows = source_client.wait_until_source_serving({}, "platform", timeout=0.1)

    assert budgets == pytest.approx([0.1, 0.04])
    assert now[0] == pytest.approx(0.1)
    assert all(row["status"] == "timeout" for row in rows)


def test_source_wait_caps_each_endpoint_budget_for_fairness(monkeypatch):
    endpoints = [
        MCPSourceEndpoint("platform-docs", "chroma", "127.0.0.1", 19083),
        MCPSourceEndpoint("graph", "graph", "127.0.0.1", 19092),
    ]
    now = [0.0]
    budgets: list[float] = []

    def slow_probes(_endpoint, *, timeout):
        budgets.append(timeout)
        if len(budgets) == 1:
            now[0] += timeout
        else:
            now[0] = 60.0
        return "down"

    monkeypatch.setattr(source_client, "iter_source_endpoints", lambda _cfg, _target: endpoints)
    monkeypatch.setattr(source_client, "probe_source", slow_probes)
    monkeypatch.setattr(source_client.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(source_client.time, "sleep", lambda _seconds: None)

    source_client.wait_until_source_serving({}, "platform", timeout=60.0)

    assert budgets == [2.0, 2.0]
