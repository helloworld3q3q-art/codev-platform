"""CodeGraph systemd 主 unit 布局迁移的兼容入口。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codev_platform.ops.systemd_unit_layout_contract import (
    SystemdUnitLayoutMigrationError,
    SystemdUnitRuntime,
    UnitLayoutMigrationPorts,
)
from codev_platform.ops.systemd_unit_layout_systemd import default_ports
from codev_platform.ops.systemd_unit_layout_transaction import (
    migrate_from_manifest,
    migrate_verified_input,
    select_fixed_payloads,
)


def main(argv: list[str] | None = None) -> int:
    """救援入口；常规操作应使用 reindex-maintenance 的显式确认命令。"""
    parser = argparse.ArgumentParser(description="迁移 CodeGraph systemd 主 unit 到 canonical 目录")
    parser.add_argument("--manifest", required=True, type=Path, help="当前 release 生成的绝对安装 manifest")
    parser.add_argument("--yes", action="store_true", help="确认执行 root 级无启停布局迁移")
    args = parser.parse_args(argv)
    if args.yes is not True:
        print("演练：会迁移 CodeGraph 主 unit；确认后请添加 --yes")
        return 0
    try:
        migrate_from_manifest(args.manifest)
    except MemoryError:
        raise
    except Exception as error:
        message = error if isinstance(error, SystemdUnitLayoutMigrationError) else None
        print(f"FATAL: {message or 'systemd 主 unit 布局迁移失败；安全状态未证明'}", file=sys.stderr)
        return 1
    print("CodeGraph systemd 主 unit 布局迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SystemdUnitLayoutMigrationError",
    "SystemdUnitRuntime",
    "UnitLayoutMigrationPorts",
    "default_ports",
    "main",
    "migrate_from_manifest",
    "migrate_verified_input",
    "select_fixed_payloads",
]
