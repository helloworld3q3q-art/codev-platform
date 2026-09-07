"""systemd 安装事务的命令行参数与用户输出适配器。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallPorts,
    SystemdInstallTransactionError,
)


def run_install_cli(
    argv: Sequence[str] | None = None,
    *,
    ports: SystemdInstallPorts | None = None,
    platform_name: str | None = None,
    effective_user_id: Callable[[], int] | None = None,
) -> int:
    """解析互斥安装模式并把领域错误转换为稳定退出码。"""
    from codev_platform import mcp_systemd_install_transaction as transaction

    parser = argparse.ArgumentParser(description="在维护独占锁内安装受限 systemd manifest")
    parser.add_argument("--manifest", required=True, type=Path, help="绝对路径 JSON manifest")
    parser.add_argument(
        "--maintenance-stage",
        action="store_true",
        help="仅在已证明维护窗口中安装载荷，延迟激活写服务",
    )
    parser.add_argument(
        "--install-only",
        action="store_true",
        help="只安装并 enable unit，不启动、停止或重启服务",
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.maintenance_stage and args.install_only:
        print("错误: systemd 安装模式互斥", file=sys.stderr)
        return 2
    installer = transaction.install_systemd_from_manifest_path
    if args.install_only:
        installer = transaction.install_systemd_install_only_from_manifest_path
    elif args.maintenance_stage:
        installer = transaction.install_systemd_maintenance_stage_from_manifest_path
    try:
        report = installer(
            args.manifest,
            ports=ports,
            platform_name=platform_name,
            effective_user_id=effective_user_id,
        )
    except SystemdInstallTransactionError as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2
    if report.deferred_activation_units:
        units = ", ".join(report.deferred_activation_units)
        print(f"维护态载荷安装完成；以下服务未由本事务重启: {units}")
        print("请继续完成 owner、索引 manifest 与恢复状态机证明，禁止手工解除 mask。")
        return 0
    print("全量 systemd 安装事务完成")
    return 0


__all__ = ["run_install_cli"]
