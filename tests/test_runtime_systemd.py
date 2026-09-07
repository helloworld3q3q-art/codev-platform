"""版本化运行时的 systemd 单元与安装门禁测试。"""

from __future__ import annotations

import os
import shlex
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform import mcp_systemd
from codev_platform import cli
from codev_platform.cli_cmds import mcp as mcp_command
from codev_platform.cli_cmds.mcp import cmd_serve_mcp
from codev_platform.core.runtime_models import SystemdRuntime
from codev_platform.core import runtime_identity as runtime_identity_module
from tests.runtime_support import minimal_config


_PYTHON_UNIT_NAMES = frozenset(
    {
        "codev-mcp-platform-docs.service",
        "codev-mcp-codegraph.service",
        "codev-mcp-agent-memory.service",
        "codev-mcp-graph.service",
        "codev-reindex.service",
        "codev-webhook.service",
        "codev-agent.service",
        "codev-web.service",
        "codev-memory-maintenance.service",
    }
)
_MANAGED_UNIT_NAMES = _PYTHON_UNIT_NAMES | {
    "codev-clock-resync.service",
    "codev-clock-resync.timer",
    "codev-memory-maintenance.timer",
}
_TEST_RELEASE_ID = "3" * 64
_TEST_RELEASE_ROOT = Path("/var/lib/codev-platform/runtime")


def _patch_release_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        mcp_systemd,
        "_snapshot_systemd_release",
        lambda _root: SimpleNamespace(
            root=_TEST_RELEASE_ROOT,
            release_id=_TEST_RELEASE_ID,
            runtime_revision="2" * 40,
        ),
    )


def _patch_service_account(monkeypatch, home: Path = Path("/home/service-user")) -> None:
    """隔离安装器的账号目录解析，避免宿主机账户影响声明测试。"""
    monkeypatch.setattr(
        mcp_systemd,
        "resolve_service_account",
        lambda user: SimpleNamespace(name=user, home=home),
    )


def _cfg(tmp_path: Path) -> dict[str, object]:
    runtime_root = tmp_path / "runtime"
    cfg = minimal_config(runtime_root)
    cfg["runtime"] = {
        "release_root": str(runtime_root),
        "chroma_venv": str(tmp_path / "legacy" / ".venv"),
    }
    cfg["daemon"] = {"port": 18083}
    cfg["mcp"] = {
        "agent_memory_sse_port": 18087,
        "codegraph_sse_port": 18091,
        "graph_sse_port": 18092,
    }
    cfg["projects"] = {}
    cfg["systemd"] = {"env_file": "/etc/codev-platform/platform.env"}
    return cfg


def _python_units(
    cfg: dict[str, object],
    runtime: SystemdRuntime,
) -> dict[str, str]:
    units = mcp_systemd.render_systemd_units(cfg, "service-user", runtime=runtime)
    for name, content in (
        mcp_systemd.render_reindex_unit(cfg, "service-user", runtime=runtime),
        mcp_systemd.render_webhook_unit(cfg, "service-user", runtime=runtime),
        mcp_systemd.render_agent_unit(cfg, "service-user", runtime=runtime),
        mcp_systemd.render_web_unit(cfg, "service-user", runtime=runtime),
    ):
        units[name] = content
    units.update(
        mcp_systemd.render_memory_maintenance_units(
            cfg,
            "service-user",
            runtime=runtime,
        )
    )
    return {name: content for name, content in units.items() if name in _PYTHON_UNIT_NAMES}


def _exec_argv(content: str) -> list[str]:
    line = next(line for line in content.splitlines() if line.startswith("ExecStart="))
    return shlex.split(line.removeprefix("ExecStart="))


def test_rewrite_python_argv_replaces_only_interpreter_and_ensures_one_isolated_flag(
    tmp_path: Path,
) -> None:
    runtime = SystemdRuntime(tmp_path / "runtime")

    assert mcp_systemd.rewrite_python_argv(
        ["/legacy/python", "-m", "package.module", "--flag"],
        runtime,
    ) == [
        str(runtime.python),
        "-I",
        "-m",
        "package.module",
        "--flag",
    ]
    assert (
        mcp_systemd.rewrite_python_argv(
            ["/legacy/python", "-I", "-B", "-I", "-m", "package.module"],
            runtime,
        ).count("-I")
        == 1
    )


def test_all_python_units_share_runtime_environment_and_current_python(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _cfg(tmp_path)
    runtime = SystemdRuntime(tmp_path / "runtime")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "checkout"))

    units = _python_units(cfg, runtime)

    assert set(units) == _PYTHON_UNIT_NAMES
    release = runtime.release_root / "current"
    expected_lines = (
        "EnvironmentFile=/etc/codev-platform/platform.env",
        "Environment=PYTHONPATH=",
        "Environment=PYTHONHOME=",
        "Environment=PYTHONNOUSERSITE=1",
        "Environment=PYTHONDONTWRITEBYTECODE=1",
        f"Environment=CODEV_PLATFORM_RELEASE_FILE={release}/release.json",
        f"Environment=PATH={runtime.python.parent}:"
        f"{release}/base/venv/bin:/usr/local/bin:/usr/bin:/bin",
        "WorkingDirectory=%h",
    )
    for name, content in units.items():
        lines = content.splitlines()
        for expected in expected_lines:
            assert expected in lines, (name, expected)
        positions = tuple(lines.index(line) for line in expected_lines)
        assert positions == tuple(range(positions[0], positions[0] + len(positions))), name
        assert "User=service-user\n" in content, name
        assert str(tmp_path / "legacy") not in content, name
        assert str(tmp_path / "checkout") not in content, name
        argv = _exec_argv(content)
        assert argv[0] == str(runtime.python), name
        assert argv[1] == "-I", name
        assert argv.count("-I") == 1, name


def test_reindex_runtime_unit_preserves_attempt_cgroup_fence(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    runtime = SystemdRuntime(tmp_path / "runtime")

    name, content = mcp_systemd.render_reindex_unit(
        cfg,
        "service-user",
        runtime=runtime,
    )

    assert name == "codev-reindex.service"
    assert "Delegate=yes\n" in content
    assert "KillMode=control-group\n" in content
    assert "TimeoutStopSec=30\n" in content
    assert "SendSIGKILL=yes\n" in content


def test_memory_maintenance_uses_packaged_module_not_checkout_script(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    runtime = SystemdRuntime(tmp_path / "runtime")

    service = mcp_systemd.render_memory_maintenance_units(
        cfg,
        "service-user",
        runtime=runtime,
    )["codev-memory-maintenance.service"]

    assert _exec_argv(service) == [
        str(runtime.python),
        "-I",
        "-m",
        "codev_platform.ops.memory_maintenance",
    ]
    assert "scripts/run_memory_maintenance" not in service


def _install_payload(
    tmp_path: Path,
    monkeypatch,
    *,
    no_restart: bool,
) -> tuple[dict[str, object], SystemdRuntime]:
    runtime = SystemdRuntime(tmp_path / "runtime")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        runtime_identity_module,
        "runtime_identity",
        lambda: SimpleNamespace(runtime_revision="2" * 40),
    )
    _patch_release_snapshot(monkeypatch)
    _patch_service_account(monkeypatch)
    result = mcp_systemd.install_systemd(
        _cfg(tmp_path),
        "service-user",
        runtime=runtime,
        no_restart=no_restart,
        platform_name="linux",
    )
    return result, runtime


def test_install_script_delegates_preflight_and_install_to_one_locked_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    result, _runtime = _install_payload(tmp_path, monkeypatch, no_restart=False)

    transaction = list(result["transaction_argv"])
    assert transaction[:4] == [
        f"{_TEST_RELEASE_ROOT.as_posix()}/releases/{_TEST_RELEASE_ID}/venv/bin/python",
        "-I",
        "-m",
        "codev_platform.mcp_systemd_install_transaction",
    ]
    assert "--maintenance-stage" in transaction
    assert not (Path(str(result["dir"])) / "install.sh").exists()
    assert tuple(result["sudo_argv"]) == ("sudo", *transaction)
    assert set(result["units"]) == _MANAGED_UNIT_NAMES


def test_managed_unit_allowlist_is_explicit_and_drift_fails_before_writes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    assert mcp_systemd.MANAGED_SYSTEMD_UNIT_NAMES == frozenset(_MANAGED_UNIT_NAMES)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(mcp_systemd, "render_systemd_units", lambda *_args, **_kwargs: {})
    _patch_release_snapshot(monkeypatch)
    _patch_service_account(monkeypatch)

    with pytest.raises(ValueError, match="受管 systemd unit 集合漂移"):
        mcp_systemd.install_systemd(
            _cfg(tmp_path),
            "service-user",
            runtime=SystemdRuntime(tmp_path / "runtime"),
            platform_name="linux",
        )

    assert not (tmp_path / "codev-systemd").exists()


def test_generated_unit_modes_are_fixed_and_no_root_shell_is_generated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    if os.name != "posix":
        pytest.skip("Windows 文件系统不表达 POSIX 执行位")
    result, _runtime = _install_payload(tmp_path, monkeypatch, no_restart=False)
    output = Path(str(result["dir"]))

    assert not (output / "install.sh").exists()
    assert all(stat.S_IMODE((output / name).stat().st_mode) == 0o644 for name in result["units"])


def test_no_restart_script_uses_same_compensating_install_only_transaction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    result, _runtime = _install_payload(tmp_path, monkeypatch, no_restart=True)

    transaction = list(result["transaction_argv"])
    assert "--install-only" in transaction
    assert "--maintenance-stage" not in transaction
    assert not (Path(str(result["dir"])) / "install.sh").exists()
    assert result["no_restart"] is True


def test_install_systemd_parser_accepts_explicit_runtime_and_no_restart() -> None:
    args = cli.build_parser().parse_args(
        [
            "serve-mcp",
            "install-systemd",
            "--runtime-root",
            "/srv/codev-runtime",
            "--no-restart",
            "--config-user",
            "service-user",
        ]
    )

    assert args.runtime_root == Path("/srv/codev-runtime")
    assert args.no_restart is True
    assert args.config_user == "service-user"


def test_install_systemd_cli_passes_one_runtime_to_installer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    captured: dict[str, object] = {}
    runtime_root = tmp_path / "runtime"

    def install(cfg, user, *, runtime, no_restart):
        captured.update(
            cfg=cfg,
            user=user,
            runtime=runtime,
            no_restart=no_restart,
        )
        return {
            "dir": str(tmp_path / "units"),
            "units": ["codev-web.service"],
            "sudo_cmd": "sudo bash install.sh",
            "no_restart": no_restart,
        }

    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: _cfg(tmp_path))
    monkeypatch.setattr("codev_platform.mcp_serve.install_systemd", install)
    args = SimpleNamespace(
        action="install-systemd",
        user="service-user",
        runtime_root=runtime_root,
        no_restart=True,
    )

    assert cmd_serve_mcp(args) == 0

    assert captured["user"] == "service-user"
    assert captured["runtime"] == SystemdRuntime(runtime_root)
    assert captured["no_restart"] is True
    output = capsys.readouterr().out
    assert "不会启动、停止或重启" in output
    assert "reindex-maintenance prepare" not in output


def test_install_systemd_cli_root_config_user采用目标_home_而不改变输出账号(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}
    service_home = tmp_path / "service-home"

    def load_config(*, home: Path | None = None) -> dict[str, object]:
        captured["config_home"] = home
        return _cfg(tmp_path)

    def install(cfg, user, *, runtime, no_restart):
        captured.update(cfg=cfg, user=user, runtime=runtime, no_restart=no_restart)
        return {
            "dir": str(tmp_path / "root-units"),
            "units": [],
            "sudo_cmd": "sudo transaction",
            "no_restart": no_restart,
        }

    monkeypatch.setattr("codev_platform.core.config.load_config", load_config)
    monkeypatch.setattr("codev_platform.mcp_serve.install_systemd", install)
    monkeypatch.setattr(
        "codev_platform.runtime_service_process.resolve_service_account",
        lambda user: SimpleNamespace(name=user, home=service_home),
    )
    monkeypatch.setattr(
        mcp_command,
        "os",
        SimpleNamespace(name="posix", geteuid=lambda: 0, environ=os.environ),
    )
    args = SimpleNamespace(
        action="install-systemd",
        user="service-user",
        config_user="service-user",
        runtime_root=tmp_path / "runtime",
        no_restart=True,
    )

    assert cmd_serve_mcp(args) == 0

    assert captured["config_home"] == service_home
    assert captured["user"] == "service-user"
    assert captured["runtime"] == SystemdRuntime(tmp_path / "runtime")


def test_install_systemd_cli_config_user拒绝非_root(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "codev_platform.mcp_serve.install_systemd",
        lambda *_args, **_kwargs: pytest.fail("非 root 不得进入安装器"),
    )
    args = SimpleNamespace(
        action="install-systemd",
        user="service-user",
        config_user="service-user",
        runtime_root=tmp_path / "runtime",
        no_restart=True,
    )

    assert cmd_serve_mcp(args) == 1

    assert "仅允许 Linux root" in capsys.readouterr().err


def test_install_systemd_cli_config_user拒绝与目标用户不一致(
    tmp_path: Path,
    capsys,
) -> None:
    args = SimpleNamespace(
        action="install-systemd",
        user="other-user",
        config_user="service-user",
        runtime_root=tmp_path / "runtime",
        no_restart=True,
    )

    assert cmd_serve_mcp(args) == 1

    assert "必须与 --user 一致" in capsys.readouterr().err


def test_install_systemd_cli_config_user要求显式目标用户(
    tmp_path: Path,
    capsys,
) -> None:
    args = SimpleNamespace(
        action="install-systemd",
        user=None,
        config_user="service-user",
        runtime_root=tmp_path / "runtime",
        no_restart=True,
    )

    assert cmd_serve_mcp(args) == 1

    assert "必须与显式 --user 一起使用" in capsys.readouterr().err
