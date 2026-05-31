"""B9: 断言每个 systemd unit 都含 Restart=always —— 防未来有人移除自愈策略。

systemd unit 是开机自起 + 崩溃重启的常驻保障; 任一 unit 丢了 Restart=always 就会
退化成"挂了不拉起"。这里渲染全部 unit (MCP 端点 + reindex + webhook), 逐个断言。
"""
from __future__ import annotations

from codev_platform import mcp_serve as ms


def _cfg(tmp_path):
    # projects 非空 → codegraph 端点也产出; chroma / cross_link 恒在
    return {
        "daemon": {"port": 18083},
        "mcp": {"cross_link_sse_port": 18086, "codegraph_sse_port": 18095},
        "projects": {"proj-a": {"repo_path": str(tmp_path / "repo_a")}},
    }


def test_all_mcp_units_have_restart_always(tmp_path):
    units = ms.render_systemd_units(_cfg(tmp_path), user="tester")
    assert units, "render_systemd_units 应至少产出 chroma + cross_link + codegraph"
    for name, content in units.items():
        assert "Restart=always" in content, f"{name} 缺 Restart=always (自愈被移除?)"
        assert "RestartSec=3" in content, f"{name} 缺 RestartSec"


def test_reindex_unit_has_restart_always(tmp_path):
    _name, content = ms.render_reindex_unit(_cfg(tmp_path), user="tester")
    assert "Restart=always" in content


def test_webhook_unit_has_restart_always(tmp_path):
    _name, content = ms.render_webhook_unit(_cfg(tmp_path), user="tester")
    assert "Restart=always" in content


def test_codegraph_unit_present_and_self_healing(tmp_path):
    # codegraph 是单端点多租户代理 —— 确认它出现在 systemd 渲染里且自愈
    units = ms.render_systemd_units(_cfg(tmp_path), user="tester")
    cg = [c for n, c in units.items() if "codegraph" in n]
    assert cg, "codegraph unit 应被渲染"
    assert "Restart=always" in cg[0]
