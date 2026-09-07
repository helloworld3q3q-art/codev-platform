"""systemd unit 实际解析与 runtime mask 分类测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _结果(stdout: str, *, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="不得泄露 systemctl 原始输出")


def test_只有三字段全部精确匹配才是有效runtime_mask() -> None:
    from codev_platform.core import systemd_unit_resolution as module

    resolution = module.read_unit_resolution(
        "codev-mcp-codegraph.service",
        command_runner=lambda _command, **_kwargs: _结果(
            "LoadState=masked\n"
            "UnitFileState=masked-runtime\n"
            "FragmentPath=/run/systemd/system/codev-mcp-codegraph.service\n"
        ),
    )

    assert module.classify_runtime_mask(
        resolution,
        runtime_mask_target="/dev/null",
    ) is module.RuntimeMaskState.EFFECTIVE_RUNTIME_MASK


def test_run残留但systemd仍解析legacy主unit属于不一致状态() -> None:
    from codev_platform.core import systemd_unit_resolution as module

    resolution = module.SystemdUnitResolution(
        unit_name="codev-mcp-codegraph.service",
        load_state="loaded",
        unit_file_state="enabled",
        fragment_path="/etc/systemd/system/codev-mcp-codegraph.service",
    )

    assert module.classify_runtime_mask(
        resolution,
        runtime_mask_target="/dev/null",
    ) is module.RuntimeMaskState.INCONSISTENT_RUNTIME_ARTIFACT


def test_尚未安装的unit在没有run残留时可证明为未mask() -> None:
    from codev_platform.core import systemd_unit_resolution as module

    resolution = module.read_unit_resolution(
        "codev-mcp-codegraph.service",
        command_runner=lambda _command, **_kwargs: _结果(
            "LoadState=not-found\nUnitFileState=not-found\nFragmentPath=\n"
        ),
    )

    assert module.classify_runtime_mask(
        resolution,
        runtime_mask_target=None,
    ) is module.RuntimeMaskState.UNMASKED


@pytest.mark.parametrize(
    "stdout",
    [
        "LoadState=masked\nUnitFileState=masked-runtime\n",
        "LoadState=masked\nUnitFileState=masked-runtime\nUnitFileState=masked-runtime\nFragmentPath=/run/systemd/system/codev-mcp-codegraph.service\n",
    ],
)
def test_字段缺失或重复时拒绝把状态当作已证明(stdout: str) -> None:
    from codev_platform.core import systemd_unit_resolution as module

    with pytest.raises(module.SystemdUnitResolutionError):
        module.read_unit_resolution(
            "codev-mcp-codegraph.service",
            command_runner=lambda _command, **_kwargs: _结果(stdout),
        )
