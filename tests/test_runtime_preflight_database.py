"""运行时 PG queue 只读 schema 与权限预检测试。"""

from __future__ import annotations

from dataclasses import dataclass
import sys
from types import SimpleNamespace

import pytest

from codev_platform.reindex.pg_queue_sql import (
    QUEUE_COLUMN_CONTRACT,
    QUEUE_PRIMARY_KEY,
)
from codev_platform.runtime_preflight_contract import ProbeDeadline
from codev_platform.runtime_preflight_database import probe_pg_queue_state_readonly


@dataclass
class _Scenario:
    columns_ok: bool = True
    primary_ok: bool = True
    index_ok: bool = True
    table_safety: bool = True
    schema_privilege: bool = True
    table_privilege: bool = True


def _install_fake_psycopg(
    monkeypatch: pytest.MonkeyPatch,
    scenario: _Scenario,
) -> list[object]:
    events: list[object] = []

    class Cursor:
        statement = ""

        def execute(self, statement: str, params: tuple[object, ...] = ()) -> None:
            self.statement = statement
            events.append(("execute", statement, params))

        def fetchone(self):
            events.append("fetchone")
            if self.statement == "SELECT current_schema()":
                return ("public",)
            if "has_schema_privilege" in self.statement:
                return (scenario.schema_privilege,)
            if "idx.indisprimary" in self.statement and not self.statement.startswith(
                "SELECT EXISTS"
            ):
                return (list(QUEUE_PRIMARY_KEY) if scenario.primary_ok else ["project_id"],)
            if "relrowsecurity" in self.statement:
                return (scenario.table_safety,)
            if self.statement.startswith("SELECT EXISTS"):
                return (scenario.index_ok,)
            if "has_table_privilege" in self.statement:
                return (scenario.table_privilege,)
            if self.statement.startswith("SELECT status"):
                return None
            raise AssertionError("出现未规划的 fetchone")

        def fetchall(self):
            events.append("fetchall")
            expected = [
                (name, data_type, "YES" if nullable else "NO")
                for name, data_type, nullable in QUEUE_COLUMN_CONTRACT
            ]
            return expected if scenario.columns_ok else expected[:-1]

        def close(self) -> None:
            events.append("cursor_close")

    class Connection:
        def cursor(self) -> Cursor:
            events.append("cursor")
            return Cursor()

        def rollback(self) -> None:
            events.append("rollback")

        def close(self) -> None:
            events.append("connection_close")

    def connect(dsn: str, **kwargs: object) -> Connection:
        events.append(("connect", dsn, kwargs))
        return Connection()

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=connect))
    return events


def _probe(monkeypatch: pytest.MonkeyPatch, scenario: _Scenario) -> list[object]:
    events = _install_fake_psycopg(monkeypatch, scenario)
    deadline = ProbeDeadline.after(30.0, lambda: 0.0)
    probe_pg_queue_state_readonly(
        "postgresql://worker:secret@db.example/platform",
        deadline,
    )
    return events


def test_pg_probe_is_readonly_and_proves_schema_index_and_dml_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = _probe(monkeypatch, _Scenario())
    statements = [event[1] for event in events if isinstance(event, tuple) and event[0] == "execute"]

    assert statements[:3] == [
        "SET TRANSACTION READ ONLY",
        "SET LOCAL statement_timeout = '5000ms'",
        "SET LOCAL lock_timeout = '5000ms'",
    ]
    assert any("information_schema.columns" in statement for statement in statements)
    assert any("pg_catalog.pg_index" in statement for statement in statements)
    assert any("has_table_privilege" in statement for statement in statements)
    assert not any(
        statement.lstrip().upper().startswith(("CREATE ", "ALTER ", "INSERT ", "UPDATE ", "DELETE "))
        for statement in statements
    )
    assert events[-3:] == ["cursor_close", "rollback", "connection_close"]


@pytest.mark.parametrize(
    "scenario",
    [
        _Scenario(columns_ok=False),
        _Scenario(primary_ok=False),
        _Scenario(index_ok=False),
        _Scenario(table_safety=False),
        _Scenario(schema_privilege=False),
        _Scenario(table_privilege=False),
    ],
)
def test_pg_probe_fails_closed_and_always_rolls_back(
    monkeypatch: pytest.MonkeyPatch,
    scenario: _Scenario,
) -> None:
    events = _install_fake_psycopg(monkeypatch, scenario)
    deadline = ProbeDeadline.after(30.0, lambda: 0.0)

    with pytest.raises(OSError):
        probe_pg_queue_state_readonly(
            "postgresql://worker:secret@db.example/platform",
            deadline,
        )

    assert events[-3:] == ["cursor_close", "rollback", "connection_close"]
