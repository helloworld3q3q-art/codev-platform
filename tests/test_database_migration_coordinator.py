"""数据库迁移分类、接管、升级与后验状态机测试。"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from codev_platform.web.db.migration_contract import (
    DatabaseMigrationError,
    DatabaseMigrationPorts,
    MigrationPlan,
    RevisionState,
    SchemaFingerprint,
)
from codev_platform.web.db.migration_coordinator import migrate_database


_PLAN = MigrationPlan(("0001_base", "0002_next", "0003_head"), "0003_head")


def _fingerprint(marker: str, count: int) -> SchemaFingerprint:
    return SchemaFingerprint(marker * 64, count)


def _ports(
    *,
    revision: RevisionState,
    target: SchemaFingerprint,
    references: dict[str, SchemaFingerprint],
    head_after_upgrade: SchemaFingerprint | None = None,
):
    state = {"revision": revision, "target": target}
    events: list[object] = []

    @contextmanager
    def transaction():
        events.append("事务开始")
        try:
            yield
        finally:
            events.append("事务结束")

    def lock() -> None:
        events.append("迁移锁")

    def read_revision() -> RevisionState:
        events.append("读取版本")
        return state["revision"]

    def read_fingerprint() -> SchemaFingerprint:
        events.append("读取指纹")
        return state["target"]

    def build(requested: tuple[str, ...]):
        events.append(("参考", requested))
        return {revision: references[revision] for revision in requested}

    def stamp(revision: str) -> None:
        events.append(("接管", revision))
        state["revision"] = RevisionState(True, revision)

    def upgrade(revision: str) -> None:
        events.append(("升级", revision))
        state["revision"] = RevisionState(True, revision)
        state["target"] = head_after_upgrade or references[revision]

    def verify() -> None:
        events.append("运行契约")

    return (
        DatabaseMigrationPorts(
            transaction=transaction,
            acquire_lock=lock,
            read_revision=read_revision,
            read_fingerprint=read_fingerprint,
            build_reference_fingerprints=build,
            stamp_revision=stamp,
            upgrade_revision=upgrade,
            verify_runtime_contract=verify,
        ),
        events,
    )


def test_empty_database_upgrades_without_stamp() -> None:
    references = {
        "0001_base": _fingerprint("1", 2),
        "0002_next": _fingerprint("2", 3),
        "0003_head": _fingerprint("3", 4),
    }
    ports, events = _ports(
        revision=RevisionState(False, None),
        target=_fingerprint("0", 0),
        references=references,
    )

    report = migrate_database(_PLAN, ports)

    assert report.previous_revision is None
    assert report.adopted_revision is None
    assert report.legacy_adopted is False
    assert not any(isinstance(event, tuple) and event[0] == "接管" for event in events)
    assert events.index("迁移锁") < events.index(("升级", "0003_head"))
    assert events[-2:] == ["运行契约", "事务结束"]


def test_unversioned_legacy_schema_is_stamped_only_to_unique_exact_revision() -> None:
    references = {
        "0001_base": _fingerprint("1", 2),
        "0002_next": _fingerprint("2", 3),
        "0003_head": _fingerprint("3", 4),
    }
    ports, events = _ports(
        revision=RevisionState(False, None),
        target=references["0002_next"],
        references=references,
    )

    report = migrate_database(_PLAN, ports)

    assert report.adopted_revision == "0002_next"
    assert report.legacy_adopted is True
    assert events.index(("接管", "0002_next")) < events.index(("升级", "0003_head"))


def test_unversioned_drift_never_stamps_or_upgrades() -> None:
    references = {
        "0001_base": _fingerprint("1", 2),
        "0002_next": _fingerprint("2", 3),
        "0003_head": _fingerprint("3", 4),
    }
    ports, events = _ports(
        revision=RevisionState(False, None),
        target=_fingerprint("f", 3),
        references=references,
    )

    with pytest.raises(DatabaseMigrationError, match="无法唯一匹配"):
        migrate_database(_PLAN, ports)

    assert not any(
        isinstance(event, tuple) and event[0] in {"接管", "升级"}
        for event in events
    )


def test_unknown_versioned_revision_fails_before_upgrade() -> None:
    references = {revision: _fingerprint(str(index), index) for index, revision in enumerate(_PLAN.revisions, 1)}
    ports, events = _ports(
        revision=RevisionState(True, "other_branch"),
        target=_fingerprint("a", 1),
        references=references,
    )

    with pytest.raises(DatabaseMigrationError, match="不属于"):
        migrate_database(_PLAN, ports)

    assert not any(isinstance(event, tuple) and event[0] == "升级" for event in events)


def test_post_upgrade_schema_drift_rolls_back_instead_of_reporting_head() -> None:
    references = {
        "0001_base": _fingerprint("1", 2),
        "0002_next": _fingerprint("2", 3),
        "0003_head": _fingerprint("3", 4),
    }
    ports, events = _ports(
        revision=RevisionState(True, "0002_next"),
        target=references["0002_next"],
        references=references,
        head_after_upgrade=_fingerprint("f", 4),
    )

    with pytest.raises(DatabaseMigrationError, match="schema.*head"):
        migrate_database(_PLAN, ports)

    assert "运行契约" not in events
    assert events[-1] == "事务结束"


def test_migration_port_exception_is_redacted() -> None:
    references = {revision: _fingerprint("a", 1) for revision in _PLAN.revisions}
    ports, _events = _ports(
        revision=RevisionState(False, None),
        target=_fingerprint("0", 0),
        references=references,
    )

    def leaking_upgrade(_revision: str) -> None:
        raise RuntimeError("postgresql://user:secret@example.invalid/db")

    object.__setattr__(ports, "upgrade_revision", leaking_upgrade)
    with pytest.raises(DatabaseMigrationError) as captured:
        migrate_database(_PLAN, ports)

    assert "secret" not in str(captured.value)
