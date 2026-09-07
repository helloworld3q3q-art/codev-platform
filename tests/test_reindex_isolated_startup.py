"""隔离 worker 稳定 owner 与恢复审计的启动门禁测试。"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex.runtime_owner import (
    QueueBackendBinding,
    QueueOwnerIdentity,
    QueueOwnerTimeout,
)
from codev_platform.reindex.recovery_audit import StartupRecoverySnapshot


@dataclass
class _Store:
    owner: QueueOwnerIdentity
    events: list[str]

    def load(self, binding: QueueBackendBinding) -> QueueOwnerIdentity:
        self.events.append(f"owner:{binding.kind}")
        return self.owner


def _owner() -> QueueOwnerIdentity:
    return QueueOwnerIdentity(
        "a" * 32,
        QueueBackendBinding("file", "b" * 64),
        1.0,
    )


def test_startup_context_loads_stable_owner_then_audits_before_backend_selection(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_startup import load_queue_startup_context

    events: list[str] = []
    queue = FileSpoolQueue(tmp_path / "spool")
    owner = _owner()
    journal = object()
    snapshot = object()

    context = load_queue_startup_context(
        queue=queue,
        cfg={},
        owner_store=_Store(owner, events),
        journal_factory=lambda token: events.append(f"journal:{token}") or journal,
        inspect_recovery=lambda actual_queue, actual_journal: (
            events.append("audit")
            or (snapshot if actual_queue is queue and actual_journal is journal else None)
        ),
        verify_owner=lambda actual_snapshot, token: events.append(
            f"verify:{token}" if actual_snapshot is snapshot else "bad-verify"
        ),
    )

    assert context.owner is owner
    assert context.journal is journal
    assert context.recovery_snapshot is snapshot
    assert events == ["owner:file", f"journal:{owner.token}", "audit", f"verify:{owner.token}"]


def test_startup_context_fails_closed_when_recovery_ownership_does_not_match(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_startup import (
        QueueStartupError,
        load_queue_startup_context,
    )

    queue = FileSpoolQueue(tmp_path / "spool")

    with pytest.raises(QueueStartupError) as raised:
        load_queue_startup_context(
            queue=queue,
            cfg={},
            owner_store=_Store(_owner(), []),
            journal_factory=lambda _token: object(),
            inspect_recovery=lambda _queue, _journal: object(),
            verify_owner=lambda _snapshot, _token: (_ for _ in ()).throw(
                RuntimeError("foreign owner")
            ),
        )
    assert "foreign owner" not in str(raised.value)


def test_startup边界异常链不泄露owner_token(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_startup import (
        QueueStartupError,
        load_queue_startup_context,
    )

    token = "b" * 32
    queue = FileSpoolQueue(tmp_path / "spool")

    class _BrokenStore:
        def load(self, binding):
            del binding
            raise RuntimeError(f"owner={token}")

    with pytest.raises(QueueStartupError) as raised:
        load_queue_startup_context(queue=queue, cfg={}, owner_store=_BrokenStore())
    pending = [raised.value]
    chain = ""
    while pending:
        error = pending.pop()
        chain += f"{error!s}\n{error!r}\n"
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None:
            pending.append(error.__context__)
    assert token not in chain


def test_startup缺失owner精确映射且异常链不泄露token(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_startup import (
        QueueStartupBootstrapRequired,
        load_queue_startup_context,
    )
    from codev_platform.reindex.runtime_owner import QueueOwnerBootstrapRequired

    token = "c" * 32
    queue = FileSpoolQueue(tmp_path / "spool")

    class _CustomBootstrapRequired(QueueOwnerBootstrapRequired):
        pass

    class _BootstrapStore:
        def load(self, binding):
            del binding
            raise _CustomBootstrapRequired(f"owner={token}")

    with pytest.raises(QueueStartupBootstrapRequired) as raised:
        load_queue_startup_context(queue=queue, cfg={}, owner_store=_BootstrapStore())

    pending = [raised.value]
    chain = ""
    while pending:
        error = pending.pop()
        chain += f"{error!s}\n{error!r}\n"
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None:
            pending.append(error.__context__)
    assert token not in chain


@pytest.mark.parametrize(
    "owner_error_name",
    [
        "QueueOwnerRecoveryRequired",
        "QueueOwnerCorruptionError",
        "QueueOwnerUnavailableError",
    ],
)
def test_startup需人工处理的owner异常精确映射且异常链不泄露token(
    tmp_path,
    owner_error_name,
) -> None:
    from codev_platform.reindex.isolated_worker_startup import (
        QueueStartupOperatorBlocked,
        load_queue_startup_context,
    )
    from codev_platform.reindex import runtime_owner

    token = "f" * 32
    queue = FileSpoolQueue(tmp_path / "spool")

    class _RecoveryFailureStore:
        def load(self, binding):
            del binding
            owner_error = getattr(runtime_owner, owner_error_name)
            raise owner_error(f"owner={token}")

    with pytest.raises(QueueStartupOperatorBlocked) as raised:
        load_queue_startup_context(queue=queue, cfg={}, owner_store=_RecoveryFailureStore())

    pending = [raised.value]
    chain = ""
    while pending:
        error = pending.pop()
        chain += f"{error!s}\n{error!r}\n"
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None:
            pending.append(error.__context__)
    assert token not in chain


@pytest.mark.parametrize("failure_type", [RuntimeError, QueueOwnerTimeout])
def test_startup普通异常与owner锁超时仍保持一般启动错误且不泄露token(
    tmp_path,
    failure_type,
) -> None:
    from codev_platform.reindex.isolated_worker_startup import (
        QueueStartupError,
        QueueStartupOperatorBlocked,
        load_queue_startup_context,
    )

    token = "j" * 32
    queue = FileSpoolQueue(tmp_path / "spool")

    class _RuntimeFailureStore:
        def load(self, binding):
            del binding
            raise failure_type(f"owner={token}")

    with pytest.raises(QueueStartupError) as raised:
        load_queue_startup_context(queue=queue, cfg={}, owner_store=_RuntimeFailureStore())

    assert not isinstance(raised.value, QueueStartupOperatorBlocked)
    pending = [raised.value]
    chain = ""
    while pending:
        error = pending.pop()
        chain += f"{error!s}\n{error!r}\n"
        if error.__cause__ is not None:
            pending.append(error.__cause__)
        if error.__context__ is not None:
            pending.append(error.__context__)
    assert token not in chain


def test_init_owner_audits_recovery_before_store_initialization(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_startup import initialize_queue_owner

    events: list[str] = []
    queue = FileSpoolQueue(tmp_path / "spool")
    owner = _owner()

    class _Initializer:
        def initialize(self, binding, *, recovery_state_present):
            events.append(f"initialize:{binding.kind}")
            assert recovery_state_present(0.5) is False
            return owner

    resolved = initialize_queue_owner(
        queue=queue,
        cfg={},
        owner_store=_Initializer(),
        journal_factory=lambda token: events.append(f"journal:{token}") or object(),
        inspect_recovery=lambda _queue, _journal: events.append("audit") or StartupRecoverySnapshot(
            None,
            False,
            (),
            0,
        ),
    )

    assert resolved is owner
    assert events == ["journal:bootstrap-readonly", "audit", "initialize:file"]


def test_init_owner_refuses_to_create_when_recovery_state_exists(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_startup import (
        QueueStartupError,
        initialize_queue_owner,
    )

    queue = FileSpoolQueue(tmp_path / "spool")

    class _Initializer:
        def initialize(self, binding, *, recovery_state_present):
            del binding
            assert recovery_state_present(0.5) is True
            raise RuntimeError("recovery present")

    with pytest.raises(QueueStartupError) as raised:
        initialize_queue_owner(
            queue=queue,
            cfg={},
            owner_store=_Initializer(),
            journal_factory=lambda _token: object(),
            inspect_recovery=lambda _queue, _journal: StartupRecoverySnapshot(
                "old-owner",
                False,
                (),
                0,
            ),
        )
    assert "recovery present" not in str(raised.value)


def test_init_owner_rejects_recovery_state_even_when_store_would_return_existing_owner(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_startup import (
        QueueStartupError,
        initialize_queue_owner,
    )

    queue = FileSpoolQueue(tmp_path / "spool")
    calls: list[str] = []

    class _ExistingOwner:
        def initialize(self, binding, *, recovery_state_present):
            del binding, recovery_state_present
            calls.append("initialize")
            return _owner()

    with pytest.raises(QueueStartupError):
        initialize_queue_owner(
            queue=queue,
            cfg={},
            owner_store=_ExistingOwner(),
            journal_factory=lambda _token: object(),
            inspect_recovery=lambda _queue, _journal: StartupRecoverySnapshot(
                "old-owner",
                False,
                (),
                0,
            ),
        )

    assert calls == []


def test_init_owner_default_adapters_create_then_load_same_stable_owner(tmp_path, monkeypatch) -> None:
    from codev_platform.reindex.isolated_worker_startup import (
        initialize_queue_owner,
        load_queue_startup_context,
    )

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "data"))
    queue = FileSpoolQueue(tmp_path / "queue")

    created = initialize_queue_owner(queue=queue, cfg={})
    context = load_queue_startup_context(queue=queue, cfg={})

    assert context.owner.token == created.token
    assert context.owner.binding == created.binding
