"""Pg dependency_state 只读视图的单元契约。"""

from __future__ import annotations

import pytest

from tests.pg_queue_fakes import FakeCursor, unit_queue

_OID_A = "a" * 40
_OID_B = "b" * 40
_OID_64 = "c" * 64


@pytest.mark.parametrize(
    ("row", "target", "expected"),
    [
        ((None, None, None, None), _OID_A, "absent"),
        ((None, None, None, None), _OID_64, "absent"),
        ((f'{{"source":"test","target_commit":"{_OID_A}"}}', None, None, None), _OID_A, "pending"),
        ((None, f'{{"source":"test","target_commit":"{_OID_A}"}}', "claim-1", None), _OID_A, "active"),
        ((f'{{"source":"test","target_commit":"{_OID_B}"}}', None, None, None), _OID_A, "replacement"),
        ((f'{{"source":"test","target_commit":"{_OID_A}"}}', None, None, 10.0), _OID_A, "absent"),
    ],
)
def test_pg_dependency_state_has_exact_four_state_semantics(row, target, expected) -> None:
    queue = unit_queue(FakeCursor(rows=[row]))

    state = queue.dependency_state(
        "demo",
        "codegraph",
        target,
        timeout_sec=0.2,
    )

    assert state == expected
    sql, params = queue._pool._conn.calls[0]
    assert sql.lstrip().upper().startswith("SELECT")
    assert "UPDATE" not in sql.upper()
    assert params == ("demo", "codegraph")


def test_pg_dependency_state_rejects_invalid_timeout_before_sql() -> None:
    queue = unit_queue()

    with pytest.raises(ValueError, match="timeout_sec"):
        queue.dependency_state(
            "demo",
            "codegraph",
            _OID_A,
            timeout_sec=0.0,
        )

    assert queue._pool._conn.calls == []


@pytest.mark.parametrize(
    "target",
    ["abc", "A" * 40, "0" * 40, "0" * 64],
)
def test_pg_dependency_state_rejects_non_canonical_target_before_sql(target) -> None:
    queue = unit_queue()

    with pytest.raises(ValueError, match="target_commit"):
        queue.dependency_state(
            "demo",
            "codegraph",
            target,
            timeout_sec=0.2,
        )

    assert queue._pool._conn.calls == []
