"""控制租约、serving 围栏与 capability 泄漏门禁测试。"""

from __future__ import annotations

import dataclasses
import logging

import pytest

import codev_platform.runtime_fencing as fencing
from codev_platform.core.runtime_models import RuntimeModelError, canonical_json_bytes
from codev_platform.runtime_attempt_contract import attempt_reservation_sha256
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ControlLeaseStatus,
    FencingContractError,
    ServingFenceProof,
    ServingFenceRecord,
    authorize_serving_writer,
    decode_control_lease_record,
    decode_serving_fence_record,
    encode_control_lease_record,
    encode_serving_fence_record,
    recover_control_lease,
    verify_control_lease,
    verify_serving_fence,
)
from codev_platform.runtime_generation_state import GenerationMode
from tests.runtime_contract_malicious_support import strict_json_mutations
from tests.runtime_fencing_test_support import (
    OTHER_TOKEN,
    TOKEN,
    _control,
    _reservation,
    _serving,
    _state_for_fence,
)


def test_fencing模型字段冻结且proof不显示原始token() -> None:
    assert tuple(status.value for status in ControlLeaseStatus) == (
        "active",
        "retired",
    )
    assert tuple(field.name for field in dataclasses.fields(ControlLeaseRecord)) == (
        "schema_version",
        "attempt_id",
        "reservation_sha256",
        "epoch",
        "token_sha256",
        "owner",
        "status",
        "issued_at",
        "predecessor_sha256",
        "terminal_journal_sha256",
        "terminal_evidence_sha256",
        "retired_at",
        "retired_from_sha256",
    )
    assert tuple(field.name for field in dataclasses.fields(ControlLeaseProof)) == (
        "attempt_id",
        "epoch",
        "token",
    )
    assert tuple(field.name for field in dataclasses.fields(ServingFenceRecord)) == (
        "schema_version",
        "fence_id",
        "generation_id",
        "accepted_attempt_id",
        "epoch",
        "token_sha256",
        "issued_at",
    )
    assert tuple(field.name for field in dataclasses.fields(ServingFenceProof)) == (
        "fence_id",
        "epoch",
        "token",
    )
    for model in (
        ControlLeaseRecord,
        ControlLeaseProof,
        ServingFenceRecord,
        ServingFenceProof,
    ):
        assert model.__dataclass_params__.frozen is True
        assert "__dict__" not in model.__slots__

    control = ControlLeaseProof(attempt_id="a" * 32, epoch=3, token=TOKEN)
    serving = ServingFenceProof(fence_id="fence-a", epoch=4, token=TOKEN)
    token_hex = TOKEN.hex()
    assert token_hex not in repr(control)
    assert token_hex not in repr(serving)
    assert control.token_sha256 == (
        "630dcd2966c4336691125448bbb25b4ff412a49c732db2c8abc1b8581bd710dd"
    )
    assert serving.token_sha256 == control.token_sha256


def test_control_lease签发验证退役并保留终态审计() -> None:
    record, proof = _control()
    assert record.schema_version == 1
    assert record.attempt_id == _reservation().attempt_id
    assert record.reservation_sha256 == attempt_reservation_sha256(_reservation())
    assert record.epoch == proof.epoch == 4
    assert record.token_sha256 == proof.token_sha256
    assert record.status is ControlLeaseStatus.ACTIVE
    assert verify_control_lease(record, proof) is proof

    retired = dataclasses.replace(
        record,
        status=ControlLeaseStatus.RETIRED,
        terminal_journal_sha256="6" * 64,
        terminal_evidence_sha256="7" * 64,
        retired_at="2026-07-19T10:10:00Z",
        retired_from_sha256=fencing.control_lease_record_sha256(record),
    )
    assert retired.status is ControlLeaseStatus.RETIRED
    assert retired.terminal_journal_sha256 == "6" * 64
    assert retired.terminal_evidence_sha256 == "7" * 64
    with pytest.raises(FencingContractError, match="活动"):
        verify_control_lease(retired, proof)


def test_recovery只可接管同一活动attempt且epoch严格提升() -> None:
    record, _ = _control()
    recovered, proof = recover_control_lease(
        record,
        _reservation(),
        epoch=5,
        token=OTHER_TOKEN,
        owner="recovery-a",
        issued_at="2026-07-19T10:05:00Z",
    )
    assert recovered.attempt_id == record.attempt_id
    assert recovered.epoch == proof.epoch == 5
    assert recovered.token_sha256 != record.token_sha256
    assert record.predecessor_sha256 is None
    assert recovered.predecessor_sha256 == fencing.control_lease_record_sha256(record)

    with pytest.raises(FencingContractError, match="提升"):
        recover_control_lease(
            record,
            _reservation(),
            epoch=4,
            token=OTHER_TOKEN,
            owner="recovery-a",
            issued_at="2026-07-19T10:05:00Z",
        )
    other_reservation = _reservation(attempt_id="b" * 32)
    with pytest.raises(FencingContractError, match="同一.*attempt"):
        recover_control_lease(
            record,
            other_reservation,
            epoch=5,
            token=OTHER_TOKEN,
            owner="recovery-a",
            issued_at="2026-07-19T10:05:00Z",
        )


def test_recovery接管签发时间必须严格晚于当前lease() -> None:
    record, _ = _control()

    with pytest.raises(FencingContractError, match="时间|晚于"):
        recover_control_lease(
            record,
            _reservation(),
            epoch=5,
            token=OTHER_TOKEN,
            owner="recovery-a",
            issued_at=record.issued_at,
        )


def test_control_lease接管本身不改变state或关闭线上writer() -> None:
    control_record, _ = _control()
    serving_record, serving_proof = _serving()
    published = _state_for_fence(
        serving_record,
        mode=GenerationMode.STEADY,
        maintenance_active=False,
    )
    before_sha256 = canonical_json_bytes(published)

    recovered, _ = recover_control_lease(
        control_record,
        _reservation(),
        epoch=5,
        token=OTHER_TOKEN,
        owner="recovery-a",
        issued_at="2026-07-19T10:05:00Z",
    )

    assert recovered.epoch == 5
    assert canonical_json_bytes(published) == before_sha256
    assert (
        authorize_serving_writer(
            published,
            serving_record.generation_id,
            serving_record,
            serving_proof,
        )
        is serving_proof
    )


@pytest.mark.parametrize(
    ("proof", "message"),
    (
        (ControlLeaseProof(attempt_id="a" * 32, epoch=3, token=TOKEN), "epoch"),
        (ControlLeaseProof(attempt_id="a" * 32, epoch=4, token=OTHER_TOKEN), "token"),
        (ServingFenceProof(fence_id="serving-fence-b", epoch=4, token=TOKEN), "ControlLeaseProof"),
    ),
)
def test_control_verifier拒绝旧epoch错误token和serving_proof(
    proof: object,
    message: str,
) -> None:
    with pytest.raises(FencingContractError, match=message):
        verify_control_lease(_control()[0], proof)  # type: ignore[arg-type]


def test_verifier使用常量时间比较(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def compare(left: str, right: str) -> bool:
        calls.append((left, right))
        return left == right

    monkeypatch.setattr(fencing.hmac, "compare_digest", compare)
    record, proof = _control()
    verify_control_lease(record, proof)
    serving_record, serving_proof = _serving()
    verify_serving_fence(serving_record, serving_proof)
    assert calls == [
        (record.token_sha256, proof.token_sha256),
        (serving_record.token_sha256, serving_proof.token_sha256),
    ]


def test_persistent_codec拒绝proof且token不进入repr异常日志或canonical(caplog) -> None:
    control_record, control_proof = _control()
    serving_record, serving_proof = _serving()
    token_markers = (repr(TOKEN), TOKEN.hex(), repr(OTHER_TOKEN), OTHER_TOKEN.hex())

    for proof in (control_proof, serving_proof):
        with pytest.raises(RuntimeModelError) as caught:
            canonical_json_bytes(proof)
        with pytest.raises(RuntimeModelError):
            canonical_json_bytes(dataclasses.asdict(proof))
        assert all(marker not in str(caught.value) for marker in token_markers)
        with caplog.at_level(logging.WARNING):
            logging.getLogger("runtime-fencing-test").warning("proof=%r", proof)
    assert all(marker not in caplog.text for marker in token_markers)
    with pytest.raises(RuntimeModelError):
        canonical_json_bytes({"capability": TOKEN})
    assert not hasattr(fencing, "encode_control_lease_proof")
    assert not hasattr(fencing, "encode_serving_fence_proof")
    for encoder, proof in (
        (encode_control_lease_record, control_proof),
        (encode_serving_fence_record, serving_proof),
    ):
        with pytest.raises(FencingContractError):
            encoder(proof)  # type: ignore[arg-type]
    for payload in (
        encode_control_lease_record(control_record),
        encode_serving_fence_record(serving_record),
    ):
        assert all(marker.encode("ascii") not in payload for marker in token_markers)


def test_fencing公开记录严格往返() -> None:
    control_record, _ = _control()
    serving_record, _ = _serving()
    assert (
        decode_control_lease_record(encode_control_lease_record(control_record)) == control_record
    )
    assert (
        decode_serving_fence_record(encode_serving_fence_record(serving_record)) == serving_record
    )


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        pytest.param(decoder, payload, id=f"{name}-{mutation}")
        for name, decoder, encoded, wrong_field in (
            (
                "control",
                decode_control_lease_record,
                encode_control_lease_record(_control()[0]),
                "status",
            ),
            (
                "serving",
                decode_serving_fence_record,
                encode_serving_fence_record(_serving()[0]),
                "fence_id",
            ),
        )
        for mutation, payload in strict_json_mutations(
            encoded,
            max_bytes=16_384,
            wrong_field=wrong_field,
        )
    ],
)
def test_fencing每个decoder逐项拒绝单变量恶意载荷(
    decoder,
    payload: object,
) -> None:
    with pytest.raises(FencingContractError):
        decoder(payload)  # type: ignore[arg-type]
