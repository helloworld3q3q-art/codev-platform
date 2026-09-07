"""稳定 RecoveryEnvelope 身份、路径与严格 codec 测试。"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    attempt_reservation_sha256,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryContractError,
    RecoveryEnvelope,
    create_recovery_envelope,
    decode_recovery_envelope,
    decode_recovery_envelope_for_context,
    encode_recovery_envelope,
    recovery_envelope_sha256,
    verify_recovery_envelope,
)
from codev_platform.runtime_transaction_contract import create_transaction_journal
from tests.runtime_contract_malicious_support import strict_json_mutations


def _reservation(**changes: object) -> AttemptReservation:
    values: dict[str, object] = {
        "schema_version": 1,
        "attempt_id": "a" * 32,
        "operation": AttemptOperation.DEPLOY,
        "plan_sha256": "e" * 64,
        "controller_sha256": "f" * 64,
        "created_at": "2026-07-19T10:00:00Z",
    }
    values.update(changes)
    return AttemptReservation(**values)


def _envelope(**changes: object) -> RecoveryEnvelope:
    envelope = create_recovery_envelope(
        _reservation(),
        create_transaction_journal(
            _reservation(),
            created_at="2026-07-19T10:00:30Z",
        ),
        controller_root_relative="controllers/controller-v1",
        interpreter_relative="controllers/controller-v1/venv/bin/python",
        interpreter_sha256="2" * 64,
        transaction_store_id="runtime-transaction-store-v1",
        created_at="2026-07-19T10:01:00Z",
    )
    return dataclasses.replace(envelope, **changes)


def test_recovery_envelope字段精确冻结且不含变化头或命令面() -> None:
    assert tuple(field.name for field in dataclasses.fields(RecoveryEnvelope)) == (
        "schema_version",
        "attempt_id",
        "reservation_sha256",
        "controller_root_relative",
        "controller_tree_sha256",
        "interpreter_relative",
        "interpreter_sha256",
        "transaction_store_id",
        "journal_genesis_sha256",
        "created_at",
    )
    assert RecoveryEnvelope.__dataclass_params__.frozen is True
    assert "__dict__" not in RecoveryEnvelope.__slots__
    forbidden = {
        "journal_head_sha256",
        "control_lease_epoch",
        "module",
        "argv",
        "command",
        "environment",
    }
    assert forbidden.isdisjoint(field.name for field in dataclasses.fields(RecoveryEnvelope))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("controller_root_relative", "/opt/codev/controller"),
        ("controller_root_relative", "C:\\codev\\controller"),
        ("controller_root_relative", "controllers/../escape"),
        ("controller_root_relative", "controllers//controller-v1"),
        ("controller_root_relative", "controllers/current"),
        ("controller_root_relative", "releases/current/controller-v1"),
        ("controller_root_relative", "$CONTROLLER_ROOT"),
        ("controller_root_relative", "controllers/controller-v1;sh"),
        ("controller_root_relative", "controllers/controller v1"),
        ("interpreter_relative", "/usr/bin/python"),
        ("interpreter_relative", "controllers/other/venv/bin/python"),
        ("interpreter_relative", "controllers/controller-v1"),
        ("interpreter_relative", "controllers/controller-v1/../python"),
    ),
)
def test_recovery_envelope拒绝runtime_root外路径shell环境覆盖与current链接(
    field: str,
    value: str,
) -> None:
    with pytest.raises(RecoveryContractError):
        _envelope(**{field: value})


def test_recovery_envelope_factory拒绝runtime_root本身() -> None:
    reservation = _reservation()
    journal = create_transaction_journal(
        reservation,
        created_at="2026-07-19T10:00:30Z",
    )
    with pytest.raises(RecoveryContractError):
        create_recovery_envelope(
            reservation,
            journal,
            controller_root_relative=".",
            interpreter_relative="controllers/controller-v1/venv/bin/python",
            interpreter_sha256="2" * 64,
            transaction_store_id="runtime-transaction-store-v1",
            created_at="2026-07-19T10:01:00Z",
        )


def test_recovery_envelope_decoder拒绝runtime_root本身() -> None:
    payload = json.loads(encode_recovery_envelope(_envelope()))
    payload["controller_root_relative"] = "."
    with pytest.raises(RecoveryContractError):
        decode_recovery_envelope(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    "changes",
    (
        {"controller_tree_sha256": "0" * 64},
        {"interpreter_sha256": "A" * 64},
        {"journal_genesis_sha256": "3" * 63},
        {"transaction_store_id": ""},
        {"transaction_store_id": "store/../../escape"},
        {"transaction_store_id": "store$(id)"},
        {"created_at": "2026-07-19T10:01:00+00:00"},
        {"created_at": "2026-02-30T10:01:00Z"},
    ),
)
def test_recovery_envelope非法字段fail_closed(changes: dict[str, object]) -> None:
    with pytest.raises(RecoveryContractError):
        _envelope(**changes)


def test_recovery_envelope只从冻结reservation派生身份() -> None:
    envelope = _envelope()
    assert envelope.attempt_id == _reservation().attempt_id
    assert envelope.reservation_sha256 == attempt_reservation_sha256(_reservation())
    assert envelope.controller_tree_sha256 == _reservation().controller_sha256
    journal = create_transaction_journal(
        _reservation(),
        created_at="2026-07-19T10:00:30Z",
    )
    assert envelope.journal_genesis_sha256 == journal.journal_genesis_sha256
    assert verify_recovery_envelope(envelope, _reservation(), journal) is envelope
    with pytest.raises(RecoveryContractError, match="AttemptReservation"):
        create_recovery_envelope(  # type: ignore[arg-type]
            object(),
            journal,
            controller_root_relative=envelope.controller_root_relative,
            interpreter_relative=envelope.interpreter_relative,
            interpreter_sha256=envelope.interpreter_sha256,
            transaction_store_id=envelope.transaction_store_id,
            created_at=envelope.created_at,
        )


def test_recovery_envelope复验拒绝reservation与journal_genesis漂移() -> None:
    envelope = _envelope()
    journal = create_transaction_journal(
        _reservation(),
        created_at="2026-07-19T10:00:30Z",
    )
    with pytest.raises(RecoveryContractError, match="reservation"):
        verify_recovery_envelope(
            envelope,
            _reservation(controller_sha256="4" * 64),
            journal,
        )
    other_journal = create_transaction_journal(
        _reservation(attempt_id="b" * 32),
        created_at="2026-07-19T10:00:30Z",
    )
    with pytest.raises(RecoveryContractError, match="TransactionJournal|reservation"):
        verify_recovery_envelope(envelope, _reservation(), other_journal)


def test_recovery_envelope摘要绑定全部稳定持久字段() -> None:
    envelope = _envelope()
    for field, value in (
        ("attempt_id", "b" * 32),
        ("reservation_sha256", "3" * 64),
        ("controller_root_relative", "controllers"),
        ("controller_tree_sha256", "4" * 64),
        ("interpreter_relative", "controllers/controller-v1/bin/python"),
        ("interpreter_sha256", "5" * 64),
        ("transaction_store_id", "runtime-transaction-store-v2"),
        ("journal_genesis_sha256", "6" * 64),
        ("created_at", "2026-07-19T10:02:00Z"),
    ):
        changed = dataclasses.replace(envelope, **{field: value})
        assert recovery_envelope_sha256(changed) != recovery_envelope_sha256(envelope)


def test_recovery_envelope严格往返() -> None:
    envelope = _envelope()
    assert decode_recovery_envelope(encode_recovery_envelope(envelope)) == envelope
    journal = create_transaction_journal(
        _reservation(),
        created_at="2026-07-19T10:00:30Z",
    )
    assert (
        decode_recovery_envelope_for_context(
            _reservation(),
            journal,
            encode_recovery_envelope(envelope),
        )
        == envelope
    )


@pytest.mark.parametrize(
    ("mutation", "payload"),
    [
        pytest.param(name, payload, id=name)
        for name, payload in strict_json_mutations(
            encode_recovery_envelope(_envelope()),
            max_bytes=32_768,
            wrong_field="controller_root_relative",
        )
    ],
)
def test_recovery_envelope_decoder逐项拒绝单变量恶意载荷(
    mutation: str,
    payload: object,
) -> None:
    del mutation
    with pytest.raises(RecoveryContractError):
        decode_recovery_envelope(payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    (
        "journal_head_sha256",
        "control_lease_epoch",
        "module",
        "argv",
        "command",
        "environment",
    ),
)
def test_recovery_envelope_decoder拒绝launcher与变化状态注入(field: str) -> None:
    payload = json.loads(encode_recovery_envelope(_envelope()))
    payload[field] = ["python", "-m", "attacker"] if field == "argv" else "attacker"
    with pytest.raises(RecoveryContractError):
        decode_recovery_envelope(json.dumps(payload).encode("utf-8"))


def test_recovery_contract不反向依赖ops或真实IO() -> None:
    import codev_platform.runtime_recovery_contract as recovery

    source = Path(recovery.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "codev_platform.ops",
        "subprocess",
        "os.environ",
        "systemd",
        "wsl",
    ):
        assert forbidden not in source.lower()
