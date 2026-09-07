"""systemd 有效载荷测试的共享构造器。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def 载荷(module, tmp_path: Path, name: str, content: bytes):
    from codev_platform.mcp_systemd_install_contract import (
        SystemdUnitActivationMode,
        SystemdUnitPayload,
    )

    spec = module.SystemdUnitInstallSpec(
        source=(tmp_path / name).resolve(),
        content_digest=hashlib.sha256(content).hexdigest(),
        enable=True,
        restart=True,
        activation_mode=(
            SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE
            if name == "codev-mcp-codegraph.service"
            else SystemdUnitActivationMode.REINDEX_STATE_MACHINE
            if name == "codev-reindex.service"
            else SystemdUnitActivationMode.STANDARD
        ),
    )
    return SystemdUnitPayload(spec=spec, content=content)


def 目标原像(
    module,
    content: bytes,
    *,
    mode: int = 0o644,
    uid: int = 0,
    gid: int = 0,
):
    return module.SystemdUnitFileSnapshot(
        content=content,
        mode=mode,
        uid=uid,
        gid=gid,
    )


def 服务内容(command: str = "/release/bin/python -I -m codev_platform.cli web serve") -> bytes:
    return f"[Service]\nExecStart={command}\n".encode()


def codegraph服务内容() -> bytes:
    return (
        b"[Unit]\n"
        b"ConditionPathExists=!/var/lib/codev-platform/codegraph-maintenance.gate\n"
        b"[Service]\n"
        b"ExecStart=/release/bin/python -I -m codev_platform.codegraph.server "
        b"--http --port 18091\n"
    )


def 属性输出(
    fragment: str,
    *,
    dropins: str = "",
    command: str | None = "/release/bin/python -I -m codev_platform.cli web serve",
) -> str:
    lines = [f"FragmentPath={fragment}", f"DropInPaths={dropins}"]
    if command is not None:
        executable = command.split(maxsplit=1)[0]
        lines.append(f"ExecStart={{ path={executable} ; argv[]={command} ; ignore_errors=no ; }}")
    return "\n".join(lines) + "\n"


def 配置codegraph_stage有效证明(
    module,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    dropins: str | None = None,
):
    from codev_platform.core.systemd_maintenance_contract import (
        CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
        CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
    )
    from codev_platform.ops.reindex_codegraph_resume_contract import (
        CODEGRAPH_RESUME_DROPIN,
    )
    from codev_platform.runtime_systemd_gate_contract import (
        DEPLOYMENT_GUARD_DROP_IN_CONTENT,
        deployment_guard_drop_in_path,
    )

    content = codegraph服务内容()
    command = "/release/bin/python -I -m codev_platform.codegraph.server --http --port 18091"
    expected_dropin = (
        b"[Service]\nEnvironmentFile=\n"
        b"EnvironmentFile=/etc/codev-platform/reindex-codegraph-resume.env\n"
    )
    deployment_path = deployment_guard_drop_in_path("codev-mcp-codegraph.service")
    payload = 载荷(module, tmp_path, "codev-mcp-codegraph.service", content)
    monkeypatch.setattr(
        module,
        "default_unit_snapshot_reader",
        lambda _name: 目标原像(module, content),
    )
    monkeypatch.setattr(
        module,
        "_run_systemctl_command",
        lambda _command: SimpleNamespace(
            returncode=0,
            stdout=属性输出(
                "/usr/local/lib/systemd/system/codev-mcp-codegraph.service",
                dropins=(
                    f"{deployment_path.as_posix()} {CODEGRAPH_RESUME_DROPIN.as_posix()} "
                    f"{CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix()}"
                    if dropins is None
                    else dropins
                ),
                command=command,
            ),
        ),
    )
    return (
        payload,
        expected_dropin,
        deployment_path,
        DEPLOYMENT_GUARD_DROP_IN_CONTENT,
        CODEGRAPH_RESUME_DROPIN,
        CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
        CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
    )
