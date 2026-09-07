"""systemd 主 unit 布局失败证据的重试门禁测试。"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_mcp_systemd_unit_layout_migration import _安装输入, _模拟端口, _绑定路径


def test_上次回滚残留隔离叶子时拒绝再次迁移(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """固定 rollback 名存在时，重试不得覆盖第一次失败留下的审计证据。"""
    from codev_platform import mcp_systemd_unit_layout_migration as module

    ports = _模拟端口(module, tmp_path)
    _绑定路径(monkeypatch, module, ports)
    original_systemctl = ports.执行systemctl
    failed_once = False

    def 首次重接失败(command: tuple[str, ...]) -> None:
        nonlocal failed_once
        if command == ("systemctl", "reenable", "codev-mcp-codegraph.service") and not failed_once:
            failed_once = True
            raise RuntimeError("首次重接失败")
        original_systemctl(command)

    migration_ports = ports.构造()
    object.__setattr__(migration_ports, "systemctl", 首次重接失败)
    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="已回滚"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path), ports=migration_ports,
            platform_name="linux", effective_user_id=lambda: 0,
        )
    before = list(ports.events)

    with pytest.raises(module.SystemdUnitLayoutMigrationError, match="隔离"):
        module.migrate_verified_input(
            _安装输入(module, tmp_path), ports=migration_ports,
            platform_name="linux", effective_user_id=lambda: 0,
        )

    assert ports.events == before
