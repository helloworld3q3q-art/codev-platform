"""reindex 维护门禁测试的共享端口构造与临时路径绑定。"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import pytest


@contextmanager
def _acquired_start_lock():
    yield True


def _launcher_ports(
    *,
    worker_status: Callable[[], dict[str, object]] | None = None,
    spawn_process: Callable[..., int] | None = None,
    record_spawned: Callable[..., None] | None = None,
):
    from codev_platform.reindex.worker_launcher import WorkerLauncherPorts

    return WorkerLauncherPorts(
        worker_status=worker_status or (lambda: {}),
        run_lock_running=lambda: False,
        start_lock=_acquired_start_lock,
        spawn_process=spawn_process or (lambda *_args, **_kwargs: 1234),
        new_owner_token=lambda: "test-owner",
        record_spawned=record_spawned or (lambda *_args, **_kwargs: None),
    )


def _bind_test_marker(
    monkeypatch: pytest.MonkeyPatch,
    marker: Path,
) -> None:
    from codev_platform.reindex import maintenance_gate, worker_launcher

    monkeypatch.setattr(
        worker_launcher,
        "maintenance_gate_active",
        lambda: maintenance_gate.maintenance_gate_active(path=marker),
    )

    @contextmanager
    def use_test_marker_permit():
        with maintenance_gate._worker_start_permit(path=marker) as permitted:
            yield permitted

    monkeypatch.setattr(worker_launcher, "_worker_start_permit", use_test_marker_permit)


def _bind_linux_global_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """把默认全局路径映射到安全的临时根，用于 Linux 门禁契约测试。"""
    from codev_platform.reindex import maintenance_gate

    global_root = tmp_path / "global-root"
    global_root.mkdir()
    global_root.chmod(0o755)
    marker = global_root / "codev-platform" / "reindex-maintenance.gate"
    monkeypatch.setattr(maintenance_gate, "_GLOBAL_GATE_ROOT", global_root)
    monkeypatch.setattr(maintenance_gate, "_DEFAULT_MARKER_PATH", marker)
    effective_uid = getattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(maintenance_gate, "_global_owner_uid", effective_uid)
    monkeypatch.setattr(maintenance_gate, "_effective_uid", effective_uid)
    return marker


def _bind_cgroup_proof(
    monkeypatch: pytest.MonkeyPatch,
    cgroup_path: Path,
) -> None:
    """让门禁经真实 cgroup 解析器验证临时内容，而非复制判定逻辑。"""
    from codev_platform.reindex import external_worker_guard

    probe = external_worker_guard.current_process_in_reindex_unit_cgroup
    monkeypatch.setattr(
        external_worker_guard,
        "current_process_in_reindex_unit_cgroup",
        lambda: probe(cgroup_path=cgroup_path),
    )
