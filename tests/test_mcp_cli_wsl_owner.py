"""serve-mcp 在 Windows/WSL 数据 owner 模式下的 CLI 路由。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from codev_platform.cli_cmds import mcp as mcp_cli
from codev_platform.core.wsl_data_owner import WslDataOwner


def test_serve_mcp_start_routes_to_platform_source_and_wsl_systemd(monkeypatch):
    cfg = {"data": {"platform_data_dir": r"\\wsl.localhost\Ubuntu\home\demo\data"}}
    owner = WslDataOwner("Ubuntu", "/home/demo/data")
    calls: list[object] = []
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg)
    monkeypatch.setattr(mcp_cli, "wsl_data_owner", lambda _cfg: owner)
    monkeypatch.setattr(
        "codev_platform.mcp_source_client.ensure_source_serving",
        lambda got_cfg, target, *, start_unit: (
            calls.append((got_cfg, target)),
            start_unit("codev-mcp-platform-docs.service"),
            [{"name": "platform-docs", "action": "started", "status": "starting"}],
        )[-1],
    )
    monkeypatch.setattr(
        mcp_cli,
        "_start_wsl_systemd_unit",
        lambda got_owner, unit: calls.append((got_owner, unit)) or {"action": "started"},
    )
    monkeypatch.setattr(
        "codev_platform.mcp_serve.ensure_serving",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("不得拉起 Windows 18xxx 影子服务")),
    )

    rc = mcp_cli.cmd_serve_mcp(
        SimpleNamespace(action="start", wait=False, timeout=60),
    )

    assert rc == 0
    assert calls == [
        (cfg, "platform"),
        (owner, "codev-mcp-platform-docs.service"),
    ]


def test_wsl_systemd_adapter_uses_allow_list_and_exact_argv(monkeypatch):
    owner = WslDataOwner("Ubuntu", "/srv/codev/data")
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 20
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mcp_cli.subprocess, "run", run)

    assert mcp_cli._start_wsl_systemd_unit(
        owner,
        "codev-mcp-platform-docs.service",
    ) == {"action": "started"}
    assert calls == [[
        "wsl.exe", "-d", "Ubuntu", "-u", "root", "--",
        "systemctl", "start", "codev-mcp-platform-docs.service",
    ]]

    with pytest.raises(ValueError, match="不在受管注册表"):
        mcp_cli._start_wsl_systemd_unit(owner, "attacker.service")


def test_serve_mcp_status_owner_uses_platform_source_only(monkeypatch):
    cfg = {"data": {"platform_data_dir": r"\\wsl.localhost\Ubuntu\srv\codev\data"}}
    owner = WslDataOwner("Ubuntu", "/srv/codev/data")
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg)
    monkeypatch.setattr(mcp_cli, "wsl_data_owner", lambda _cfg: owner)
    monkeypatch.setattr(
        "codev_platform.mcp_source_client.probe_source_all",
        lambda got_cfg, target: [{
            "name": "platform-docs",
            "kind": "chroma",
            "port": 19083,
            "status": "ok",
            "reason": "",
            "sse_url": "http://127.0.0.1:19083/sse",
        }],
    )
    monkeypatch.setattr(
        "codev_platform.mcp_serve.probe_all",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local probe forbidden")),
    )

    assert mcp_cli.cmd_serve_mcp(SimpleNamespace(action="status")) == 0


def test_serve_mcp_start_owner_propagates_start_failure(monkeypatch):
    cfg = {"data": {"platform_data_dir": r"\\wsl.localhost\Ubuntu\srv\codev\data"}}
    owner = WslDataOwner("Ubuntu", "/srv/codev/data")
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg)
    monkeypatch.setattr(mcp_cli, "wsl_data_owner", lambda _cfg: owner)
    monkeypatch.setattr(
        "codev_platform.mcp_source_client.ensure_source_serving",
        lambda *_args, **_kwargs: [{"name": "graph", "action": "failed", "error": "rc=1"}],
    )

    rc = mcp_cli.cmd_serve_mcp(SimpleNamespace(action="start", wait=False, timeout=60))

    assert rc == 1


def test_serve_mcp_start_wait_owner_never_uses_local_wait(monkeypatch):
    cfg = {"data": {"platform_data_dir": r"\\wsl.localhost\Ubuntu\srv\codev\data"}}
    owner = WslDataOwner("Ubuntu", "/srv/codev/data")
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: cfg)
    monkeypatch.setattr(mcp_cli, "wsl_data_owner", lambda _cfg: owner)
    monkeypatch.setattr(
        "codev_platform.mcp_source_client.ensure_source_serving",
        lambda *_args, **_kwargs: [{"name": "graph", "action": "already-up"}],
    )
    monkeypatch.setattr(
        "codev_platform.mcp_source_client.wait_until_source_serving",
        lambda got_cfg, target, *, timeout: (
            calls.append((got_cfg, target, timeout))
            or [{"name": "graph", "status": "ok"}]
        ),
    )
    monkeypatch.setattr(
        "codev_platform.mcp_serve.wait_until_serving",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("local wait forbidden")),
    )

    rc = mcp_cli.cmd_serve_mcp(SimpleNamespace(action="start", wait=True, timeout=9))

    assert rc == 0
    assert calls == [(cfg, "platform", 9.0)]
