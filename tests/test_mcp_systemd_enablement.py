"""受管 systemd 持久启用链接证明测试。"""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest


def _root_link_metadata() -> SimpleNamespace:
    return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0, st_gid=0)


def test_CodeGraph启用态绑定multi_user目标和canonical主unit() -> None:
    from codev_platform.mcp_systemd_enablement import read_persistent_unit_enablement_state

    links: list[Path] = []

    def read_link(path: Path) -> str:
        links.append(path)
        return "/usr/local/lib/systemd/system/codev-mcp-codegraph.service"

    assert (
        read_persistent_unit_enablement_state(
            "codev-mcp-codegraph.service",
            lstat=lambda _path: _root_link_metadata(),
            readlink=read_link,
        )
        == "enabled"
    )
    assert links == [
        Path("/etc/systemd/system/multi-user.target.wants/codev-mcp-codegraph.service")
    ]


def test_timer启用态只接受timers目标链接() -> None:
    from codev_platform.mcp_systemd_enablement import read_persistent_unit_enablement_state

    links: list[Path] = []

    assert (
        read_persistent_unit_enablement_state(
            "codev-clock-resync.timer",
            lstat=lambda path: links.append(path) or _root_link_metadata(),
            readlink=lambda _path: "/etc/systemd/system/codev-clock-resync.timer",
        )
        == "enabled"
    )
    assert links == [Path("/etc/systemd/system/timers.target.wants/codev-clock-resync.timer")]


def test_持久启用链接不存在时明确返回disabled() -> None:
    from codev_platform.mcp_systemd_enablement import read_persistent_unit_enablement_state

    def missing(_path: Path):
        raise FileNotFoundError

    assert (
        read_persistent_unit_enablement_state(
            "codev-webhook.service",
            lstat=missing,
            readlink=lambda _path: pytest.fail("缺失链接不得继续读取"),
        )
        == "disabled"
    )


@pytest.mark.parametrize(
    ("metadata", "target"),
    [
        (SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_gid=0), ""),
        (_root_link_metadata(), "/etc/systemd/system/other.service"),
        (SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=1000, st_gid=0), ""),
    ],
)
def test_持久启用链接类型属主或目标不精确时失败关闭(metadata, target: str) -> None:
    from codev_platform.mcp_systemd_enablement import (
        ManagedSystemdEnablementError,
        read_persistent_unit_enablement_state,
    )

    with pytest.raises(ManagedSystemdEnablementError, match="启用链接"):
        read_persistent_unit_enablement_state(
            "codev-webhook.service",
            lstat=lambda _path: metadata,
            readlink=lambda _path: target,
        )
