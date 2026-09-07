"""通用 systemd 安装脚本的首次部署门禁约束。"""

from __future__ import annotations

import json
import hashlib
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform import mcp_systemd as systemd
from codev_platform.core.runtime_models import SystemdRuntime
import codev_platform.core.runtime_identity as runtime_identity_module


_TEST_RUNTIME_REVISION = "2" * 40
_TEST_RELEASE_ID = "3" * 64
_TEST_RELEASE_ROOT = Path("/var/lib/codev-platform/runtime")


def _cfg(venv_path: Path) -> dict:
    return {
        "runtime": {
            "chroma_venv": str(venv_path),
            "release_root": str(venv_path.parent / "systemd-runtime"),
        },
        "daemon": {"port": 18083},
        "mcp": {"graph_sse_port": 18092, "codegraph_sse_port": 18095},
        "projects": {},
    }


def _测试venv解释器(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _绑定测试运行时版本(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_identity_module,
        "runtime_identity",
        lambda: SimpleNamespace(runtime_revision=_TEST_RUNTIME_REVISION),
    )
    monkeypatch.setattr(
        systemd,
        "_snapshot_systemd_release",
        lambda _root: SimpleNamespace(
            root=_TEST_RELEASE_ROOT,
            release_id=_TEST_RELEASE_ID,
            runtime_revision=_TEST_RUNTIME_REVISION,
        ),
    )


def _绑定测试服务账号(monkeypatch, home: Path = Path("/home/tester")) -> None:
    monkeypatch.setattr(
        systemd,
        "resolve_service_account",
        lambda user: SimpleNamespace(name=user, home=home),
    )


def _render_install_script(monkeypatch, tmp_path: Path, cfg: dict) -> tuple[list[str], Path]:
    """把用户目录隔离到临时目录，并提取不可变 Python 事务参数。"""
    runtime = SystemdRuntime(Path(cfg["runtime"]["release_root"]))
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    _绑定测试运行时版本(monkeypatch)
    _绑定测试服务账号(monkeypatch)
    result = systemd.install_systemd(
        cfg,
        user="tester",
        runtime=runtime,
        platform_name="linux",
    )
    assert not (Path(result["dir"]) / "install.sh").exists()
    lines = [f"exec {shlex.join(result['transaction_argv'])}"]
    return lines, runtime.python


def test_install_script把全部安装副作用委派给受控事务(monkeypatch, tmp_path):
    """Bash 不复制门禁逻辑，实际安装由维护排他锁内的事务统一执行。"""
    cfg = _cfg(tmp_path / "venv with 'quote")

    lines, platform_python = _render_install_script(monkeypatch, tmp_path, cfg)

    transaction = next(line for line in lines if "mcp_systemd_install_transaction" in line)
    command = shlex.split(transaction.removeprefix("exec "))
    manifest_path = Path(command[command.index("--manifest") + 1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert command == [
        f"{_TEST_RELEASE_ROOT.as_posix()}/releases/{_TEST_RELEASE_ID}/venv/bin/python",
        "-I",
        "-m",
        "codev_platform.mcp_systemd_install_transaction",
        "--manifest",
        str(manifest_path),
        "--maintenance-stage",
    ]
    assert manifest["version"] == 5
    assert manifest["runtime_revision"] == _TEST_RUNTIME_REVISION
    assert manifest["runtime_binding"] == {
        "release_root": _TEST_RELEASE_ROOT.as_posix(),
        "expected_release_id": _TEST_RELEASE_ID,
        "target_user": "tester",
        "environment_file": None,
    }
    assert {item["source"] for item in manifest["units"]} == {
        str(tmp_path / "codev-systemd" / name)
        for name in {
            "codev-mcp-platform-docs.service",
            "codev-mcp-codegraph.service",
            "codev-mcp-agent-memory.service",
            "codev-mcp-graph.service",
            "codev-reindex.service",
            "codev-webhook.service",
            "codev-agent.service",
            "codev-web.service",
            "codev-clock-resync.service",
            "codev-clock-resync.timer",
            "codev-memory-maintenance.service",
            "codev-memory-maintenance.timer",
        }
    }
    actions = {
        Path(item["source"]).name: (item["enable"], item["restart"]) for item in manifest["units"]
    }
    assert actions == {
        "codev-mcp-platform-docs.service": (True, True),
        "codev-mcp-codegraph.service": (True, True),
        "codev-mcp-agent-memory.service": (True, True),
        "codev-mcp-graph.service": (True, True),
        "codev-reindex.service": (True, True),
        "codev-webhook.service": (True, True),
        "codev-agent.service": (True, True),
        "codev-web.service": (True, True),
        "codev-clock-resync.service": (False, False),
        "codev-clock-resync.timer": (True, True),
        "codev-memory-maintenance.service": (False, False),
        "codev-memory-maintenance.timer": (True, True),
    }
    assert all(
        set(item) == {"source", "sha256", "enable", "restart", "activation_mode"}
        for item in manifest["units"]
    )
    activation_modes = {
        Path(item["source"]).name: item["activation_mode"] for item in manifest["units"]
    }
    assert activation_modes["codev-reindex.service"] == "reindex_state_machine"
    assert activation_modes["codev-mcp-codegraph.service"] == "codegraph_state_machine"
    assert activation_modes["codev-webhook.service"] == "ingress_state_machine"
    assert {
        value
        for name, value in activation_modes.items()
        if name
        not in {
            "codev-reindex.service",
            "codev-mcp-codegraph.service",
            "codev-webhook.service",
        }
    } == {"standard"}
    assert {Path(item["source"]).name: item["sha256"] for item in manifest["units"]} == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in manifest_path.parent.glob("*.service")
    } | {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in manifest_path.parent.glob("*.timer")
    }
    assert not any(line.startswith("cp ") for line in lines)
    assert not any(line.startswith("systemctl ") for line in lines)
    assert not any("reindex-maintenance provision" in line for line in lines)


def test_install_script显式使用维护态且不在Bash复制marker或mask判断(monkeypatch, tmp_path):
    """全部维护证明都在同一把排他锁内由 Python 状态机完成。"""
    cfg = _cfg(tmp_path / "venv")

    lines, _ = _render_install_script(monkeypatch, tmp_path, cfg)

    transaction = next(line for line in lines if "mcp_systemd_install_transaction" in line)
    assert "--maintenance-stage" in shlex.split(transaction.removeprefix("exec "))
    assert not any("reindex-maintenance.gate" in line for line in lines)
    assert not any("/run/systemd/system" in line for line in lines)


def test_install_script_shell_quotes_platform_python(monkeypatch, tmp_path):
    """带空格和单引号的 Chroma 配置不能影响平台安装事务解释器。"""
    cfg = _cfg(tmp_path / "venv with 'quote")

    lines, platform_python = _render_install_script(monkeypatch, tmp_path, cfg)
    transaction = next(line for line in lines if "mcp_systemd_install_transaction" in line)

    assert shlex.split(transaction.removeprefix("exec "))[:4] == [
        f"{_TEST_RELEASE_ROOT.as_posix()}/releases/{_TEST_RELEASE_ID}/venv/bin/python",
        "-I",
        "-m",
        "codev_platform.mcp_systemd_install_transaction",
    ]


def test_sudo安装命令对带空格和单引号的输出目录保持可逆(monkeypatch, tmp_path):
    """操作员复制唯一 sudo 命令时，用户目录字符不能改变声明路径。"""
    home = tmp_path / "home with 'quote"
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))

    cfg = _cfg(tmp_path / "venv")
    _绑定测试运行时版本(monkeypatch)
    _绑定测试服务账号(monkeypatch)
    result = systemd.install_systemd(cfg, user="tester", platform_name="linux")

    assert shlex.split(result["sudo_cmd"]) == [
        "sudo",
        f"{_TEST_RELEASE_ROOT.as_posix()}/releases/{_TEST_RELEASE_ID}/venv/bin/python",
        "-I",
        "-m",
        "codev_platform.mcp_systemd_install_transaction",
        "--manifest",
        str(home / "codev-systemd" / "install-manifest.json"),
        "--maintenance-stage",
    ]
    assert not (home / "codev-systemd" / "install.sh").exists()


def test_生产安装manifest使用目标服务账号home而非root百分号home(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path / "venv")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    _绑定测试运行时版本(monkeypatch)
    _绑定测试服务账号(monkeypatch, Path("/srv/codev-service"))

    systemd.install_systemd(cfg, user="tester", platform_name="linux")

    output_dir = tmp_path / "codev-systemd"
    for name in ("codev-reindex.service", "codev-mcp-platform-docs.service"):
        content = (output_dir / name).read_text(encoding="utf-8")
        assert "WorkingDirectory=/srv/codev-service\n" in content
        assert "WorkingDirectory=%h\n" not in content


def test_生产安装无法解析服务账号时拒绝生成manifest(monkeypatch, tmp_path):
    cfg = _cfg(tmp_path / "venv")
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    _绑定测试运行时版本(monkeypatch)
    monkeypatch.setattr(
        systemd,
        "resolve_service_account",
        lambda _user: (_ for _ in ()).throw(RuntimeError("lookup failed")),
    )

    with pytest.raises(RuntimeError, match="服务账号工作目录无法解析"):
        systemd.install_systemd(cfg, user="tester", platform_name="linux")

    assert not (tmp_path / "codev-systemd").exists()


@pytest.mark.parametrize("working_directory", ("relative/path", "/srv/../unsafe", "/srv\nunsafe"))
def test_运行时环境拒绝不安全工作目录(working_directory: str, tmp_path):
    from codev_platform.mcp_systemd_runtime import render_runtime_environment_lines

    runtime = SystemdRuntime(Path(_cfg(tmp_path / "venv")["runtime"]["release_root"]))

    with pytest.raises(ValueError, match="工作目录无效"):
        render_runtime_environment_lines(
            "",
            runtime,
            working_directory=working_directory,
        )


def test_codegraph单元固定关闭watcher与共享daemon(tmp_path):
    """常驻 MCP 即使以后更换启动路径，也不得重新启用 watcher 或共享 daemon。"""
    units = systemd.render_systemd_units(
        _cfg(tmp_path / "venv"),
        user="tester",
        kinds=("codegraph",),
    )

    content = units["codev-mcp-codegraph.service"]

    assert "Environment=CODEGRAPH_NO_WATCH=1\n" in content
    assert "Environment=CODEGRAPH_NO_DAEMON=1\n" in content
    assert "KillMode=control-group\n" in content
    assert "TimeoutStopSec=30\n" in content
    assert "SendSIGKILL=yes\n" in content
    assert "Restart=always\n" in content
    assert "ConditionPathExists=!/var/lib/codev-platform/codegraph-maintenance.gate\n" in content


def test_systemd平台服务不继承chroma专用解释器(tmp_path):
    chroma_venv = tmp_path / "chroma-venv"
    cfg = _cfg(chroma_venv)
    runtime = SystemdRuntime(Path(cfg["runtime"]["release_root"]))
    platform_python = runtime.python

    units = systemd.render_systemd_units(
        cfg,
        user="tester",
        kinds=("codegraph",),
        runtime=runtime,
    )
    reindex_name, reindex = systemd.render_reindex_unit(
        cfg,
        user="tester",
        runtime=runtime,
    )

    assert (
        f"ExecStart={shlex.quote(str(platform_python))} -I -m codev_platform.codegraph.server"
        in units["codev-mcp-codegraph.service"]
    )
    assert (
        f"PATH={platform_python.parent}:{runtime.release_root / 'current'}/base/venv/bin:"
        "/usr/local/bin:/usr/bin:/bin\n" in (units["codev-mcp-codegraph.service"])
    )
    assert reindex_name == "codev-reindex.service"
    assert (
        f"ExecStart={shlex.quote(str(platform_python))} "
        "-I -m codev_platform.cli reindex-queue worker"
    ) in reindex


def test_web后台纳入受管release与配置端口(tmp_path):
    """历史手工 codev-web unit 必须进入同一 manifest，不能留下旧 release shadow。"""
    cfg = _cfg(tmp_path / "chroma-venv")
    cfg["web"] = {"host": "0.0.0.0", "port": 18088}
    runtime = SystemdRuntime(Path(cfg["runtime"]["release_root"]))

    name, content = systemd.render_web_unit(cfg, user="tester", runtime=runtime)

    assert name == "codev-web.service"
    assert (
        f"ExecStart={shlex.quote(str(runtime.python))} -I -m codev_platform.cli "
        "web serve --host 0.0.0.0 --port 18088"
    ) in content
    assert "WorkingDirectory=%h\n" in content
    assert "User=tester\n" in content
    assert "Restart=always\n" in content


def test_systemd全部平台单元与安装事务不继承chroma解释器(monkeypatch, tmp_path):
    """全部受管 Python 进程必须随 current release 解释器切换。"""
    chroma_venv = tmp_path / "chroma-venv"
    cfg = _cfg(chroma_venv)
    runtime = SystemdRuntime(Path(cfg["runtime"]["release_root"]))
    platform_python = runtime.python

    mcp_units = systemd.render_systemd_units(cfg, user="tester", runtime=runtime)
    _, reindex_unit = systemd.render_reindex_unit(cfg, user="tester", runtime=runtime)
    _, webhook_unit = systemd.render_webhook_unit(cfg, user="tester", runtime=runtime)
    _, agent_unit = systemd.render_agent_unit(cfg, user="tester", runtime=runtime)
    _, web_unit = systemd.render_web_unit(cfg, user="tester", runtime=runtime)
    maintenance_units = systemd.render_memory_maintenance_units(
        cfg,
        user="tester",
        runtime=runtime,
    )
    lines, rendered_platform_python = _render_install_script(monkeypatch, tmp_path, cfg)

    platform_execstart = f"ExecStart={shlex.quote(str(platform_python))}"
    platform_units = (
        mcp_units["codev-mcp-platform-docs.service"],
        mcp_units["codev-mcp-codegraph.service"],
        mcp_units["codev-mcp-agent-memory.service"],
        mcp_units["codev-mcp-graph.service"],
        reindex_unit,
        webhook_unit,
        agent_unit,
        web_unit,
        maintenance_units["codev-memory-maintenance.service"],
    )
    assert all(platform_execstart in content for content in platform_units)
    transaction = next(line for line in lines if "mcp_systemd_install_transaction" in line)
    assert shlex.split(transaction.removeprefix("exec "))[:3] == [
        f"{_TEST_RELEASE_ROOT.as_posix()}/releases/{_TEST_RELEASE_ID}/venv/bin/python",
        "-I",
        "-m",
    ]
    assert shlex.split(transaction.removeprefix("exec "))[3] == (
        "codev_platform.mcp_systemd_install_transaction"
    )


def test_受管安装仅将stage受保护写服务绑定不可变解释器(monkeypatch, tmp_path):
    """普通服务保持 current，stage 写服务必须固定到本次绑定的 release。"""
    legacy_chroma_venv = tmp_path / "legacy-chroma-venv"
    cfg = _cfg(legacy_chroma_venv)

    _lines, current_python = _render_install_script(monkeypatch, tmp_path, cfg)

    protected_units = {
        "codev-mcp-codegraph.service",
        "codev-reindex.service",
    }
    current_units = {
        "codev-mcp-platform-docs.service",
        "codev-mcp-agent-memory.service",
        "codev-mcp-graph.service",
        "codev-webhook.service",
        "codev-agent.service",
        "codev-web.service",
        "codev-memory-maintenance.service",
    }
    contents = {
        name: (tmp_path / "codev-systemd" / name).read_text(encoding="utf-8")
        for name in protected_units | current_units
    }
    immutable_python = (
        f"{_TEST_RELEASE_ROOT.as_posix()}/releases/{_TEST_RELEASE_ID}/venv/bin/python"
    )
    immutable_expected = f"ExecStart={shlex.quote(immutable_python)}"
    current_expected = f"ExecStart={shlex.quote(str(current_python))}"
    legacy_python = str(_测试venv解释器(legacy_chroma_venv))

    assert all(immutable_expected in contents[name] for name in protected_units)
    assert all(current_expected in contents[name] for name in current_units)
    assert all(current_expected not in contents[name] for name in protected_units)
    assert all(immutable_expected not in contents[name] for name in current_units)
    assert all(legacy_python not in content for content in contents.values())
    clock_content = (tmp_path / "codev-systemd" / "codev-clock-resync.service").read_text(
        encoding="utf-8"
    )
    clock_exec_start = next(
        line for line in clock_content.splitlines() if line.startswith("ExecStart=")
    )
    assert shlex.split(clock_exec_start.removeprefix("ExecStart=")) == [
        "/bin/sh",
        "-c",
        "/sbin/hwclock --hctosys || hwclock -s || true",
    ]


def test_systemd_python单元清空外部导入环境且使用隔离模式(tmp_path):
    """受控 EnvironmentFile 不能让宿主 PYTHONPATH 覆盖 release 的可编辑安装。"""
    cfg = _cfg(tmp_path / "chroma-venv")
    cfg["systemd"] = {"env_file": "/etc/codev-platform/service.env"}
    runtime = SystemdRuntime(Path(cfg["runtime"]["release_root"]))
    mcp_units = systemd.render_systemd_units(cfg, user="tester", runtime=runtime)
    _, reindex_unit = systemd.render_reindex_unit(cfg, user="tester", runtime=runtime)
    _, webhook_unit = systemd.render_webhook_unit(cfg, user="tester", runtime=runtime)
    _, agent_unit = systemd.render_agent_unit(cfg, user="tester", runtime=runtime)
    _, web_unit = systemd.render_web_unit(cfg, user="tester", runtime=runtime)
    maintenance = systemd.render_memory_maintenance_units(
        cfg,
        user="tester",
        runtime=runtime,
    )
    contents = (
        *mcp_units.values(),
        reindex_unit,
        webhook_unit,
        agent_unit,
        web_unit,
        maintenance["codev-memory-maintenance.service"],
    )

    hygiene = (
        "Environment=PYTHONPATH=\n"
        "Environment=PYTHONHOME=\n"
        "Environment=PYTHONNOUSERSITE=1\n"
        "Environment=PYTHONDONTWRITEBYTECODE=1\n"
        "Environment=CODEV_PLATFORM_RELEASE_FILE="
        f"{runtime.release_root / 'current'}/release.json\n"
        f"Environment=PATH={runtime.python.parent}:"
        f"{runtime.release_root / 'current'}/base/venv/bin:"
        "/usr/local/bin:/usr/bin:/bin\n"
    )
    assert all(hygiene in content for content in contents)
    assert all("ExecStart=" in content and " -I " in content for content in contents)
    assert all(
        content.index("EnvironmentFile=/etc/codev-platform/service.env\n")
        < content.index("Environment=PYTHONPATH=\n")
        for content in contents
    )


def test_systemd保留受控的CodeGraph命令覆盖(tmp_path):
    """绝对 CODEGRAPH_CMD 是既有服务能力，不能被通用导入隔离静默清空。"""
    cfg = _cfg(tmp_path / "chroma-venv")
    cfg["systemd"] = {"env_file": "/etc/codev-platform/service.env"}

    units = systemd.render_systemd_units(cfg, user="tester", kinds=("codegraph",))
    unit = units["codev-mcp-codegraph.service"]

    assert "EnvironmentFile=/etc/codev-platform/service.env\n" in unit
    assert "Environment=CODEGRAPH_CMD=\n" not in unit


def test_受管环境文件存在时必须作为必需配置加载(tmp_path):
    """恢复同源证明拒绝可选环境文件，渲染器不得再生成忽略缺失的语义。"""
    cfg = _cfg(tmp_path / "venv")
    cfg["systemd"] = {"env_file": "/etc/codev-platform/service.env"}

    units = systemd.render_systemd_units(cfg, user="tester", kinds=("codegraph",))

    content = units["codev-mcp-codegraph.service"]
    assert "EnvironmentFile=/etc/codev-platform/service.env\n" in content
    assert "EnvironmentFile=-/etc/codev-platform/service.env\n" not in content


@pytest.mark.parametrize(
    "value",
    (
        "relative.env",
        "//etc/codev-platform/service.env",
        "/etc//codev-platform/service.env",
        "/etc/codev-platform/../service.env",
        "/etc/codev-platform/./service.env",
        "/etc/codev-platform/service env",
        "/etc/codev-platform/service\tenv",
        "/etc/codev-platform/service\nenv",
        "/etc/codev-platform/service\renv",
        "/etc/codev-platform/service\x01env",
        "/etc/codev-platform/service\x7fenv",
        "/etc/codev-platform/'service'.env",
        '/etc/codev-platform/"service".env',
        "/etc/codev-platform/service\\env",
        "/etc/codev-platform/*.env",
        "/etc/codev-platform/?.env",
        "/etc/codev-platform/[ab].env",
        "/etc/codev-platform/%n.env",
    ),
)
def test_systemd环境文件拒绝非字面POSIX绝对路径(value: str) -> None:
    """配置值只能表示一个固定文件，不能进入 systemd 的二次语法解释。"""
    with pytest.raises(ValueError, match="systemd.env_file"):
        systemd._environment_file_line({"systemd": {"env_file": value}})
