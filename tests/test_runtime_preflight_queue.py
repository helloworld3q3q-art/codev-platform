"""运行时 File queue 聚合操作预检测试。"""

from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from codev_platform import runtime_preflight_queue as queue_preflight
from codev_platform.runtime_preflight_contract import ProbeDeadline


_PHASES = ("pending", "active", "results", "quarantined", "locks")


def _queue_root(tmp_path: Path) -> Path:
    root = tmp_path / "reindex_queue"
    for phase in _PHASES:
        (root / phase).mkdir(parents=True, exist_ok=True)
    return root


def _deadline() -> ProbeDeadline:
    return ProbeDeadline.after(5.0, time.monotonic)


def _sentinels(root: Path) -> tuple[Path, ...]:
    return tuple(root.rglob(".codev-preflight-*"))


def test_file_queue_operational_probe_exercises_all_phases_and_cleans(tmp_path: Path) -> None:
    root = _queue_root(tmp_path)

    queue_preflight.probe_file_queue_operational(root, _deadline())

    assert _sentinels(root) == ()


@pytest.mark.parametrize("phase", _PHASES)
def test_file_queue_operational_probe_rejects_missing_phase(
    tmp_path: Path,
    phase: str,
) -> None:
    root = _queue_root(tmp_path)
    (root / phase).rmdir()

    with pytest.raises(OSError):
        queue_preflight.probe_file_queue_operational(root, _deadline())

    assert _sentinels(root) == ()


def test_file_queue_operational_probe_rejects_phase_symlink(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows 普通用户不保证可创建符号链接")
    root = _queue_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "active").rmdir()
    (root / "active").symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError):
        queue_preflight.probe_file_queue_operational(root, _deadline())

    assert _sentinels(outside) == ()


def test_file_queue_replace_failure_cleans_every_owned_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _queue_root(tmp_path)
    monkeypatch.setattr(
        queue_preflight.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("注入替换失败")),
    )

    with pytest.raises(OSError, match="注入替换失败"):
        queue_preflight.probe_file_queue_operational(root, _deadline())

    assert _sentinels(root) == ()


def test_file_queue_lock_failure_cleans_every_owned_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _queue_root(tmp_path)
    monkeypatch.setattr(
        queue_preflight,
        "_probe_queue_lock",
        lambda *_args: (_ for _ in ()).throw(OSError("注入锁失败")),
    )

    with pytest.raises(OSError, match="注入锁失败"):
        queue_preflight.probe_file_queue_operational(root, _deadline())

    assert _sentinels(root) == ()
