"""B9: 断言每个 systemd unit 都含 Restart=always —— 防未来有人移除自愈策略。

systemd unit 是开机自起 + 崩溃重启的常驻保障; 任一 unit 丢了 Restart=always 就会
退化成"挂了不拉起"。这里渲染全部 unit (MCP 端点 + reindex + webhook), 逐个断言。
"""
from __future__ import annotations

from codev_platform import mcp_serve as ms
from codev_platform import mcp_systemd as sysd


def _cfg(tmp_path):
    # projects 非空 → codegraph 端点也产出; chroma / graph 恒在
    return {
        "daemon": {"port": 18083},
        "mcp": {"graph_sse_port": 18092, "codegraph_sse_port": 18095},
        "projects": {"proj-a": {"repo_path": str(tmp_path / "repo_a")}},
    }


def test_all_mcp_units_have_restart_always(tmp_path):
    units = ms.render_systemd_units(_cfg(tmp_path), user="tester")
    assert units, "render_systemd_units 应至少产出 chroma + codegraph + graph"
    for name, content in units.items():
        assert "Restart=always" in content, f"{name} 缺 Restart=always (自愈被移除?)"
        assert "RestartSec=3" in content, f"{name} 缺 RestartSec"


def test_reindex_unit_has_restart_always(tmp_path):
    _name, content = ms.render_reindex_unit(_cfg(tmp_path), user="tester")
    assert "Restart=always" in content


def test_reindex_unit_uses_configured_environment_file(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["systemd"] = {"env_file": "/etc/codev-platform/platform.env"}

    _name, content = ms.render_reindex_unit(cfg, user="tester")

    assert "EnvironmentFile=-/etc/codev-platform/platform.env" in content
    assert "Environment=PATH=/usr/local/bin:/usr/bin:/bin:" in content
    assert content.index("EnvironmentFile=-") < content.index("Environment=PATH=")
    assert content.index("Environment=PATH=") < content.index("ExecStart=")


def test_reindex_unit_omits_environment_file_when_unconfigured(tmp_path):
    _name, content = ms.render_reindex_unit(_cfg(tmp_path), user="tester")

    assert "EnvironmentFile=" not in content


def test_all_long_running_units_use_configured_environment_file(tmp_path):
    cfg = _cfg(tmp_path)
    cfg["systemd"] = {"env_file": "/etc/codev-platform/platform.env"}

    units = ms.render_systemd_units(cfg, user="tester")
    for name, content in (
        ms.render_reindex_unit(cfg, user="tester"),
        ms.render_webhook_unit(cfg, user="tester"),
        ms.render_agent_unit(cfg, user="tester"),
    ):
        units[name] = content
    units.update(sysd.render_memory_maintenance_units(cfg, user="tester"))

    service_units = {name: content for name, content in units.items() if name.endswith(".service")}
    assert service_units
    for name, content in service_units.items():
        assert "EnvironmentFile=-/etc/codev-platform/platform.env" in content, name
        assert "Environment=PATH=/usr/local/bin:/usr/bin:/bin:" in content, name


def test_webhook_unit_has_restart_always(tmp_path):
    _name, content = ms.render_webhook_unit(_cfg(tmp_path), user="tester")
    assert "Restart=always" in content


def test_codegraph_unit_present_and_self_healing(tmp_path):
    # codegraph 是单端点多租户代理 —— 确认它出现在 systemd 渲染里且自愈
    units = ms.render_systemd_units(_cfg(tmp_path), user="tester")
    cg = [c for n, c in units.items() if "codegraph" in n]
    assert cg, "codegraph unit 应被渲染"
    assert "Restart=always" in cg[0]
