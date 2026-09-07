"""reindex 运维写操作的停机与 run lock 前置门禁测试。"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from codev_platform.reindex.runtime_owner import QueueBackendBinding, QueueOwnerIdentity


def _owner() -> QueueOwnerIdentity:
    return QueueOwnerIdentity("a" * 32, QueueBackendBinding("file", "b" * 64), 1.0)


@contextmanager
def _maintenance_window():
    """为纯编排单测提供已被外层证明的维护窗口。"""
    yield True


def _maintenance_proof() -> None:
    """真实维护状态在集成测试覆盖；本文件只验证管理员编排顺序。"""


def test_默认运维停机证明同时要求systemd与外部写入者(monkeypatch) -> None:
    """owner 与迁移的默认前置不能只确认 systemd worker 已停。"""
    from codev_platform.ops import reindex_admin
    from codev_platform.ops import reindex_admin_systemd_guard
    from codev_platform.reindex import external_worker_guard

    events: list[str] = []
    monkeypatch.setattr(
        reindex_admin_systemd_guard,
        "verify_codev_reindex_stopped",
        lambda: events.append("systemd"),
    )
    monkeypatch.setattr(
        external_worker_guard,
        "assert_no_external_reindex_writers",
        lambda: events.append("writers"),
        raising=False,
    )

    reindex_admin._default_legacy_worker_stop_proof()

    assert events == ["systemd", "writers"]


def test_确认初始化先核对停机再取得run_lock并调用底层初始化() -> None:
    from codev_platform.ops.reindex_admin import run_confirmed_owner_initialization

    events: list[str] = []

    @contextmanager
    def _lock(token: str):
        events.append(f"lock:{token}")
        yield True

    result = run_confirmed_owner_initialization(
        queue=object(),
        cfg={},
        worker_status=lambda: events.append("status") or {"running": False},
        acquire_run_lock=_lock,
        new_owner_token=lambda: "admin-token",
        owner_initializer=lambda **kwargs: events.append("initialize") or _owner(),
        legacy_worker_stop_proof=lambda: events.append("legacy-proof"),
        maintenance_window_permit=_maintenance_window,
        maintenance_window_proof=_maintenance_proof,
    )

    assert result == _owner()
    assert events == [
        "status",
        "legacy-proof",
        "lock:admin-token",
        "legacy-proof",
        "initialize",
    ]


def test_加载式初始化只在维护证明和运行锁内打开队列() -> None:
    """文件建目录与 PG 建表都必须纳入同一受控维护窗口。"""
    from codev_platform.ops.reindex_admin import (
        run_confirmed_owner_initialization_from_loaders,
    )

    events: list[str] = []
    queue = object()

    @contextmanager
    def _window():
        events.append("window-enter")
        yield True
        events.append("window-exit")

    @contextmanager
    def _lock(_token: str):
        events.append("lock")
        yield True

    result = run_confirmed_owner_initialization_from_loaders(
        config_loader=lambda: events.append("config") or {"reindex": {}},
        queue_opener=lambda **kwargs: (
            events.append("queue") or queue
            if kwargs == {"fail_soft": False}
            else pytest.fail("队列必须严格打开")
        ),
        worker_status=lambda: events.append("status") or {"running": False},
        acquire_run_lock=_lock,
        new_owner_token=lambda: "admin-token",
        owner_initializer=lambda **kwargs: (
            events.append("initialize") or _owner()
            if kwargs == {"queue": queue, "cfg": {"reindex": {}}}
            else pytest.fail("初始化参数不正确")
        ),
        legacy_worker_stop_proof=lambda: events.append("legacy-proof"),
        maintenance_window_permit=_window,
        maintenance_window_proof=lambda: events.append("maintenance-proof"),
    )

    assert result == _owner()
    assert events == [
        "window-enter",
        "maintenance-proof",
        "status",
        "legacy-proof",
        "lock",
        "maintenance-proof",
        "legacy-proof",
        "config",
        "queue",
        "initialize",
        "window-exit",
    ]


def test_加载式初始化被维护许可拒绝时不得读取配置或打开队列() -> None:
    from codev_platform.ops.reindex_admin import (
        ReindexAdminPreconditionError,
        run_confirmed_owner_initialization_from_loaders,
    )

    @contextmanager
    def _rejected_window():
        yield False

    with pytest.raises(ReindexAdminPreconditionError):
        run_confirmed_owner_initialization_from_loaders(
            config_loader=lambda: pytest.fail("未取得维护许可不得读取配置"),
            queue_opener=lambda **_kwargs: pytest.fail("未取得维护许可不得打开队列"),
            worker_status=lambda: pytest.fail("未取得维护许可不得读取 worker 状态"),
            acquire_run_lock=lambda _token: pytest.fail("未取得维护许可不得取得运行锁"),
            new_owner_token=lambda: "admin-token",
            owner_initializer=lambda **_kwargs: pytest.fail("未取得维护许可不得初始化"),
            maintenance_window_permit=_rejected_window,
        )


def test_确认初始化必须持有维护窗口并在锁内再次证明() -> None:
    """没有 prepare 的手工停机不得成为 owner 写入授权。"""
    from codev_platform.ops.reindex_admin import run_confirmed_owner_initialization

    events: list[str] = []

    @contextmanager
    def _window():
        events.append("window-enter")
        yield True
        events.append("window-exit")

    @contextmanager
    def _lock(_token: str):
        events.append("lock")
        yield True

    result = run_confirmed_owner_initialization(
        queue=object(),
        cfg={},
        worker_status=lambda: events.append("status") or {"running": False},
        acquire_run_lock=_lock,
        new_owner_token=lambda: "admin-token",
        owner_initializer=lambda **_kwargs: events.append("initialize") or _owner(),
        legacy_worker_stop_proof=lambda: events.append("legacy-proof"),
        maintenance_window_permit=_window,
        maintenance_window_proof=lambda: events.append("maintenance-proof"),
    )

    assert result == _owner()
    assert events == [
        "window-enter",
        "maintenance-proof",
        "status",
        "legacy-proof",
        "lock",
        "maintenance-proof",
        "legacy-proof",
        "initialize",
        "window-exit",
    ]


def test_确认初始化取得run_lock后重新证明宽写入者仍已停止() -> None:
    from codev_platform.ops.reindex_admin import (
        ReindexAdminPreconditionError,
        run_confirmed_owner_initialization,
    )

    events: list[str] = []

    @contextmanager
    def _lock(_token: str):
        events.append("lock")
        yield True

    def _proof() -> None:
        events.append("proof")
        if events.count("proof") == 2:
            raise RuntimeError("writer started after initial scan")

    with pytest.raises(ReindexAdminPreconditionError):
        run_confirmed_owner_initialization(
            queue=object(),
            cfg={},
            worker_status=lambda: events.append("status") or {"running": False},
            acquire_run_lock=_lock,
            new_owner_token=lambda: "admin-token",
            owner_initializer=lambda **_kwargs: pytest.fail("二次证明失败不得初始化 owner"),
            legacy_worker_stop_proof=_proof,
            maintenance_window_permit=_maintenance_window,
            maintenance_window_proof=_maintenance_proof,
        )

    assert events == ["status", "proof", "lock", "proof"]


def test_确认初始化无法证明worker停止时不取得锁也不写owner() -> None:
    from codev_platform.ops.reindex_admin import (
        ReindexAdminPreconditionError,
        run_confirmed_owner_initialization,
    )

    with pytest.raises(ReindexAdminPreconditionError):
        run_confirmed_owner_initialization(
            queue=object(),
            cfg={},
            worker_status=lambda: {"running": True},
            acquire_run_lock=lambda _token: pytest.fail("运行中不得尝试取得运维锁"),
            new_owner_token=lambda: "admin-token",
            owner_initializer=lambda **_kwargs: pytest.fail("运行中不得初始化 owner"),
            legacy_worker_stop_proof=lambda: pytest.fail("运行中不得证明旧 worker 已停"),
            maintenance_window_permit=_maintenance_window,
            maintenance_window_proof=_maintenance_proof,
        )


def test_确认旧任务迁移使用取得自身锁前的停机快照() -> None:
    from codev_platform.ops.reindex_admin import run_confirmed_legacy_migration

    events: list[str] = []

    @contextmanager
    def _lock(token: str):
        events.append(f"lock:{token}")
        yield True

    def _migrate(**kwargs):
        events.append("migrate")
        assert kwargs["confirmed"] is True
        assert kwargs["run_lock_acquired"] is True
        assert "worker_status" not in kwargs
        assert kwargs["legacy_worker_stopped"] is True
        assert kwargs["timeout_sec"] == 0.3
        return "report"

    assert run_confirmed_legacy_migration(
        queue=object(),
        timeout_sec=0.3,
        worker_status=lambda: events.append("status") or {"running": False},
        acquire_run_lock=_lock,
        new_owner_token=lambda: "admin-token",
        migration_runner=_migrate,
        legacy_worker_stop_proof=lambda: events.append("legacy-proof"),
        maintenance_window_permit=_maintenance_window,
        maintenance_window_proof=_maintenance_proof,
    ) == "report"
    assert events == [
        "status",
        "legacy-proof",
        "lock:admin-token",
        "legacy-proof",
        "migrate",
    ]


def test_init_owner命令的演练模式不读取队列也不写入() -> None:
    from codev_platform.ops.reindex_admin import cmd_reindex_admin

    output: list[str] = []

    assert cmd_reindex_admin(
        SimpleNamespace(action="init-owner", yes=False),
        out=output.append,
        err=lambda _message: pytest.fail("演练不应报错"),
        config_loader=lambda: pytest.fail("演练不应读配置"),
        queue_opener=lambda **_kwargs: pytest.fail("演练不应打开队列"),
    ) == 0
    assert output == ["演练：init-owner 是写操作；确认后请添加 --yes"]


def test_init_owner命令确认后只以严格队列初始化且不输出token(monkeypatch) -> None:
    from codev_platform.ops import reindex_admin

    output: list[str] = []
    errors: list[str] = []
    queue = object()

    def _confirmed_loader(**kwargs):
        return kwargs["owner_initializer"](
            queue=kwargs["queue_opener"](fail_soft=False),
            cfg=kwargs["config_loader"](),
        )

    monkeypatch.setattr(
        reindex_admin,
        "run_confirmed_owner_initialization_from_loaders",
        _confirmed_loader,
    )

    assert reindex_admin.cmd_reindex_admin(
        SimpleNamespace(action="init-owner", yes=True),
        out=output.append,
        err=errors.append,
        config_loader=lambda: {"reindex": {}},
        queue_opener=lambda **kwargs: queue if kwargs == {"fail_soft": False} else None,
        owner_runner=lambda **kwargs: (
            _owner()
            if kwargs["queue"] is queue and kwargs["cfg"] == {"reindex": {}}
            else pytest.fail("owner 参数不正确")
        ),
    ) == 0
    assert errors == []
    assert output == ["稳定 queue owner 初始化完成（后端=file）"]
    assert _owner().token not in "\n".join(output)


def test_migrate_legacy命令仅转交专用迁移壳并保留依赖注入() -> None:
    from codev_platform.ops.reindex_admin import cmd_reindex_admin

    captured: dict[str, object] = {}

    def config_loader() -> dict[str, object]:
        return {"reindex": {}}

    def queue_opener(**_kwargs: object) -> object:
        return object()

    args = SimpleNamespace(action="migrate-legacy", yes=False, pending_map=None)

    def _command(received_args: object, **kwargs: object) -> int:
        captured["args"] = received_args
        captured.update(kwargs)
        return 9

    assert cmd_reindex_admin(
        args,
        config_loader=config_loader,
        queue_opener=queue_opener,
        migration_command=_command,
    ) == 9

    assert captured["args"] is args
    assert captured["config_loader"] is config_loader
    assert captured["queue_opener"] is queue_opener
