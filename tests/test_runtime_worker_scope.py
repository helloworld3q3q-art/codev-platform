"""root-fd worker 的 user systemd scope 命令与 cgroup 身份契约。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from codev_platform.core.systemd_process_identity import cgroup_belongs_to_unit
from codev_platform.runtime_worker_scope import (
    RuntimeWorkerScopeError,
    build_worker_scope_command,
    new_worker_scope_unit,
)


def test用户瞬时unit归属仅接受精确service片段() -> None:
    """user.slice 路径不能再套用 system.slice 的固定前缀。"""
    unit = "codev-rootfd-0123456789ab.service"
    raw = (
        "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
        "codev-rootfd-0123456789ab.service\n"
    ).encode("ascii")

    assert cgroup_belongs_to_unit(raw, unit)
    assert not cgroup_belongs_to_unit(
        b"0::/user.slice/user-1000.slice/user@1000.service/app.slice/other.service\n",
        unit,
    )
    assert not cgroup_belongs_to_unit(
        b"0::/user.slice/codev-rootfd-0123456789ab.service-copy.service\n",
        unit,
    )


def test随机scope单元名受限且每次不同() -> None:
    """随机 transient unit 不得复用稳定名字或越出 service 命名边界。"""
    first = new_worker_scope_unit()
    second = new_worker_scope_unit()

    assert first != second
    assert first.startswith("codev-rootfd-")
    assert first.endswith(".service")
    assert len(first) <= 63


def testscope命令固定user服务cgroup与隔离启动门(tmp_path: Path) -> None:
    """父端不能把 root fd、路径或非固定解释器参数暴露给 systemd 命令行。"""
    bootstrap = tmp_path / "runtime_bound_worker_bootstrap.py"
    bootstrap.write_text("# fixture", encoding="utf-8")
    command = build_worker_scope_command(
        unit="codev-rootfd-0123456789ab.service",
        handoff_socket="/tmp/codev-rootfd-test/handoff.sock",
        operation="root-identity",
        timeout_sec=12.1,
        bootstrap_path=bootstrap,
        interpreter=sys.executable,
        environment={"HOME": "/tmp/home", "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
    )

    assert command[0] == "/usr/bin/systemd-run"
    assert "--user" in command
    assert "--wait" in command
    assert "--pipe" in command
    assert "--collect" in command
    assert "--property=KillMode=control-group" in command
    assert "--property=ExitType=main" in command
    assert "--property=KillSignal=SIGKILL" in command
    assert "--property=Restart=no" in command
    assert "--property=RemainAfterExit=no" in command
    assert "--property=NoNewPrivileges=yes" in command
    assert "--property=ProtectControlGroups=yes" in command
    assert "--property=RuntimeMaxSec=13s" in command
    assert "--unit=codev-rootfd-0123456789ab.service" in command
    assert "--root-fd" not in command
    assert "--parent-liveness-fd" not in command
    assert "--parent-pidfd" not in command
    assert "--bootstrap-pidfd" not in command
    marker = command.index("--")
    assert command[marker + 1 : marker + 4] == ("/usr/bin/env", "-i", "HOME=/tmp/home")
    interpreter_index = command.index(sys.executable)
    assert command[interpreter_index : interpreter_index + 4] == (
        sys.executable,
        "-B",
        "-I",
        str(bootstrap),
    )
    assert str(bootstrap) in command
    assert "--handoff-socket" in command
    assert "--expected-unit" in command
    assert "--operation" in command


@pytest.mark.parametrize(
    ("unit", "socket_path", "operation", "timeout_sec"),
    (
        ("codev-rootfd-invalid.scope", "/tmp/handoff.sock", "root-identity", 1.0),
        ("codev-rootfd-valid.service", "relative.sock", "root-identity", 1.0),
        ("codev-rootfd-valid.service", "/tmp/handoff.sock", "", 1.0),
        ("codev-rootfd-valid.service", "/tmp/handoff.sock", "root-identity", 0.0),
    ),
)
def testscope命令拒绝不安全输入(
    tmp_path: Path,
    unit: str,
    socket_path: str,
    operation: str,
    timeout_sec: float,
) -> None:
    bootstrap = tmp_path / "runtime_bound_worker_bootstrap.py"
    bootstrap.write_text("# fixture", encoding="utf-8")

    with pytest.raises(RuntimeWorkerScopeError):
        build_worker_scope_command(
            unit=unit,
            handoff_socket=socket_path,
            operation=operation,
            timeout_sec=timeout_sec,
            bootstrap_path=bootstrap,
            interpreter=sys.executable,
            environment={"PATH": "/usr/bin:/bin"},
        )
