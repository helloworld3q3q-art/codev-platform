"""systemd 安装后主 unit 与有效解析结果的严格证明测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.mcp_systemd_effective_payload_support import (
    属性输出 as _属性输出,
    服务内容 as _服务内容,
    目标原像 as _目标原像,
    载荷 as _载荷,
)


def test_writer_noop时目标主unit原像证明失败(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-web.service", _服务内容())
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, b"[Service]\nExecStart=/old/python\n"),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: pytest.fail("落盘原像不一致时不得查询 systemd"),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="目标载荷"):
        module.default_effective_unit_payload_verifier((payload,))


def test_run层FragmentPath遮蔽受管目标时失败(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-web.service", _服务内容())
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=_属性输出("/run/systemd/system/codev-web.service"),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="FragmentPath"):
        module.default_effective_unit_payload_verifier((payload,))


def test_CodeGraph仅接受canonical目录FragmentPath(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-mcp-codegraph.service", _服务内容())
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=_属性输出("/etc/systemd/system/codev-mcp-codegraph.service"),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="FragmentPath"):
        module.default_effective_unit_payload_verifier((payload,))


def test_CodeGraph普通有效证明也拒绝缺失耐久维护条件(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    command = "/release/bin/python -I -m codev_platform.codegraph.server --http --port 18091"
    content = _服务内容(command)
    payload = _载荷(module, tmp_path, "codev-mcp-codegraph.service", content)
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=_属性输出(
                "/usr/local/lib/systemd/system/codev-mcp-codegraph.service",
                command=command,
            ),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="耐久维护条件"):
        module.default_effective_unit_payload_verifier((payload,))


@pytest.mark.parametrize(
    "unsafe_prefix",
    (
        b"Description=unsafe\\\n",
        b"Description=unsafe\r\n",
        b"Description=unsafe\x00\n",
        b"Description=unsafe\x0b",
        "Description=unsafe\u2028".encode(),
    ),
)
def test_CodeGraph条件证明拒绝续行回车或空字符绕过(
    tmp_path: Path,
    unsafe_prefix: bytes,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.mcp_systemd_effective_payload import (
        verify_codegraph_maintenance_condition,
    )

    content = (
        b"[Unit]\n"
        + unsafe_prefix
        + b"ConditionPathExists=!/var/lib/codev-platform/codegraph-maintenance.gate\n"
        b"[Service]\nExecStart=/release/bin/python -I -m "
        b"codev_platform.codegraph.server --http --port 18091\n"
    )
    payload = _载荷(module, tmp_path, "codev-mcp-codegraph.service", content)

    with pytest.raises(module.SystemdInstallTransactionError, match="指令"):
        verify_codegraph_maintenance_condition(payload)


@pytest.mark.parametrize("非ASCII空白", ("\u00a0", "\u2003", "\u3000"))
def test_CodeGraph条件证明拒绝Unicode空白伪装指令前缀(
    tmp_path: Path,
    非ASCII空白: str,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.mcp_systemd_effective_payload import (
        verify_codegraph_maintenance_condition,
    )

    content = (
        "[Unit]\n"
        f"{非ASCII空白}ConditionPathExists="
        "!/var/lib/codev-platform/codegraph-maintenance.gate\n"
        "[Service]\nExecStart=/release/bin/python -I -m "
        "codev_platform.codegraph.server --http --port 18091\n"
    ).encode()
    payload = _载荷(module, tmp_path, "codev-mcp-codegraph.service", content)

    with pytest.raises(module.SystemdInstallTransactionError, match="指令"):
        verify_codegraph_maintenance_condition(payload)


def test_systemd仍解析旧ExecStart时失败(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-web.service", _服务内容())
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=_属性输出(
                "/etc/systemd/system/codev-web.service",
                command="/old/release/bin/python -I -m codev_platform.cli web serve",
            ),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="ExecStart"):
        module.default_effective_unit_payload_verifier((payload,))


@pytest.mark.parametrize(
    "content",
    (
        "[Unit]\nDescription=hidden\u2028ExecStart=/usr/bin/true\n"
        "[Service]\nDescription=no command\n",
        "[Service]\n\u00a0ExecStart=/usr/bin/true\n",
        "[Unit]\nExecStart=/usr/bin/true\n[Service]\nDescription=no command\n",
    ),
)
def test_普通service完整有效证明拒绝伪ExecStart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str,
) -> None:
    """主载荷解析必须与 systemd 语义一致，不能借未知 drop-in 假闭环。"""
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-web.service", content.encode())
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=_属性输出(
                "/etc/systemd/system/codev-web.service",
                dropins="/etc/systemd/system/codev-web.service.d/50-unknown.conf",
                command="/usr/bin/true",
            ),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError):
        module.default_effective_unit_payload_verifier((payload,))


def test_任意层有效DropInPaths仍含旧shadow时失败(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-web.service", _服务内容())
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=_属性输出(
                "/etc/systemd/system/codev-web.service",
                dropins=("/usr/local/lib/systemd/system/codev-web.service.d/90-codev-release.conf"),
            ),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="drop-in"):
        module.default_effective_unit_payload_verifier((payload,))


@pytest.mark.parametrize(
    "stdout",
    (
        "DropInPaths=\nExecStart={ path=/release/bin/python ; argv[]=/release/bin/python ; }\n",
        "FragmentPath=/etc/systemd/system/codev-web.service\nExecStart={ path=/release/bin/python ; argv[]=/release/bin/python ; }\n",
        "FragmentPath=/etc/systemd/system/codev-web.service\nDropInPaths=\n",
        "",
    ),
)
def test_带键属性缺失时失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stdout: str,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-web.service", _服务内容())
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(returncode=0, stdout=stdout),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="属性不完整"):
        module.default_effective_unit_payload_verifier((payload,))


def test_非运行时时钟timer要求FragmentPath与空dropin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    content = b"[Timer]\nOnBootSec=1min\n"
    payload = _载荷(module, tmp_path, "codev-clock-resync.timer", content)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda command: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout=(
                    "FragmentPath=/etc/systemd/system/codev-clock-resync.timer\nDropInPaths=\n"
                ),
            )
        ),
    )

    module.default_effective_unit_payload_verifier((payload,))

    assert "--property=ExecStart" not in commands[0]
    assert "--value" not in commands[0]


def test_部署守卫成员的非运行时timer要求唯一部署guard(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.runtime_systemd_gate_contract import (
        DEPLOYMENT_GUARD_DROP_IN_CONTENT,
        deployment_guard_drop_in_path,
    )

    content = b"[Timer]\nOnBootSec=1min\n"
    unit = "codev-memory-maintenance.timer"
    payload = _载荷(module, tmp_path, unit, content)
    guard_path = deployment_guard_drop_in_path(unit)
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda command: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout=(
                    f"FragmentPath=/etc/systemd/system/{unit}\n"
                    f"DropInPaths={guard_path.as_posix()}\n"
                ),
            )
        ),
    )
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda path: (
            _目标原像(module, DEPLOYMENT_GUARD_DROP_IN_CONTENT) if path == guard_path else None
        ),
    )

    module.default_effective_unit_payload_verifier((payload,))

    assert "--property=ExecStart" not in commands[0]


@pytest.mark.parametrize(
    "content",
    (
        b"[Service]\nExecStart=\n",
        b"[Service]\nExecStart=/one\nExecStart=/two\n",
        b"[Service]\nExecStart='/unterminated\n",
        b"[Service]\nDescription=no command\n",
    ),
)
def test_service载荷ExecStart为空多个缺失或无法解析时失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: bytes,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-web.service", content)
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, content),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="ExecStart"):
        module.default_effective_unit_payload_verifier((payload,))


def test_有效载荷证明严格使用带键属性且接入默认端口(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(module, tmp_path, "codev-web.service", _服务内容())
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda command: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout=_属性输出("/etc/systemd/system/codev-web.service"),
            )
        ),
    )

    module.default_effective_unit_payload_verifier((payload,))

    assert module.default_ports().verify_effective_unit_payloads is (
        module.default_effective_unit_payload_verifier
    )
    assert set(commands[0][3:]) == {
        "--property=FragmentPath",
        "--property=DropInPaths",
        "--property=ExecStart",
    }
    assert "--value" not in commands[0]


def test_非运行时时钟unit以原像路径和空dropin证明而不解析ExecStart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    command = "/bin/sh -c '/sbin/hwclock --hctosys || hwclock -s || true'"
    payload = _载荷(
        module,
        tmp_path,
        "codev-clock-resync.service",
        _服务内容(command),
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda argv: (
            calls.append(argv),
            SimpleNamespace(
                returncode=0,
                stdout=_属性输出(
                    "/etc/systemd/system/codev-clock-resync.service",
                    command=None,
                ),
            ),
        )[1],
    )

    module.default_effective_unit_payload_verifier((payload,))

    assert "--property=ExecStart" not in calls[0]


def test_非运行时时钟unit拒绝任何有效dropin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module

    payload = _载荷(
        module,
        tmp_path,
        "codev-clock-resync.service",
        _服务内容("/bin/sh -c 'true'"),
    )
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _argv: SimpleNamespace(
            returncode=0,
            stdout=_属性输出(
                "/etc/systemd/system/codev-clock-resync.service",
                dropins="/etc/systemd/system/codev-clock-resync.service.d/50-foreign.conf",
                command=None,
            ),
        ),
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="有效 drop-in 路径集合"):
        module.default_effective_unit_payload_verifier((payload,))


@pytest.mark.parametrize("case", ("missing", "altered"))
def test_部署守卫成员的非运行时timer拒绝缺失或篡改部署guard(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform import mcp_systemd_install_systemd as module
    from codev_platform.runtime_systemd_gate_contract import (
        DEPLOYMENT_GUARD_DROP_IN_CONTENT,
        deployment_guard_drop_in_path,
    )

    payload = _载荷(
        module,
        tmp_path,
        "codev-memory-maintenance.timer",
        b"[Timer]\nOnBootSec=1min\n",
    )
    guard_path = deployment_guard_drop_in_path("codev-memory-maintenance.timer")
    dropins = "" if case == "missing" else guard_path.as_posix()
    guard_content = (
        DEPLOYMENT_GUARD_DROP_IN_CONTENT
        if case == "missing"
        else b"[Unit]\nConditionPathExists=/unsafe\n"
    )
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: _目标原像(module, payload.content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _argv: SimpleNamespace(
            returncode=0,
            stdout=_属性输出(
                "/etc/systemd/system/codev-memory-maintenance.timer",
                dropins=dropins,
                command=None,
            ),
        ),
    )
    monkeypatch.setattr(
        module,
        "default_drop_in_snapshot_reader",
        lambda path: _目标原像(module, guard_content) if path == guard_path else None,
    )

    with pytest.raises(module.SystemdInstallTransactionError, match="(路径集合|原像)"):
        module.default_effective_unit_payload_verifier((payload,))
