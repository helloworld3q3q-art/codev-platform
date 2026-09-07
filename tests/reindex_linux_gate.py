"""Linux reindex 普通行为测试的临时全局门禁准备。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def provision_test_gate(monkeypatch, tmp_path: Path) -> None:
    """把默认全局门禁隔离到临时目录，避免普通测试误触真实 WSL 运行态。"""
    if not sys.platform.startswith("linux"):
        return
    from codev_platform.reindex import maintenance_gate

    root = tmp_path / "global-gate-root"
    root.mkdir()
    root.chmod(0o755)
    marker = root / "codev-platform" / "reindex-maintenance.gate"
    effective_uid = os.geteuid()
    monkeypatch.setattr(maintenance_gate, "_GLOBAL_GATE_ROOT", root)
    monkeypatch.setattr(maintenance_gate, "_DEFAULT_MARKER_PATH", marker)
    monkeypatch.setattr(maintenance_gate, "_global_owner_uid", lambda: effective_uid)
    monkeypatch.setattr(maintenance_gate, "_effective_uid", lambda: effective_uid)
    maintenance_gate.provision_maintenance_gate()
