"""E16 + B8: 断言 agent 常驻 unit 与时钟重同步 service/timer 的渲染与安装纳入。

- E16: render_agent_unit 含 Restart=always + `agent serve` ExecStart。
- B8: render_clock_resync_units 的 .timer 含 OnUnitActiveSec, .service 含 hwclock。
- install_systemd 输出含这些 unit 名 (agent + clock service/timer)。
"""
from __future__ import annotations

from codev_platform import mcp_serve as ms


def _cfg(tmp_path):
    return {
        "daemon": {"port": 18083},
        "mcp": {"cross_link_sse_port": 18086, "codegraph_sse_port": 18095},
        "projects": {"proj-a": {"repo_path": str(tmp_path / "repo_a")}},
    }


def test_agent_unit_self_healing_and_execstart():
    name, content = ms.render_agent_unit({}, user="tester")
    assert name == "codev-agent.service"
    assert "Restart=always" in content
    assert "RestartSec=3" in content
    assert "agent serve" in content
    assert "codev_platform.cli" in content


def test_agent_unit_respects_config_port():
    name, content = ms.render_agent_unit({"agent": {"port": 18090}}, user="tester")
    assert "--port 18090" in content


def test_clock_resync_service_has_hwclock():
    units = ms.render_clock_resync_units(user="tester")
    svc = units["codev-clock-resync.service"]
    assert "hwclock" in svc
    assert "Type=oneshot" in svc


def test_clock_resync_timer_periodic():
    units = ms.render_clock_resync_units(user="tester")
    timer = units["codev-clock-resync.timer"]
    assert "OnUnitActiveSec=5min" in timer
    assert "OnBootSec=1min" in timer
    assert "WantedBy=timers.target" in timer


def test_install_systemd_includes_agent_and_clock(tmp_path):
    res = ms.install_systemd(_cfg(tmp_path), user="tester")
    names = res["units"]
    assert "codev-agent.service" in names
    assert "codev-clock-resync.service" in names
    assert "codev-clock-resync.timer" in names
