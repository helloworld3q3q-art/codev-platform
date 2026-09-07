"""数据库迁移后的只读独立验收测试。"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from codev_platform.web.db.migration_contract import (
    DatabaseMigrationError,
    DatabaseVerificationPorts,
    MigrationPlan,
    RevisionState,
    SchemaFingerprint,
)
from codev_platform.web.db.migration_coordinator import verify_database_current


_PLAN = MigrationPlan(("0001_base", "0002_head"), "0002_head")


def _ports(
    events: list[str],
    *,
    revision: RevisionState | None = None,
    fingerprint: SchemaFingerprint | None = None,
) -> DatabaseVerificationPorts:
    @contextmanager
    def transaction():
        events.append("只读事务开始")
        try:
            yield
        finally:
            events.append("只读事务结束")

    return DatabaseVerificationPorts(
        readonly_transaction=transaction,
        read_revision=lambda: events.append("读取版本")
        or revision
        or RevisionState(True, _PLAN.head),
        read_fingerprint=lambda: events.append("读取指纹")
        or fingerprint
        or SchemaFingerprint("a" * 64, 7),
        verify_runtime_contract=lambda: events.append("运行契约"),
    )


def test_只读验收要求head表数量和运行契约全部通过() -> None:
    events: list[str] = []

    report = verify_database_current(_PLAN, _ports(events), expected_table_count=7)

    assert report.head_revision == _PLAN.head
    assert report.schema_sha256 == "a" * 64
    assert report.table_count == 7
    assert events == [
        "只读事务开始",
        "读取版本",
        "读取指纹",
        "运行契约",
        "只读事务结束",
    ]


@pytest.mark.parametrize(
    ("revision", "fingerprint", "message"),
    [
        (RevisionState(False, None), SchemaFingerprint("a" * 64, 7), "revision"),
        (RevisionState(True, "0001_base"), SchemaFingerprint("a" * 64, 7), "revision"),
        (RevisionState(True, _PLAN.head), SchemaFingerprint("a" * 64, 6), "表集合"),
    ],
)
def test_版本或受管表集合不精确时失败关闭(
    revision: RevisionState,
    fingerprint: SchemaFingerprint,
    message: str,
) -> None:
    with pytest.raises(DatabaseMigrationError, match=message):
        verify_database_current(
            _PLAN,
            _ports([], revision=revision, fingerprint=fingerprint),
            expected_table_count=7,
        )


def test_只读验收端口异常不泄露dsn() -> None:
    ports = _ports([])
    object.__setattr__(
        ports,
        "read_revision",
        lambda: (_ for _ in ()).throw(RuntimeError("postgresql://u:secret@host/db")),
    )

    with pytest.raises(DatabaseMigrationError) as captured:
        verify_database_current(_PLAN, ports, expected_table_count=7)

    assert "secret" not in str(captured.value)


def test_postgres验收入口只使用只读端口与固定受管表数量(monkeypatch) -> None:
    import codev_platform.web.db.migration_postgres as postgres

    sentinel_plan = object()
    sentinel_ports = object()
    calls: list[object] = []

    class FakeAdapter:
        def __init__(self, engine, plan) -> None:
            calls.append(("adapter", engine, plan))

        def verification_ports(self):
            calls.append("readonly-ports")
            return sentinel_ports

    expected = object()
    monkeypatch.setattr(postgres, "load_migration_plan", lambda: sentinel_plan)
    monkeypatch.setattr(postgres, "PostgresMigrationAdapter", FakeAdapter)
    monkeypatch.setattr(
        postgres,
        "verify_database_current",
        lambda plan, ports, *, expected_table_count: calls.append(
            ("verify", plan, ports, expected_table_count)
        )
        or expected,
    )

    engine = object()
    assert postgres.verify_postgres_current(engine) is expected
    assert calls == [
        ("adapter", engine, sentinel_plan),
        "readonly-ports",
        ("verify", sentinel_plan, sentinel_ports, len(postgres.MANAGED_TABLE_NAMES)),
    ]
