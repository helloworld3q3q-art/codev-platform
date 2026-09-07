"""CodeGraph 主 unit 布局迁移的目标解释器绑定测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdUnitActivationMode,
    SystemdUnitInstallSpec,
    SystemdUnitPayload,
)
from codev_platform.mcp_systemd_install_input import VerifiedInstallInput
from codev_platform.ops.systemd_unit_layout_transaction import (
    SystemdUnitLayoutMigrationError,
    select_fixed_payloads,
)


def _安装输入(
    tmp_path: Path,
    python: str,
    *,
    isolated: bool = True,
) -> VerifiedInstallInput:
    isolated_flag = "-I " if isolated else ""
    content = (
        f"[Service]\nExecStart={python} {isolated_flag}-m codev_platform.codegraph.server "
        "--http --port 19091\n"
    )
    return _安装输入内容(tmp_path, content)


def _安装输入内容(tmp_path: Path, content: str) -> VerifiedInstallInput:
    encoded = content.encode()
    spec = SystemdUnitInstallSpec(
        source=(tmp_path / "codev-mcp-codegraph.service").resolve(),
        content_digest=hashlib.sha256(encoded).hexdigest(),
        enable=True,
        restart=True,
        activation_mode=SystemdUnitActivationMode.CODEGRAPH_STATE_MACHINE,
    )
    payload = SystemdUnitPayload(spec=spec, content=encoded)
    return VerifiedInstallInput(
        manifest=SystemdInstallManifest((spec,), runtime_revision="1" * 40),
        payloads=(payload,),
    )


def test_选择固定载荷时拒绝其他release的CodeGraph解释器(tmp_path: Path) -> None:
    install_input = _安装输入(tmp_path, "/old-release/.venv/bin/python")

    with pytest.raises(SystemdUnitLayoutMigrationError, match="目标解释器"):
        select_fixed_payloads(
            install_input,
            expected_python="/target-release/.venv/bin/python",
        )


def test_选择固定载荷时接受目标release的CodeGraph解释器(tmp_path: Path) -> None:
    target_python = "/target-release/.venv/bin/python"

    payload = select_fixed_payloads(
        _安装输入(tmp_path, target_python),
        expected_python=target_python,
    )

    assert payload.spec.unit_name == "codev-mcp-codegraph.service"


def test_选择固定载荷时拒绝缺少隔离模式的CodeGraph启动命令(tmp_path: Path) -> None:
    target_python = "/target-release/.venv/bin/python"

    with pytest.raises(SystemdUnitLayoutMigrationError, match="模块或参数"):
        select_fixed_payloads(
            _安装输入(tmp_path, target_python, isolated=False),
            expected_python=target_python,
        )


@pytest.mark.parametrize(
    "content",
    [
        (
            "[Service]\n"
            "ExecStart=/target-release/.venv/bin/python -I -m "
            "codev_platform.codegraph.server --http --port 0\n"
        ),
        (
            "[Service]\n"
            "ExecStart=/target-release/.venv/bin/python -I -m "
            "codev_platform.codegraph.server --http --port 19091\n"
            "ExecStart=/target-release/.venv/bin/python -I -m "
            "codev_platform.codegraph.server --http --port 19091\n"
        ),
        (
            "[Service]\n"
            "ExecStart=/target-release/.venv/bin/python -I -m "
            "codev_platform.codegraph.server --http --port 19091 --extra\n"
        ),
    ],
)
def test_选择固定载荷时拒绝非唯一或非规范启动命令(tmp_path: Path, content: str) -> None:
    """任何不精确的 CodeGraph 载荷都不能进入布局迁移。"""
    with pytest.raises(SystemdUnitLayoutMigrationError):
        select_fixed_payloads(
            _安装输入内容(tmp_path, content),
            expected_python="/target-release/.venv/bin/python",
        )
