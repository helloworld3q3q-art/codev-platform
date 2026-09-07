"""serve-mcp DOWN 原因诊断 + start --wait 轮询判定的纯函数单测 (P1 fullchain-audit-2026-06-01)。

只测纯函数 (diagnose_down / should_keep_waiting), 不触 IO (探测/spawn)。
"""
from __future__ import annotations


from codev_platform.mcp_serve import (
    MCPEndpoint,
    diagnose_down,
    should_keep_waiting,
)


def _ep(kind: str = "codegraph") -> MCPEndpoint:
    return MCPEndpoint(name="x", kind=kind, port=12345)


# ---------------------------------------------------------------- diagnose_down

def test_port_closed_unit_inactive():
    r = diagnose_down(_ep(), port_open=False, healthz_ok=False, unit_active=False)
    assert "未启动" in r and "unit" in r


def test_port_closed_unit_active():
    r = diagnose_down(_ep(), port_open=False, healthz_ok=False, unit_active=True)
    assert "active" in r and "端口未监听" in r


def test_port_closed_unit_unknown():
    r = diagnose_down(_ep(), port_open=False, healthz_ok=False, unit_active=None)
    assert "端口未监听" in r
    # 不提 systemd unit (未知态)
    assert "unit" not in r


def test_dep_missing_codegraph():
    r = diagnose_down(_ep("codegraph"), port_open=True, healthz_ok=False, dep_ok=False)
    assert "依赖缺失" in r and "codegraph" in r
    assert "mcp-proxy" not in r


def test_dep_missing_chroma():
    r = diagnose_down(_ep("chroma"), port_open=True, healthz_ok=False, dep_ok=False)
    assert "依赖缺失" in r and "chromadb" in r


def test_db_missing():
    r = diagnose_down(_ep("codegraph"), port_open=True, healthz_ok=False,
                      dep_ok=True, db_present=False)
    assert "数据缺失" in r and "codegraph 索引" in r


def test_healthz_abnormal_while_port_open():
    r = diagnose_down(_ep(), port_open=True, healthz_ok=False,
                      dep_ok=True, db_present=True)
    assert "/healthz" in r and "预热" in r


def test_all_normal_returns_empty():
    r = diagnose_down(_ep(), port_open=True, healthz_ok=True,
                      dep_ok=True, db_present=True)
    assert r == ""


def test_dep_takes_priority_over_db_and_healthz():
    # port 开 + dep 缺 + db 缺 + healthz 异常 -> 先报最根因 dep
    r = diagnose_down(_ep("codegraph"), port_open=True, healthz_ok=False,
                      dep_ok=False, db_present=False)
    assert "依赖缺失" in r


def test_port_closed_takes_priority_over_dep():
    # port 没开时不论 dep_ok 怎样, 都先报端口/进程问题
    r = diagnose_down(_ep(), port_open=False, healthz_ok=False,
                      unit_active=False, dep_ok=False, db_present=False)
    assert "未启动" in r


# ---------------------------------------------------------------- should_keep_waiting

def test_stop_when_all_ok():
    assert should_keep_waiting(elapsed=1.0, timeout=60.0, all_ok=True) is False


def test_keep_when_not_ok_and_within_timeout():
    assert should_keep_waiting(elapsed=5.0, timeout=60.0, all_ok=False) is True


def test_stop_when_timeout_reached():
    assert should_keep_waiting(elapsed=60.0, timeout=60.0, all_ok=False) is False
    assert should_keep_waiting(elapsed=61.0, timeout=60.0, all_ok=False) is False


def test_all_ok_overrides_timeout():
    # 即使超时, all_ok=True 也不再等
    assert should_keep_waiting(elapsed=100.0, timeout=60.0, all_ok=True) is False
