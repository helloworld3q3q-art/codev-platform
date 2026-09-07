"""root-fd worker 内对象访问定型的当前目录相对路径回归。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from codev_platform.runtime_bound_worker_object_access import (
    seal_object_access_from_cwd,
    verify_object_access_from_cwd,
)


_LINUX_ROOT = sys.platform.startswith("linux") and os.geteuid() == 0


@pytest.mark.skipif(not _LINUX_ROOT, reason="fd-relative 对象访问要求 Linux root")
def testworker对象访问支持当前目录下的安全相对路径(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "object"
    root.mkdir(mode=0o700)
    marker = root / ".incomplete"
    marker.write_bytes(b"after_install\n")
    os.chmod(marker, 0o600)
    monkeypatch.chdir(tmp_path)

    seal_object_access_from_cwd(Path("object"))
    verify_object_access_from_cwd(Path("object"))

    assert (root.stat().st_mode & 0o777) == 0o750
    assert (marker.stat().st_mode & 0o777) == 0o640
