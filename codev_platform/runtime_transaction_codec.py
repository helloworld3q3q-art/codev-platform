"""事务 action 与 journal 的严格持久化编解码。"""

from __future__ import annotations

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    decode_exact_json_mapping,
)
from codev_platform.core.runtime_models import canonical_json_bytes
from codev_platform.runtime_transaction_contract import (
    MAX_TRANSACTION_JOURNAL_BYTES,
    TransactionAction,
    TransactionActionIntent,
    TransactionActionState,
    TransactionContractError,
    TransactionJournal,
    TransactionJournalStatus,
)


_MAX_ACTION_BYTES = 32_768
_ACTION_FIELDS = frozenset(
    {
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
    }
)
_JOURNAL_FIELDS = frozenset(
    {
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
    }
)


def encode_transaction_action(value: TransactionAction) -> bytes:
    """编码一个公开 journal 动作记录。"""
    if type(value) is not TransactionAction:
        raise TransactionContractError("只接受 TransactionAction")
    return canonical_json_bytes(value)


def decode_transaction_action(payload: bytes) -> TransactionAction:
    """严格解码一个 journal 动作记录。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_ACTION_FIELDS,
            optional_defaults={},
            max_bytes=_MAX_ACTION_BYTES,
        )
        return _action_from_mapping(values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise TransactionContractError("TransactionAction 序列化载荷无效") from None


def encode_transaction_journal(value: TransactionJournal) -> bytes:
    """编码完整 journal，原始 capability 永不进入载荷。"""
    if type(value) is not TransactionJournal:
        raise TransactionContractError("只接受 TransactionJournal")
    return canonical_json_bytes(value)


def decode_transaction_journal(payload: bytes) -> TransactionJournal:
    """严格解码 journal 及每一个嵌套动作。"""
    try:
        values = decode_exact_json_mapping(
            payload,
            required_fields=_JOURNAL_FIELDS,
            optional_defaults={},
            max_bytes=MAX_TRANSACTION_JOURNAL_BYTES,
        )
        nested = values.get("actions")
        if type(nested) is not list:
            raise TransactionContractError("actions 必须是 JSON 数组")
        values["actions"] = tuple(_action_from_mapping(item) for item in nested)
        status = values.get("status")
        if type(status) is not str:
            raise TransactionContractError("status 类型无效")
        values["status"] = TransactionJournalStatus(status)
        return TransactionJournal(**values)
    except (RuntimeContractSupportError, TypeError, ValueError):
        raise TransactionContractError("TransactionJournal 序列化载荷无效") from None


def _action_from_mapping(value: object) -> TransactionAction:
    if type(value) is not dict or frozenset(value) != _ACTION_FIELDS:
        raise TransactionContractError("TransactionAction 字段集合不匹配")
    values = dict(value)
    state = values.get("state")
    intent = values.get("intent")
    if type(state) is not str or type(intent) is not str:
        raise TransactionContractError("TransactionAction 枚举类型无效")
    values["state"] = TransactionActionState(state)
    values["intent"] = TransactionActionIntent(intent)
    return TransactionAction(**values)


__all__ = [
    "decode_transaction_action",
    "decode_transaction_journal",
    "encode_transaction_action",
    "encode_transaction_journal",
]
