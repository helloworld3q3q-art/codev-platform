"""固定 CodeGraph 主 unit 载荷的运行时绑定校验。"""
from __future__ import annotations

import shlex
from pathlib import Path
from pathlib import PurePosixPath

from codev_platform.mcp_systemd_install_contract import SystemdUnitPayload
from codev_platform.mcp_systemd_install_input import VerifiedInstallInput
from codev_platform.ops.systemd_unit_layout_contract import _CODEGRAPH_UNIT
from codev_platform.ops.systemd_unit_layout_contract import SystemdUnitLayoutMigrationError


_CODEGRAPH_MODULE = "codev_platform.codegraph.server"


def select_fixed_codegraph_payload(
    install_input: VerifiedInstallInput,
    *,
    expected_python: str | Path | None = None,
) -> SystemdUnitPayload:
    """选择并校验 manifest 中唯一可迁移的固定 CodeGraph 主 unit。"""
    if not isinstance(install_input, VerifiedInstallInput):
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移输入无效")
    selected = tuple(
        payload
        for payload in install_input.payloads
        if payload.spec.unit_name == _CODEGRAPH_UNIT
    )
    if len(selected) != 1:
        raise SystemdUnitLayoutMigrationError("迁移输入必须包含唯一固定 CodeGraph unit")
    payload = selected[0]
    if payload.spec not in install_input.manifest.units:
        raise SystemdUnitLayoutMigrationError("systemd 布局迁移输入与清单不一致")
    if expected_python is not None:
        require_codegraph_payload_python(payload, expected_python)
    return payload


def require_codegraph_payload_python(
    payload: SystemdUnitPayload,
    expected_python: str | Path,
) -> None:
    """拒绝解释器、模块或端口格式不能绑定当前 release 的固定 CodeGraph 载荷。"""
    if not isinstance(payload, SystemdUnitPayload):
        raise SystemdUnitLayoutMigrationError("CodeGraph unit 载荷无效")
    expected = _require_expected_python(expected_python)
    command = _single_execstart(payload.content)
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as error:
        raise SystemdUnitLayoutMigrationError("CodeGraph unit ExecStart 格式无效") from error
    if not argv or argv[0] != str(expected):
        raise SystemdUnitLayoutMigrationError("CodeGraph unit 目标解释器不匹配")
    if len(argv) != 7 or argv[1:6] != ["-I", "-m", _CODEGRAPH_MODULE, "--http", "--port"]:
        raise SystemdUnitLayoutMigrationError("CodeGraph unit ExecStart 模块或参数无效")
    try:
        port = int(argv[6])
    except ValueError as error:
        raise SystemdUnitLayoutMigrationError("CodeGraph unit ExecStart 端口无效") from error
    if not argv[6].isdecimal() or not 1 <= port <= 65535:
        raise SystemdUnitLayoutMigrationError("CodeGraph unit ExecStart 端口无效")


def _require_expected_python(value: str | Path) -> str:
    raw = str(value) if isinstance(value, Path) else value
    path = PurePosixPath(raw) if isinstance(raw, str) else None
    if (
        type(raw) is not str
        or "\x00" in raw
        or not raw.startswith("/")
        or not path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise SystemdUnitLayoutMigrationError("CodeGraph 目标解释器无效")
    return raw


def _single_execstart(content: bytes) -> str:
    try:
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise SystemdUnitLayoutMigrationError("CodeGraph unit 内容无法解码") from error
    commands = [line.removeprefix("ExecStart=") for line in lines if line.startswith("ExecStart=")]
    commands = [command for command in commands if command]
    if len(commands) != 1:
        raise SystemdUnitLayoutMigrationError("CodeGraph unit 必须包含唯一 ExecStart")
    return commands[0]


__all__ = ["require_codegraph_payload_python", "select_fixed_codegraph_payload"]
