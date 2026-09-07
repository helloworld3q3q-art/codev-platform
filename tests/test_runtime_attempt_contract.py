"""部署尝试预留与冻结契约测试。"""

from __future__ import annotations

import dataclasses
import json

import pytest

import codev_platform.runtime_attempt_contract as contract
from codev_platform.runtime_attempt_contract import (
    AttemptContractError,
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
    decode_attempt_reservation,
    decode_deployment_attempt_for_reservation,
    decode_deployment_attempt,
    encode_attempt_reservation,
    encode_deployment_attempt,
    freeze_deployment_attempt,
    reserve_deployment_attempt,
    verify_frozen_attempt,
)
from tests.runtime_contract_malicious_support import strict_json_mutations


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="b" * 64,
        controller_sha256="c" * 64,
        created_at="2026-07-19T10:00:00Z",
    )


def _attempt(reservation: AttemptReservation | None = None) -> DeploymentAttempt:
    return freeze_deployment_attempt(
        _reservation() if reservation is None else reservation,
        target_generation_id="d" * 64,
        baseline_generation_id="e" * 64,
        baseline_observation_sha256="f" * 64,
    )


def test_attempt模型字段冻结且操作值精确() -> None:
    assert tuple(item.value for item in AttemptOperation) == ("deploy", "rollback")
    assert tuple(field.name for field in dataclasses.fields(AttemptReservation)) == (
        "schema_version",
        "attempt_id",
        "operation",
        "plan_sha256",
        "controller_sha256",
        "created_at",
    )
    assert tuple(field.name for field in dataclasses.fields(DeploymentAttempt)) == (
        "schema_version",
        "attempt_id",
        "operation",
        "target_generation_id",
        "baseline_generation_id",
        "baseline_observation_sha256",
        "plan_sha256",
        "controller_sha256",
        "created_at",
    )
    assert AttemptReservation.__dataclass_params__.frozen is True
    assert DeploymentAttempt.__dataclass_params__.frozen is True
    assert "__dict__" not in DeploymentAttempt.__slots__


def test同一目标generation每次人工部署都创建独立attempt(monkeypatch) -> None:
    values = iter(("1" * 32, "2" * 32))
    monkeypatch.setattr(contract.secrets, "token_hex", lambda size: next(values))

    first = reserve_deployment_attempt(
        operation=AttemptOperation.DEPLOY,
        plan_sha256="a" * 64,
        controller_sha256="b" * 64,
        created_at="2026-07-19T10:00:00Z",
    )
    second = reserve_deployment_attempt(
        operation=AttemptOperation.DEPLOY,
        plan_sha256="a" * 64,
        controller_sha256="b" * 64,
        created_at="2026-07-19T10:00:01Z",
    )
    first_attempt = _attempt(first)
    second_attempt = _attempt(second)

    assert first.attempt_id == "1" * 32
    assert second.attempt_id == "2" * 32
    assert first_attempt.target_generation_id == second_attempt.target_generation_id
    assert first_attempt.attempt_id != second_attempt.attempt_id


def test冻结attempt只能继承预留记录身份() -> None:
    reservation = _reservation()
    attempt = _attempt(reservation)

    assert attempt.attempt_id == reservation.attempt_id
    assert attempt.operation is reservation.operation
    assert attempt.plan_sha256 == reservation.plan_sha256
    assert attempt.controller_sha256 == reservation.controller_sha256
    assert attempt.created_at == reservation.created_at


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("attempt_id", "1" * 32),
        ("operation", AttemptOperation.ROLLBACK),
        ("plan_sha256", "2" * 64),
        ("controller_sha256", "3" * 64),
        ("created_at", "2026-07-19T10:00:01Z"),
    ),
)
def test冻结attempt复验拒绝任何预留身份漂移(field: str, value: object) -> None:
    reservation = _reservation()
    changed_reservation = dataclasses.replace(reservation, **{field: value})
    changed_attempt = _attempt(changed_reservation)

    with pytest.raises(AttemptContractError, match="预留记录"):
        verify_frozen_attempt(reservation, changed_attempt)


def test绑定解码必须复验原预留记录() -> None:
    reservation = _reservation()
    attempt = _attempt(dataclasses.replace(reservation, plan_sha256="2" * 64))

    with pytest.raises(AttemptContractError, match="预留记录"):
        decode_deployment_attempt_for_reservation(
            reservation,
            encode_deployment_attempt(attempt),
        )


def test_attempt两阶段记录严格往返() -> None:
    reservation = _reservation()
    attempt = _attempt(reservation)

    assert decode_attempt_reservation(encode_attempt_reservation(reservation)) == reservation
    assert decode_deployment_attempt(encode_deployment_attempt(attempt)) == attempt


@pytest.mark.parametrize("attempt_id", ["", "a" * 31, "A" * 32, "g" * 32, "0" * 32])
def test_attempt_id拒绝非规范值(attempt_id: str) -> None:
    with pytest.raises(AttemptContractError):
        dataclasses.replace(_reservation(), attempt_id=attempt_id)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"operation": "deploy"},
        {"plan_sha256": "0" * 64},
        {"controller_sha256": "A" * 64},
        {"created_at": "2026-07-19T10:00:00+00:00"},
        {"created_at": "2026-02-30T10:00:00Z"},
    ],
)
def test_reservation非法字段fail_closed(changes: dict[str, object]) -> None:
    with pytest.raises(AttemptContractError):
        dataclasses.replace(_reservation(), **changes)


def test_attempt拒绝目标与基线相同() -> None:
    with pytest.raises(AttemptContractError):
        freeze_deployment_attempt(
            _reservation(),
            target_generation_id="d" * 64,
            baseline_generation_id="d" * 64,
            baseline_observation_sha256="f" * 64,
        )


def test_freeze拒绝伪造reservation类型() -> None:
    with pytest.raises(AttemptContractError):
        freeze_deployment_attempt(
            object(),  # type: ignore[arg-type]
            target_generation_id="d" * 64,
            baseline_generation_id="e" * 64,
            baseline_observation_sha256="f" * 64,
        )


def test_attempt解码拒绝非枚举操作() -> None:
    bad_operation = json.loads(encode_deployment_attempt(_attempt()))
    bad_operation["operation"] = "recover"
    with pytest.raises(AttemptContractError):
        decode_deployment_attempt(json.dumps(bad_operation).encode("utf-8"))


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        pytest.param(decoder, payload, id=f"{name}-{mutation}")
        for name, decoder, encoded in (
            (
                "reservation",
                decode_attempt_reservation,
                encode_attempt_reservation(_reservation()),
            ),
            (
                "attempt",
                decode_deployment_attempt,
                encode_deployment_attempt(_attempt()),
            ),
        )
        for mutation, payload in strict_json_mutations(
            encoded,
            max_bytes=16_384,
            wrong_field="operation",
        )
    ],
)
def test_attempt每个decoder逐项拒绝单变量恶意载荷(decoder, payload: object) -> None:
    with pytest.raises(AttemptContractError):
        decoder(payload)  # type: ignore[arg-type]


def test_attempt随机源固定请求16bytes(monkeypatch) -> None:
    requested: list[int] = []

    def fake_token_hex(size: int) -> str:
        requested.append(size)
        return "1" * 32

    monkeypatch.setattr(contract.secrets, "token_hex", fake_token_hex)
    reserve_deployment_attempt(
        operation=AttemptOperation.DEPLOY,
        plan_sha256="a" * 64,
        controller_sha256="b" * 64,
        created_at="2026-07-19T10:00:00Z",
    )

    assert requested == [16]


@pytest.mark.parametrize("candidate", ["a" * 31, "A" * 32, "0" * 32, object()])
def test随机源必须返回恰好128位非零小写十六进制(monkeypatch, candidate: object) -> None:
    monkeypatch.setattr(contract.secrets, "token_hex", lambda _size: candidate)
    with pytest.raises(AttemptContractError):
        reserve_deployment_attempt(
            operation=AttemptOperation.DEPLOY,
            plan_sha256="a" * 64,
            controller_sha256="b" * 64,
            created_at="2026-07-19T10:00:00Z",
        )
