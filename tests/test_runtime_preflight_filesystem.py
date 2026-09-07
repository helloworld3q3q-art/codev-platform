"""运行时目录权限探针的语义边界测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codev_platform import runtime_preflight_filesystem as filesystem


def test_仅穿越探针不要求列举父命名空间(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0710 服务组命名空间可穿越，但不能被误判为不可用。"""
    observed: list[int] = []

    def access(_path: Path, mode: int) -> bool:
        observed.append(mode)
        return mode == os.X_OK

    monkeypatch.setattr(filesystem.os, "access", access)

    filesystem.probe_directory_traverse(tmp_path)

    with pytest.raises(OSError, match="不可读"):
        filesystem.probe_directory_rx(tmp_path)

    assert observed == [os.X_OK, os.R_OK | os.X_OK]


def test_仅穿越探针仍拒绝缺少执行权限的目录(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem.os, "access", lambda _path, _mode: False)

    with pytest.raises(OSError, match="不可遍历"):
        filesystem.probe_directory_traverse(tmp_path)
