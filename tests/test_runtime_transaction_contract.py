"""运行代际事务 journal 与崩溃窗口恢复测试。"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
    freeze_deployment_attempt,
    verify_frozen_attempt,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ControlLeaseStatus,
    ServingFenceProof,
    control_lease_record_sha256,
    issue_control_lease,
    recover_control_lease,
)
from codev_platform.runtime_transaction_codec import (
    decode_transaction_action,
    decode_transaction_journal,
    encode_transaction_action,
    encode_transaction_journal,
)
from codev_platform.runtime_transaction_contract import (
    CURRENT_SERVE_PERMIT_RESOURCE_ID,
    TransactionAction,
    TransactionActionIntent,
    TransactionActionState,
    TransactionContractError,
    TransactionJournal,
    TransactionJournalStatus,
    TransactionRecoveryDirective,
    advance_transaction_action,
    append_transaction_action,
    create_transaction_journal,
    prepare_transaction_action,
    transaction_action_key,
    transaction_journal_sha256,
    transaction_recovery_directive,
)
from tests.runtime_contract_malicious_support import strict_json_mutations


TOKEN = bytes(range(32))
OTHER_TOKEN = bytes(range(32, 64))


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="e" * 64,
        controller_sha256="f" * 64,
        created_at="2026-07-19T10:00:00Z",
    )


def _attempt() -> DeploymentAttempt:
    reservation = _reservation()
    return verify_frozen_attempt(
        reservation,
        freeze_deployment_attempt(
            reservation,
            target_generation_id="b" * 64,
            baseline_generation_id="c" * 64,
            baseline_observation_sha256="d" * 64,
        ),
    )


def _control(
    *,
    epoch: int = 4,
    token: bytes = TOKEN,
) -> tuple[ControlLeaseRecord, ControlLeaseProof]:
    return issue_control_lease(
        _reservation(),
        epoch=epoch,
        token=token,
        owner="controller-a",
        issued_at="2026-07-19T10:00:30Z",
    )


def _prepared(
    *,
    control: tuple[ControlLeaseRecord, ControlLeaseProof] | None = None,
    control_lease_lineage: tuple[ControlLeaseRecord, ...] | None = None,
    **changes: object,
) -> TransactionAction:
    values: dict[str, object] = {
        "step_sequence": 1,
        "intent": TransactionActionIntent.REVOKE_CURRENT_SERVE_PERMIT,
        "resource_kind": "serve-permit",
        "resource_id": CURRENT_SERVE_PERMIT_RESOURCE_ID,
        "operation_sha256": "1" * 64,
        "before_sha256": "2" * 64,
        "after_sha256": "3" * 64,
        "recorded_at": "2026-07-19T10:01:00Z",
    }
    values.update(changes)
    return prepare_transaction_action(
        _attempt(),
        *(_control() if control is None else control),
        control_lease_lineage=control_lease_lineage,
        **values,
    )


def _journal() -> TransactionJournal:
    return create_transaction_journal(
        _reservation(),
        created_at="2026-07-19T10:00:30Z",
    )


def _retired_control(
    record: ControlLeaseRecord,
    proof: ControlLeaseProof,
) -> ControlLeaseRecord:
    del proof
    return dataclasses.replace(
        record,
        status=ControlLeaseStatus.RETIRED,
        terminal_journal_sha256="7" * 64,
        terminal_evidence_sha256="8" * 64,
        retired_at="2026-07-19T10:10:00Z",
        retired_from_sha256=control_lease_record_sha256(record),
    )


def test_transaction模型字段状态与冻结属性精确() -> None:
    assert tuple(intent.value for intent in TransactionActionIntent) == (
        "revoke-current-serve-permit",
        "apply-resource",
    )
    assert tuple(state.value for state in TransactionActionState) == (
        "prepared",
        "applied",
        "committed",
    )
    assert tuple(item.value for item in TransactionRecoveryDirective) == (
        "start",
        "reconcile",
        "commit",
        "advance",
        "terminal",
    )
    assert tuple(field.name for field in dataclasses.fields(TransactionAction)) == (
        "schema_version",
        "attempt_id",
        "reservation_sha256",
        "step_sequence",
        "resource_kind",
        "resource_id",
        "intent",
        "operation_sha256",
        "before_sha256",
        "after_sha256",
        "state",
        "control_lease_epoch_audit",
        "control_token_sha256_audit",
        "control_lease_record_sha256_audit",
        "recorded_at",
    )
    assert tuple(field.name for field in dataclasses.fields(TransactionJournal)) == (
        "schema_version",
        "attempt_id",
        "reservation_sha256",
        "journal_genesis_sha256",
        "status",
        "actions",
        "terminal_evidence_sha256",
        "created_at",
        "updated_at",
        "completed_at",
    )
    models = (TransactionAction, TransactionJournal)
    assert all(model.__dataclass_params__.frozen for model in models)
    assert all("__dict__" not in model.__slots__ for model in models)


def test事务校验模块可从独立入口导入() -> None:
    """模型门面与校验实现不得形成冷启动循环依赖。"""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import codev_platform.runtime_transaction_contract_validation",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_action键只由attempt步骤资源种类和资源ID组成() -> None:
    action = _prepared()
    for field, value in (
        ("attempt_id", "b" * 32),
        ("step_sequence", 2),
        ("resource_kind", "configuration"),
        ("resource_id", "platform/config.json"),
    ):
        changed = dataclasses.replace(action, **{field: value})
        assert transaction_action_key(changed) != transaction_action_key(action)
    for field, value in (
        ("intent", TransactionActionIntent.APPLY_RESOURCE),
        ("operation_sha256", "4" * 64),
        ("state", TransactionActionState.APPLIED),
        ("control_lease_epoch_audit", 5),
        ("control_token_sha256_audit", "5" * 64),
        ("control_lease_record_sha256_audit", "6" * 64),
        ("recorded_at", "2026-07-19T10:02:00Z"),
    ):
        changed = dataclasses.replace(action, **{field: value})
        assert transaction_action_key(changed) == transaction_action_key(action)


def test_journal_genesis绑定reservation且append只追加合法状态边() -> None:
    journal = _journal()
    prepared = _prepared()
    assert prepared.control_lease_record_sha256_audit == control_lease_record_sha256(_control()[0])
    after_prepared = append_transaction_action(journal, prepared, *_control())
    applied = advance_transaction_action(
        prepared,
        TransactionActionState.APPLIED,
        *_control(),
        recorded_at="2026-07-19T10:02:00Z",
    )
    after_applied = append_transaction_action(after_prepared, applied, *_control())
    committed = advance_transaction_action(
        applied,
        TransactionActionState.COMMITTED,
        *_control(),
        recorded_at="2026-07-19T10:03:00Z",
    )
    after_committed = append_transaction_action(after_applied, committed, *_control())
    assert tuple(action.state for action in after_committed.actions) == (
        TransactionActionState.PREPARED,
        TransactionActionState.APPLIED,
        TransactionActionState.COMMITTED,
    )
    assert after_committed.journal_genesis_sha256 == journal.journal_genesis_sha256
    assert transaction_journal_sha256(after_committed) != transaction_journal_sha256(journal)


def test_journal重复append幂等且拒绝跳步回退或身份漂移() -> None:
    prepared = _prepared()
    journal = append_transaction_action(_journal(), prepared, *_control())
    retry = dataclasses.replace(prepared, recorded_at="2026-07-19T10:01:30Z")
    assert append_transaction_action(journal, retry, *_control()) is journal

    skipped = dataclasses.replace(prepared, state=TransactionActionState.COMMITTED)
    with pytest.raises(TransactionContractError, match="状态边"):
        append_transaction_action(journal, skipped, *_control())
    drifted = dataclasses.replace(prepared, operation_sha256="4" * 64)
    with pytest.raises(TransactionContractError, match="身份"):
        append_transaction_action(journal, drifted, *_control())
    drifted_intent = dataclasses.replace(
        prepared,
        intent=TransactionActionIntent.APPLY_RESOURCE,
    )
    with pytest.raises(TransactionContractError, match="身份"):
        append_transaction_action(journal, drifted_intent, *_control())


@pytest.mark.parametrize(
    ("window", "latest_state", "directive"),
    (
        ("prepared-fsynced-resource-unchanged", TransactionActionState.PREPARED, "reconcile"),
        ("resource-changed-applied-not-fsynced", TransactionActionState.PREPARED, "reconcile"),
        ("applied-fsynced-committed-not-fsynced", TransactionActionState.APPLIED, "commit"),
        ("committed-fsynced-next-not-started", TransactionActionState.COMMITTED, "advance"),
    ),
)
def test_journal四崩溃窗口恢复绝不直接重放非幂等副作用(
    window: str,
    latest_state: TransactionActionState,
    directive: str,
) -> None:
    del window
    prepared = _prepared()
    journal = append_transaction_action(_journal(), prepared, *_control())
    if latest_state is not TransactionActionState.PREPARED:
        applied = advance_transaction_action(
            prepared,
            TransactionActionState.APPLIED,
            *_control(),
            recorded_at="2026-07-19T10:02:00Z",
        )
        journal = append_transaction_action(journal, applied, *_control())
    if latest_state is TransactionActionState.COMMITTED:
        committed = advance_transaction_action(
            applied,
            TransactionActionState.COMMITTED,
            *_control(),
            recorded_at="2026-07-19T10:03:00Z",
        )
        journal = append_transaction_action(journal, committed, *_control())

    decision = transaction_recovery_directive(journal)
    assert decision.value == directive
    assert "replay" not in tuple(item.value for item in TransactionRecoveryDirective)


def test_journal空头只允许开始新动作且下一步骤必须连续() -> None:
    journal = _journal()
    assert transaction_recovery_directive(journal) is TransactionRecoveryDirective.START
    with pytest.raises(TransactionContractError, match="步骤"):
        append_transaction_action(
            journal,
            _prepared(step_sequence=2),
            *_control(),
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"intent": TransactionActionIntent.APPLY_RESOURCE},
        {"resource_kind": "systemd-unit"},
        {"resource_id": "unrelated-permit"},
    ),
)
def test_journal第一个入口动作必须类型化撤销当前serve_permit(
    changes: dict[str, object],
) -> None:
    wrong_first = dataclasses.replace(_prepared(), **changes)
    with pytest.raises(TransactionContractError, match="permit"):
        append_transaction_action(_journal(), wrong_first, *_control())


def test_journal读取历史拒绝前一动作未提交就开始下一资源() -> None:
    first = _prepared()
    second = _prepared(
        step_sequence=2,
        resource_kind="configuration",
        resource_id="platform/config.json",
        operation_sha256="4" * 64,
        before_sha256="5" * 64,
        after_sha256="6" * 64,
        recorded_at="2026-07-19T10:02:00Z",
    )
    journal = _journal()

    with pytest.raises(TransactionContractError, match="COMMITTED"):
        dataclasses.replace(
            journal,
            actions=(first, second),
            updated_at=second.recorded_at,
        )


def test_journal_append拒绝与动作审计不一致的更高lease() -> None:
    record, proof = _control()
    recovered = recover_control_lease(
        record,
        _reservation(),
        epoch=5,
        token=OTHER_TOKEN,
        owner="recovery-a",
        issued_at="2026-07-19T10:01:30Z",
    )
    with pytest.raises(TransactionContractError, match="epoch"):
        append_transaction_action(
            _journal(),
            _prepared(control=(record, proof)),
            *recovered,
        )


def test_journal相邻动作与读取历史都拒绝control_epoch倒退() -> None:
    record, _ = _control()
    recovered = recover_control_lease(
        record,
        _reservation(),
        epoch=5,
        token=OTHER_TOKEN,
        owner="recovery-a",
        issued_at="2026-07-19T10:01:30Z",
    )
    prepared = _prepared(
        control=recovered,
        control_lease_lineage=(record, recovered[0]),
        recorded_at="2026-07-19T10:02:00Z",
    )
    with pytest.raises(TransactionContractError, match="epoch"):
        advance_transaction_action(
            prepared,
            TransactionActionState.APPLIED,
            *_control(),
            recorded_at="2026-07-19T10:03:00Z",
        )

    regressed = dataclasses.replace(
        prepared,
        state=TransactionActionState.APPLIED,
        control_lease_epoch_audit=4,
        recorded_at="2026-07-19T10:03:00Z",
    )
    journal = _journal()
    with pytest.raises(TransactionContractError, match="epoch"):
        dataclasses.replace(
            journal,
            actions=(prepared, regressed),
            updated_at=regressed.recorded_at,
        )


def test_action与journal严格往返() -> None:
    action = _prepared()
    journal = append_transaction_action(_journal(), action, *_control())
    assert decode_transaction_action(encode_transaction_action(action)) == action
    assert decode_transaction_journal(encode_transaction_journal(journal)) == journal


def test_journal撤销当前permit提交后可追加普通资源动作() -> None:
    record, proof = _control()
    prepared = _prepared(control=(record, proof))
    journal = append_transaction_action(_journal(), prepared, record, proof)
    for state, recorded_at in (
        (TransactionActionState.APPLIED, "2026-07-19T10:02:00Z"),
        (TransactionActionState.COMMITTED, "2026-07-19T10:03:00Z"),
    ):
        prepared = advance_transaction_action(
            prepared,
            state,
            record,
            proof,
            recorded_at=recorded_at,
        )
        journal = append_transaction_action(journal, prepared, record, proof)
    ordinary = _prepared(
        control=(record, proof),
        step_sequence=2,
        intent=TransactionActionIntent.APPLY_RESOURCE,
        resource_kind="configuration",
        resource_id="platform/config.json",
        operation_sha256="4" * 64,
        before_sha256="5" * 64,
        after_sha256="6" * 64,
        recorded_at="2026-07-19T10:04:00Z",
    )
    journal = append_transaction_action(journal, ordinary, record, proof)
    assert journal.actions[-1].intent is TransactionActionIntent.APPLY_RESOURCE
    assert decode_transaction_action(encode_transaction_action(ordinary)) == ordinary


def test_transaction_action_decoder拒绝非法intent() -> None:
    payload = json.loads(encode_transaction_action(_prepared()))
    payload["intent"] = "revoke-current-serve-permit "
    with pytest.raises(TransactionContractError):
        decode_transaction_action(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        pytest.param(decoder, payload, id=f"{name}-{mutation}")
        for name, decoder, encoded, wrong_field, max_bytes in (
            (
                "action",
                decode_transaction_action,
                encode_transaction_action(_prepared()),
                "state",
                32_768,
            ),
            (
                "journal",
                decode_transaction_journal,
                encode_transaction_journal(
                    append_transaction_action(_journal(), _prepared(), *_control())
                ),
                "actions",
                1_048_576,
            ),
        )
        for mutation, payload in strict_json_mutations(
            encoded,
            max_bytes=max_bytes,
            wrong_field=wrong_field,
        )
    ],
)
def test_transaction每个decoder逐项拒绝单变量恶意载荷(
    decoder,
    payload: object,
) -> None:
    with pytest.raises(TransactionContractError):
        decoder(payload)  # type: ignore[arg-type]


def test_journal_decoder拒绝嵌套action未知字段() -> None:
    journal = append_transaction_action(_journal(), _prepared(), *_control())
    payload = json.loads(encode_transaction_journal(journal))
    payload["actions"][0]["capability"] = TOKEN.hex()
    with pytest.raises(TransactionContractError):
        decode_transaction_journal(json.dumps(payload).encode("utf-8"))


def _invoke_journal_write(
    entry: str,
    record: ControlLeaseRecord,
    proof: object,
) -> object:
    prepared = _prepared()
    if entry == "prepare":
        return _prepared(control=(record, proof))  # type: ignore[arg-type]
    if entry == "advance":
        return advance_transaction_action(
            prepared,
            TransactionActionState.APPLIED,
            record,
            proof,
            recorded_at="2026-07-19T10:02:00Z",
        )
    return append_transaction_action(_journal(), prepared, record, proof)


@pytest.mark.parametrize("entry", ("prepare", "advance", "append"))
def test_journal每个写入口都拒绝未锚定或非活动lease(entry: str) -> None:
    record, proof = _control()
    retired = _retired_control(record, proof)
    invalid_pairs = (
        (record, ControlLeaseProof(proof.attempt_id, proof.epoch, OTHER_TOKEN)),
        (retired, proof),
        (record, ServingFenceProof("serving-fence-x", proof.epoch, TOKEN)),
    )
    for invalid_record, invalid_proof in invalid_pairs:
        with pytest.raises(TransactionContractError):
            _invoke_journal_write(entry, invalid_record, invalid_proof)


def test_journal更高epoch只接受真实recovery并从record持久审计() -> None:
    record, proof = _control()
    prepared = _prepared(control=(record, proof))
    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=5,
        token=OTHER_TOKEN,
        owner="recovery-a",
        issued_at="2026-07-19T10:01:30Z",
    )
    applied = advance_transaction_action(
        prepared,
        TransactionActionState.APPLIED,
        recovered_record,
        recovered_proof,
        recorded_at="2026-07-19T10:02:00Z",
        control_lease_lineage=(record, recovered_record),
    )
    journal = append_transaction_action(_journal(), prepared, record, proof)
    journal = append_transaction_action(
        journal,
        applied,
        recovered_record,
        recovered_proof,
        control_lease_lineage=(record, recovered_record),
    )
    assert applied.control_lease_epoch_audit == recovered_record.epoch
    assert applied.control_token_sha256_audit == recovered_record.token_sha256
    assert applied.control_lease_record_sha256_audit == control_lease_record_sha256(
        recovered_record
    )
    assert journal.actions[-1] == applied


def test_journal拒绝epoch_token正确但完整lease摘要漂移的动作审计() -> None:
    record, proof = _control()
    prepared = _prepared(control=(record, proof))
    drifted = dataclasses.replace(
        prepared,
        control_lease_record_sha256_audit="9" * 64,
    )

    with pytest.raises(TransactionContractError, match="摘要|lease"):
        append_transaction_action(_journal(), drifted, record, proof)


def test_journal_live_advance拒绝同epoch同token的完整lease记录漂移() -> None:
    """owner 等非 capability 字段也受完整 record 审计锚点保护。"""
    record, proof = _control()
    prepared = _prepared(control=(record, proof))
    drifted_record = dataclasses.replace(record, owner="controller-b")

    with pytest.raises(TransactionContractError, match="完整 lease"):
        advance_transaction_action(
            prepared,
            TransactionActionState.APPLIED,
            drifted_record,
            proof,
            recorded_at="2026-07-19T10:02:00Z",
        )


def test_journal写入口拒绝早于control_lease签发的动作时间() -> None:
    """动作时间既要单调，也必须位于租约生效之后。"""
    record, proof = _control()
    with pytest.raises(TransactionContractError, match="action recorded_at"):
        _prepared(
            control=(record, proof),
            recorded_at="2026-07-19T10:00:00Z",
        )

    early_action = dataclasses.replace(
        _prepared(control=(record, proof)),
        recorded_at="2026-07-19T10:00:00Z",
    )
    with pytest.raises(TransactionContractError, match="action recorded_at"):
        append_transaction_action(_journal(), early_action, record, proof)


def _changed_control_action(
    prepared: TransactionAction,
    *,
    epoch: int,
    token: bytes,
) -> tuple[TransactionAction, ControlLeaseRecord, ControlLeaseProof]:
    record, proof = _control(epoch=epoch, token=token)
    action = dataclasses.replace(
        prepared,
        state=TransactionActionState.APPLIED,
        control_lease_epoch_audit=record.epoch,
        control_token_sha256_audit=record.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(record),
        recorded_at="2026-07-19T10:02:00Z",
    )
    return action, record, proof


@pytest.mark.parametrize(
    ("epoch", "token"),
    ((4, OTHER_TOKEN), (5, TOKEN)),
)
def test_journal_live_advance拒绝epoch_token映射漂移(epoch: int, token: bytes) -> None:
    prepared = _prepared()
    _, record, proof = _changed_control_action(prepared, epoch=epoch, token=token)
    with pytest.raises(TransactionContractError, match="token"):
        advance_transaction_action(
            prepared,
            TransactionActionState.APPLIED,
            record,
            proof,
            recorded_at="2026-07-19T10:02:00Z",
        )


@pytest.mark.parametrize(
    ("epoch", "token"),
    ((4, OTHER_TOKEN), (5, TOKEN)),
)
def test_journal_live_append拒绝epoch_token映射漂移(epoch: int, token: bytes) -> None:
    prepared = _prepared()
    journal = append_transaction_action(_journal(), prepared, *_control())
    action, record, proof = _changed_control_action(prepared, epoch=epoch, token=token)
    with pytest.raises(TransactionContractError, match="token"):
        append_transaction_action(journal, action, record, proof)


@pytest.mark.parametrize(
    ("epoch", "token"),
    ((4, OTHER_TOKEN), (5, TOKEN)),
)
def test_journal直接构造拒绝epoch_token映射漂移(epoch: int, token: bytes) -> None:
    prepared = _prepared()
    action, _, _ = _changed_control_action(prepared, epoch=epoch, token=token)
    with pytest.raises(TransactionContractError, match="token"):
        dataclasses.replace(
            _journal(),
            actions=(prepared, action),
            updated_at=action.recorded_at,
        )


@pytest.mark.parametrize(
    ("epoch", "token"),
    ((4, OTHER_TOKEN), (5, TOKEN)),
)
def test_journal_decoder拒绝epoch_token映射漂移(epoch: int, token: bytes) -> None:
    prepared = _prepared()
    action, _, _ = _changed_control_action(prepared, epoch=epoch, token=token)
    journal = _journal()
    payload = {
        "schema_version": journal.schema_version,
        "attempt_id": journal.attempt_id,
        "reservation_sha256": journal.reservation_sha256,
        "journal_genesis_sha256": journal.journal_genesis_sha256,
        "status": journal.status.value,
        "actions": [
            json.loads(encode_transaction_action(prepared)),
            json.loads(encode_transaction_action(action)),
        ],
        "terminal_evidence_sha256": None,
        "created_at": journal.created_at,
        "updated_at": action.recorded_at,
        "completed_at": None,
    }
    with pytest.raises(TransactionContractError, match="序列化"):
        decode_transaction_journal(json.dumps(payload).encode("utf-8"))


def test_completed_journal直接构造拒绝空动作历史() -> None:
    with pytest.raises(TransactionContractError):
        dataclasses.replace(
            _journal(),
            status=TransactionJournalStatus.COMPLETED,
            terminal_evidence_sha256="9" * 64,
            updated_at="2026-07-19T10:01:00Z",
            completed_at="2026-07-19T10:01:00Z",
        )


def test_completed_journal严格codec拒绝空动作历史() -> None:
    payload = json.loads(encode_transaction_journal(_journal()))
    payload.update(
        status=TransactionJournalStatus.COMPLETED.value,
        terminal_evidence_sha256="9" * 64,
        updated_at="2026-07-19T10:01:00Z",
        completed_at="2026-07-19T10:01:00Z",
    )

    with pytest.raises(TransactionContractError):
        decode_transaction_journal(json.dumps(payload).encode("utf-8"))


def test_completed_journal拒绝latest动作未COMMITTED() -> None:
    prepared = _prepared()

    with pytest.raises(TransactionContractError):
        dataclasses.replace(
            _journal(),
            status=TransactionJournalStatus.COMPLETED,
            actions=(prepared,),
            terminal_evidence_sha256="9" * 64,
            updated_at="2026-07-19T10:02:00Z",
            completed_at="2026-07-19T10:02:00Z",
        )


def test_journal直接构造拒绝首个action时间不晚于created_at() -> None:
    journal = _journal()
    prepared = dataclasses.replace(_prepared(), recorded_at=journal.created_at)

    with pytest.raises(TransactionContractError):
        dataclasses.replace(
            journal,
            actions=(prepared,),
            updated_at=prepared.recorded_at,
        )


def test_journal直接构造拒绝相邻action_recorded_at未严格递增() -> None:
    prepared = _prepared()
    applied = dataclasses.replace(
        prepared,
        state=TransactionActionState.APPLIED,
        recorded_at=prepared.recorded_at,
    )

    with pytest.raises(TransactionContractError):
        dataclasses.replace(
            _journal(),
            actions=(prepared, applied),
            updated_at=applied.recorded_at,
        )


def test_completed_journal拒绝completed_at不晚于全部动作() -> None:
    record, proof = _control()
    prepared = _prepared(control=(record, proof))
    applied = advance_transaction_action(
        prepared,
        TransactionActionState.APPLIED,
        record,
        proof,
        recorded_at="2026-07-19T10:02:00Z",
    )
    committed = advance_transaction_action(
        applied,
        TransactionActionState.COMMITTED,
        record,
        proof,
        recorded_at="2026-07-19T10:03:00Z",
    )

    with pytest.raises(TransactionContractError):
        dataclasses.replace(
            _journal(),
            status=TransactionJournalStatus.COMPLETED,
            actions=(prepared, applied, committed),
            terminal_evidence_sha256="9" * 64,
            updated_at=committed.recorded_at,
            completed_at=committed.recorded_at,
        )


def test_journal_append拒绝lease绑定不同reservation() -> None:
    journal = _journal()
    other_reservation = dataclasses.replace(_reservation(), plan_sha256="9" * 64)
    record, proof = issue_control_lease(
        other_reservation,
        epoch=4,
        token=TOKEN,
        owner="controller-a",
        issued_at="2026-07-19T10:00:30Z",
    )
    assert record.reservation_sha256 != journal.reservation_sha256

    with pytest.raises(TransactionContractError):
        append_transaction_action(journal, _prepared(), record, proof)
