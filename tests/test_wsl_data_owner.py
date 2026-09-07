"""Windows 配置指向 WSL UNC data root 时的数据 owner 解析。"""

from __future__ import annotations

import pytest

from codev_platform.core.wsl_data_owner import WslDataOwner, wsl_data_owner


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (
            r"\\wsl.localhost\Ubuntu\home\demo\codev-platform\data",
            WslDataOwner("Ubuntu", "/home/demo/codev-platform/data"),
        ),
        (
            r"//wsl$/Ubuntu/home/demo/codev-platform/data",
            WslDataOwner("Ubuntu", "/home/demo/codev-platform/data"),
        ),
        (
            r"\\?\UNC\wsl.localhost\Ubuntu\home\demo\codev-platform\data",
            WslDataOwner("Ubuntu", "/home/demo/codev-platform/data"),
        ),
        (
            "\\\\wsl.localhost\\Ubuntu\\home\\demo\\codev-platform\\data\\",
            WslDataOwner("Ubuntu", "/home/demo/codev-platform/data"),
        ),
    ],
)
def test_windows_wsl_unc_data_root_resolves_owner(configured, expected):
    cfg = {"data": {"platform_data_dir": configured}}

    assert wsl_data_owner(cfg, platform_name="nt", environment={}) == expected


def test_environment_data_root_has_priority_over_config():
    cfg = {"data": {"platform_data_dir": r"D:\data"}}

    assert wsl_data_owner(
        cfg,
        platform_name="nt",
        environment={
            "PLATFORM_DATA_DIR": r"\\wsl.localhost\Ubuntu\srv\codev\data",
        },
    ) == WslDataOwner("Ubuntu", "/srv/codev/data")


@pytest.mark.parametrize(
    ("platform_name", "configured"),
    [
        ("posix", "/home/demo/data"),
        ("nt", r"D:\data"),
        ("nt", r"\\server\share\data"),
    ],
)
def test_non_wsl_owned_data_has_no_wsl_owner(platform_name, configured):
    assert wsl_data_owner(
        {"data": {"platform_data_dir": configured}},
        platform_name=platform_name,
        environment={},
    ) is None
