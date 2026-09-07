"""reindex-queue CLI 的 worker 与单次 drain 测试。"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from tests.reindex_queue_cli_support import (
    _FakeQueue,
    _args,
    _confirmed_queue_window,
    _maintenance_permit,
    rq,
)

pytestmark = pytest.mark.usefixtures(_confirmed_queue_window.__name__)


def test_drain_once_calls_worker(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    q = _FakeQueue()

    class _Worker:
        def __init__(self, queue, cfg, on_heartbeat=None, on_job_event=None):
            assert queue is q
            assert callable(on_heartbeat)
            assert callable(on_job_event)

        def drain_once(self):
            return 3

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)
    monkeypatch.setattr("codev_platform.reindex.ReindexWorker", _Worker)
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "legacy")

    assert rq.cmd_reindex_queue(_args(action="drain-once")) == 0

    assert "drain-once processed=3" in capsys.readouterr().out


def test_drain_once_defaults_to_isolated_runtime_without_opening_legacy_queue(
    monkeypatch,
    capsys,
    tmp_path,
):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    calls: list[str] = []

    class _Loop:
        def drain_once(self):
            calls.append("isolated-drain")
            return 2

    class _Runtime:
        loop = _Loop()

    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "isolated", raising=False)
    monkeypatch.setattr(
        rq,
        "_build_isolated_worker",
        lambda _cfg, _owner: calls.append("build") or _Runtime(),
        raising=False,
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: pytest.fail("isolated drain 不得预先打开 legacy queue"),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.ReindexWorker",
        lambda *_args, **_kwargs: pytest.fail("默认路径不得构造 legacy worker"),
    )

    assert rq.cmd_reindex_queue(_args(action="drain-once")) == 0
    assert calls == ["build", "isolated-drain"]
    assert "drain-once processed=2" in capsys.readouterr().out


@pytest.mark.parametrize("action", ["worker", "drain-once"])
def test_维护门禁阻断直接worker入口且不读取配置或队列(monkeypatch, capsys, action) -> None:
    if action == "worker":
        monkeypatch.setattr(
            "codev_platform.ops.reindex_queue_worker._default_maintenance_waiter",
            lambda: False,
        )
    else:
        monkeypatch.setattr(
            rq,
            "maintenance_reindex_operation_permit",
            lambda: _maintenance_permit(False),
            raising=False,
        )
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: pytest.fail("维护门禁拒绝后不得读取 worker 配置"),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: pytest.fail("维护门禁拒绝后不得打开队列"),
    )

    assert rq.cmd_reindex_queue(_args(action=action)) == 1
    assert "维护窗口" in capsys.readouterr().err


def test_维护许可覆盖整个worker动作直至返回(monkeypatch) -> None:
    events: list[str] = []

    @contextmanager
    def _tracking_permit():
        events.append("enter")
        yield True
        events.append("exit")

    monkeypatch.setattr(
        rq,
        "maintenance_reindex_operation_permit",
        _tracking_permit,
        raising=False,
    )

    def _action(_args) -> int:
        assert events == ["enter"]
        events.append("action")
        return 7

    assert rq._run_permitted_worker_action(_args(action="worker"), _action) == 7
    assert events == ["enter", "action", "exit"]


def test_常驻worker不再被外层维护共享许可包住整个生命周期(monkeypatch) -> None:
    """prepare 必须能在两轮 drain 之间取得 gate 独占锁。"""
    monkeypatch.setattr(
        rq,
        "_run_permitted_worker_action",
        lambda *_args: pytest.fail("常驻 worker 不得走整生命周期外层许可"),
    )
    monkeypatch.setattr(rq, "_cmd_worker", lambda _args: 7)

    assert rq.cmd_reindex_queue(_args(action="worker")) == 7


def test_drain_once_refuses_when_worker_lock_held(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    q = _FakeQueue()
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    from codev_platform.reindex import supervisor

    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True
        assert rq.cmd_reindex_queue(_args(action="drain-once")) == 1

    assert "already running" in capsys.readouterr().err


def test_worker_rejects_non_positive_timing(monkeypatch, capsys):
    q = _FakeQueue()
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kw: q)

    assert rq.cmd_reindex_queue(_args(action="worker", idle_exit_sec=0)) == 1
    assert "--idle-exit-sec" in capsys.readouterr().err

    assert rq.cmd_reindex_queue(_args(action="worker", heartbeat_sec=0)) == 1
    assert "--heartbeat-sec" in capsys.readouterr().err


def test_worker_refuses_legacy_mode_when_production_unit_pins_isolated(monkeypatch, capsys):
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "legacy")
    monkeypatch.setattr(
        rq,
        "_build_isolated_worker",
        lambda *_args: pytest.fail("模式不匹配不得构造隔离运行时"),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: pytest.fail("模式不匹配不得打开 legacy queue"),
    )

    assert (
        rq.cmd_reindex_queue(
            _args(action="worker", require_execution_mode="isolated"),
        )
        == 1
    )
    assert "只允许 execution_mode=isolated" in capsys.readouterr().err


def test_worker短驻模式同样拒绝legacy执行器(monkeypatch, capsys):
    """短驻 legacy 也会跨维护窗口写入，不能作为绕过常驻限制的入口。"""
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "legacy")

    assert rq.cmd_reindex_queue(_args(action="worker", idle_exit_sec=0.1, heartbeat_sec=0.1)) == 1
    assert "只允许 execution_mode=isolated" in capsys.readouterr().err


def test_worker_defaults_to_isolated_runtime_without_heartbeat_thread(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    calls: list[tuple[float, float] | str] = []

    class _Loop:
        async def run_until_idle(self, *, idle_exit_sec: float, poll_sec: float) -> None:
            calls.append((idle_exit_sec, poll_sec))

    class _Runtime:
        loop = _Loop()

    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "isolated", raising=False)
    monkeypatch.setattr(
        rq,
        "_build_isolated_worker",
        lambda _cfg, _owner: calls.append("build") or _Runtime(),
        raising=False,
    )
    monkeypatch.setattr(
        "codev_platform.reindex.open_default_queue",
        lambda **_kwargs: pytest.fail("isolated worker 不得预先打开 legacy queue"),
    )
    monkeypatch.setattr(
        "codev_platform.reindex.supervisor.heartbeat_thread",
        lambda *_args, **_kwargs: pytest.fail("isolated worker 不得创建 heartbeat 线程"),
    )

    assert (
        rq.cmd_reindex_queue(
            _args(action="worker", idle_exit_sec=0.1, heartbeat_sec=0.1),
        )
        == 0
    )
    assert calls == ["build", (0.1, 0.1)]
    from codev_platform.reindex import supervisor

    status = supervisor.worker_status()
    assert status["execution_mode"] == "isolated"
    assert status["exit_reason"] == "idle"
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True


@pytest.mark.parametrize("action", ["drain-once", "worker"])
def test_直接worker缺失owner以稳定退出码退出且释放运行锁(
    monkeypatch,
    capsys,
    tmp_path,
    action,
) -> None:
    from codev_platform.reindex.isolated_worker_startup import QueueStartupBootstrapRequired
    from codev_platform.reindex.owner_readiness import OWNER_BOOTSTRAP_EXIT_CODE
    from codev_platform.reindex import supervisor

    token = "d" * 32
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "isolated")
    monkeypatch.setattr(
        rq,
        "_build_isolated_worker",
        lambda _cfg, _owner: (_ for _ in ()).throw(
            QueueStartupBootstrapRequired(f"owner={token}")
        ),
    )

    result = rq.cmd_reindex_queue(
        _args(action=action, idle_exit_sec=0.1 if action == "worker" else None),
    )

    assert result == OWNER_BOOTSTRAP_EXIT_CODE
    captured = capsys.readouterr()
    assert "维护窗口完成恢复审计后执行 reindex-queue init-owner --yes" in captured.err
    assert "错误：" in captured.err
    assert token not in captured.err
    assert supervisor.worker_status()["exit_reason"] == "bootstrap-required"
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True


def test_drain_once人工处理owner阻断以稳定退出码退出且释放运行锁(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    from codev_platform.reindex import supervisor
    from codev_platform.reindex.isolated_worker_startup import QueueStartupOperatorBlocked
    from codev_platform.reindex.owner_readiness import OWNER_OPERATOR_BLOCKED_EXIT_CODE

    token = "g" * 32

    class _Loop:
        def drain_once(self) -> int:
            raise QueueStartupOperatorBlocked(f"owner={token}")

    class _Runtime:
        loop = _Loop()

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "isolated")
    monkeypatch.setattr(rq, "_build_isolated_worker", lambda _cfg, _owner: _Runtime())

    result = rq.cmd_reindex_queue(_args(action="drain-once"))

    captured = capsys.readouterr()
    assert result == OWNER_OPERATOR_BLOCKED_EXIT_CODE
    assert "错误：" in captured.err
    assert token not in captured.err
    assert supervisor.worker_status()["exit_reason"] == "owner-operator-blocked"
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True


def test_直接worker构造期人工处理owner阻断以稳定退出码退出且释放运行锁(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    from codev_platform.reindex import supervisor
    from codev_platform.reindex.isolated_worker_startup import QueueStartupOperatorBlocked
    from codev_platform.reindex.owner_readiness import OWNER_OPERATOR_BLOCKED_EXIT_CODE

    token = "h" * 32
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "isolated")
    monkeypatch.setattr(
        rq,
        "_build_isolated_worker",
        lambda _cfg, _owner: (_ for _ in ()).throw(
            QueueStartupOperatorBlocked(f"owner={token}")
        ),
    )

    result = rq.cmd_reindex_queue(_args(action="worker", idle_exit_sec=0.1))

    captured = capsys.readouterr()
    assert result == OWNER_OPERATOR_BLOCKED_EXIT_CODE
    assert "错误：" in captured.err
    assert token not in captured.err
    assert supervisor.worker_status()["exit_reason"] == "owner-operator-blocked"
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True


@pytest.mark.parametrize("action", ["drain-once", "worker"])
def test_直接worker普通构造异常仍然上抛且释放运行锁(
    monkeypatch,
    tmp_path,
    action,
) -> None:
    from codev_platform.reindex import supervisor
    from codev_platform.reindex.isolated_worker_startup import QueueStartupError

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "isolated")
    monkeypatch.setattr(
        rq,
        "_build_isolated_worker",
        lambda _cfg, _owner: (_ for _ in ()).throw(QueueStartupError("构造失败")),
    )

    with pytest.raises(QueueStartupError, match="构造失败"):
        rq.cmd_reindex_queue(
            _args(action=action, idle_exit_sec=0.1 if action == "worker" else None),
        )

    assert supervisor.worker_status()["exit_reason"] == "error"
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True


def test_常驻worker运行期缺失owner同样稳定退出(monkeypatch, capsys, tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_startup import QueueStartupBootstrapRequired
    from codev_platform.reindex.owner_readiness import OWNER_BOOTSTRAP_EXIT_CODE
    from codev_platform.reindex import supervisor

    token = "e" * 32

    class _Loop:
        async def run_until_idle(self, **_kwargs) -> None:
            raise QueueStartupBootstrapRequired(f"owner={token}")

    class _Runtime:
        loop = _Loop()

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "isolated")
    monkeypatch.setattr(rq, "_build_isolated_worker", lambda _cfg, _owner: _Runtime())

    result = rq.cmd_reindex_queue(_args(action="worker", idle_exit_sec=0.1))

    captured = capsys.readouterr()
    assert result == OWNER_BOOTSTRAP_EXIT_CODE
    assert token not in captured.err
    assert supervisor.worker_status()["exit_reason"] == "bootstrap-required"


def test_常驻worker运行期人工处理owner阻断以稳定退出码退出且释放运行锁(
    monkeypatch,
    capsys,
    tmp_path,
) -> None:
    from codev_platform.reindex import supervisor
    from codev_platform.reindex.isolated_worker_startup import QueueStartupOperatorBlocked
    from codev_platform.reindex.owner_readiness import OWNER_OPERATOR_BLOCKED_EXIT_CODE

    token = "i" * 32

    class _Loop:
        async def run_until_idle(self, **_kwargs) -> None:
            raise QueueStartupOperatorBlocked(f"owner={token}")

    class _Runtime:
        loop = _Loop()

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rq, "_worker_execution_mode", lambda _cfg: "isolated")
    monkeypatch.setattr(rq, "_build_isolated_worker", lambda _cfg, _owner: _Runtime())

    result = rq.cmd_reindex_queue(_args(action="worker", idle_exit_sec=0.1))

    captured = capsys.readouterr()
    assert result == OWNER_OPERATOR_BLOCKED_EXIT_CODE
    assert "错误：" in captured.err
    assert token not in captured.err
    assert supervisor.worker_status()["exit_reason"] == "owner-operator-blocked"
    with supervisor.acquire_run_lock(supervisor.new_owner_token()) as acquired:
        assert acquired is True
