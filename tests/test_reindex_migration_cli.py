"""legacy 队列迁移命令的停机门禁、映射输入与安全输出测试。"""
from __future__ import annotations

from contextlib import contextmanager
import os
import stat
from types import SimpleNamespace

import pytest

from codev_platform.ops.reindex_migration import (
    LegacyMigrationItem,
    LegacyMigrationReport,
    PendingMigrationReport,
    QueueMigrationWindowReport,
)
from codev_platform.reindex.runtime_owner import QueueBackendBinding, QueueOwnerIdentity


def _owner() -> QueueOwnerIdentity:
    return QueueOwnerIdentity("a" * 32, QueueBackendBinding("file", "b" * 64), 1.0)


def _remaining_report() -> QueueMigrationWindowReport:
    item = LegacyMigrationItem(
        "pending",
        "demo__chroma",
        ("symbolic_target_commit",),
        "pending_mapping_required",
        "pt:pending-version",
    )
    active = LegacyMigrationReport(False, (item,), 1, False, (item,), False)
    pending = PendingMigrationReport(False, (), True, 0, False)
    return QueueMigrationWindowReport(active, pending, False, False)


def _completed_report() -> QueueMigrationWindowReport:
    active = LegacyMigrationReport(True, (), 0, True, (), True)
    pending = PendingMigrationReport(True, (), True, 0, True)
    return QueueMigrationWindowReport(active, pending, True, True)


@contextmanager
def _maintenance_window():
    """为迁移编排单测提供已由集成层证明的维护窗口。"""
    yield True


def _maintenance_proof() -> None:
    """真实 marker/drop-in/systemd 证明在维护窗口集成测试覆盖。"""


def _install_confirmed_window(monkeypatch) -> None:
    """迁移数据转换单测使用已证明维护窗口替身，避免依赖本机 systemd。"""
    from codev_platform.ops import reindex_admin

    def _run(*, input_loader, migration_runner):
        inputs = input_loader()
        return migration_runner(
            **inputs,
            confirmed=True,
            run_lock_acquired=True,
            legacy_worker_stopped=True,
        )

    monkeypatch.setattr(
        reindex_admin,
        "run_confirmed_queue_migration_from_loader",
        _run,
    )


def test_确认组合迁移先证明停机并取得锁再执行() -> None:
    from codev_platform.ops.reindex_admin import run_confirmed_queue_migration

    events: list[str] = []
    received: dict[str, object] = {}

    @contextmanager
    def _lock(token: str):
        events.append(f"lock:{token}")
        yield True

    def _migrate(**kwargs: object) -> QueueMigrationWindowReport:
        events.append("migrate")
        received.update(kwargs)
        return _completed_report()

    result = run_confirmed_queue_migration(
        queue=object(),
        pending_mapping=(),
        stable_owner_token="stable-owner",
        bootstrap=False,
        timeout_sec=0.3,
        worker_status=lambda: events.append("status") or {"running": False},
        acquire_run_lock=_lock,
        new_owner_token=lambda: "admin-token",
        legacy_worker_stop_proof=lambda: events.append("legacy-proof"),
        migration_runner=_migrate,
        maintenance_window_permit=_maintenance_window,
        maintenance_window_proof=_maintenance_proof,
    )

    assert result.completed is True
    assert events == [
        "status",
        "legacy-proof",
        "lock:admin-token",
        "legacy-proof",
        "migrate",
    ]
    assert received == {
        "queue": received["queue"],
        "pending_mapping": (),
        "confirmed": True,
        "run_lock_acquired": True,
        "legacy_worker_stopped": True,
        "stable_owner_token": "stable-owner",
        "bootstrap": False,
        "timeout_sec": 0.3,
    }


def test_确认迁移把队列加载延后到受控维护窗口(monkeypatch) -> None:
    """严格打开 PG/File 队列不得发生在 gate 独占锁之外。"""
    from codev_platform.ops import reindex_admin
    from codev_platform.ops.reindex_migration_cli import cmd_migrate_legacy

    events: list[str] = []
    queue = object()
    owner = _owner()

    def _confirmed_window(*, input_loader, migration_runner):
        events.append("window")
        inputs = input_loader()
        assert inputs["queue"] is queue
        return migration_runner(**inputs)

    monkeypatch.setattr(
        reindex_admin,
        "run_confirmed_queue_migration_from_loader",
        _confirmed_window,
        raising=False,
    )

    result = cmd_migrate_legacy(
        SimpleNamespace(action="migrate-legacy", yes=True, pending_map=None),
        out=lambda _message: None,
        err=lambda message: pytest.fail(message),
        config_loader=lambda: events.append("config") or {"reindex": {}},
        queue_opener=lambda **kwargs: (
            events.append("queue") or queue
            if kwargs == {"fail_soft": False}
            else pytest.fail("队列必须严格打开")
        ),
        owner_context_loader=lambda received_queue, _cfg: (
            events.append("owner-context") or (owner.token, False)
            if received_queue is queue
            else pytest.fail("owner 上下文收到错误队列")
        ),
        migration_runner=lambda **_kwargs: pytest.fail("确认不得走演练执行器"),
        confirmed_runner=lambda **kwargs: (
            events.append("migrate") or _completed_report()
            if kwargs["queue"] is queue
            else pytest.fail("迁移收到错误队列")
        ),
    )

    assert result == 0
    assert events == ["window", "config", "queue", "owner-context", "migrate"]


def test_迁移演练输出可编辑映射模板且遗留任务返回非零() -> None:
    from codev_platform.ops.reindex_migration_cli import cmd_migrate_legacy

    output: list[str] = []
    calls: list[dict[str, object]] = []

    def _dry_runner(**kwargs: object) -> QueueMigrationWindowReport:
        calls.append(kwargs)
        return _remaining_report()

    result = cmd_migrate_legacy(
        SimpleNamespace(action="migrate-legacy", yes=False, pending_map=None),
        out=output.append,
        err=lambda message: output.append(f"ERR:{message}"),
        config_loader=lambda: {"reindex": {}},
        queue_opener=lambda **kwargs: object() if kwargs == {"fail_soft": False} else None,
        owner_context_loader=lambda _queue, _cfg: (None, True),
        migration_runner=_dry_runner,
        confirmed_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("演练不得进入写路径")),
    )

    rendered = "\n".join(output)
    assert result == 1
    assert calls == [{
        "queue": calls[0]["queue"],
        "pending_mapping": None,
        "confirmed": False,
        "run_lock_acquired": False,
        "legacy_worker_stopped": False,
        "stable_owner_token": None,
        "bootstrap": True,
        "timeout_sec": 0.3,
    }]
    assert "pt:pending-version" in rendered
    assert "pending_mapping_required" in rendered
    assert "迁移未完成" in rendered


def test_确认迁移解析映射并将稳定owner仅传入受控执行器(tmp_path, monkeypatch) -> None:
    from codev_platform.ops.reindex_migration_cli import cmd_migrate_legacy

    _install_confirmed_window(monkeypatch)

    mapping = tmp_path / "pending-map.json"
    mapping.write_text(
        '{"schema_version":1,"items":[{"project_id":"demo","kind":"chroma",'
        '"pending_version":"pt:pending-version","target_commit":"'
        + "a" * 40
        + '"}]}',
        encoding="utf-8",
    )
    output: list[str] = []
    received: dict[str, object] = {}
    owner = _owner()

    def _confirmed_runner(**kwargs: object) -> QueueMigrationWindowReport:
        received.update(kwargs)
        return _completed_report()

    result = cmd_migrate_legacy(
        SimpleNamespace(action="migrate-legacy", yes=True, pending_map=str(mapping)),
        out=output.append,
        err=lambda message: output.append(f"ERR:{message}"),
        config_loader=lambda: {"reindex": {}},
        queue_opener=lambda **kwargs: object() if kwargs == {"fail_soft": False} else None,
        owner_context_loader=lambda _queue, _cfg: (owner.token, False),
        migration_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("确认不得走演练执行器")),
        confirmed_runner=_confirmed_runner,
    )

    rendered = "\n".join(output)
    assert result == 0
    assert received["pending_mapping"]
    assert received["stable_owner_token"] == owner.token
    assert received["bootstrap"] is False
    assert received["timeout_sec"] == 0.3
    assert owner.token not in rendered
    assert "迁移完成" in rendered


def test_pending映射文件在lstat与open之间被替换时失败关闭(monkeypatch) -> None:
    import codev_platform.ops.reindex_migration_cli as migration_cli

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def fileno(self) -> int:
            return 7

        def read(self, _limit: int) -> bytes:
            return b"{}"

    before = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_size=2,
        st_dev=10,
        st_ino=11,
    )
    after = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_size=2,
        st_dev=10,
        st_ino=12,
    )
    monkeypatch.setattr(migration_cli.Path, "lstat", lambda _path: before)
    monkeypatch.setattr(migration_cli.Path, "open", lambda _path, _mode: _Stream())
    monkeypatch.setattr(os, "fstat", lambda _fd: after)

    with pytest.raises(ValueError, match="映射文件"):
        migration_cli._read_bounded_regular_file("pending-map.json")


def test_确认迁移有遗留时不输出pending版本能力(monkeypatch) -> None:
    from codev_platform.ops.reindex_migration_cli import cmd_migrate_legacy

    _install_confirmed_window(monkeypatch)

    output: list[str] = []
    owner = _owner()

    result = cmd_migrate_legacy(
        SimpleNamespace(action="migrate-legacy", yes=True, pending_map=None),
        out=output.append,
        err=lambda message: output.append(f"ERR:{message}"),
        config_loader=lambda: {"reindex": {}},
        queue_opener=lambda **kwargs: object() if kwargs == {"fail_soft": False} else None,
        owner_context_loader=lambda _queue, _cfg: (owner.token, False),
        migration_runner=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("确认不得走演练执行器")),
        confirmed_runner=lambda **_kwargs: _remaining_report(),
    )

    rendered = "\n".join(output)
    assert result == 1
    assert "pending_mapping_required" in rendered
    assert "pt:pending-version" not in rendered
    assert "待补 pending 映射模板" not in rendered
