"""事务 journal 的无反向依赖模型、枚举与稳定身份函数。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from codev_platform.core.runtime_models import canonical_sha256


MAX_TRANSACTION_JOURNAL_BYTES = 1_048_576
SERVE_PERMIT_RESOURCE_KIND = "serve-permit"
CURRENT_SERVE_PERMIT_RESOURCE_ID = "current-serving-permit"


class TransactionContractError(ValueError):
    """事务动作、journal 历史或序列化载荷不符合契约。"""


class TransactionActionState(StrEnum):
    PREPARED = "prepared"
    APPLIED = "applied"
    COMMITTED = "committed"


class TransactionJournalStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class TransactionActionIntent(StrEnum):
    REVOKE_CURRENT_SERVE_PERMIT = "revoke-current-serve-permit"
    APPLY_RESOURCE = "apply-resource"


class TransactionRecoveryDirective(StrEnum):
    START = "start"
    RECONCILE = "reconcile"
    COMMIT = "commit"
    ADVANCE = "advance"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class TransactionAction:
    """一次资源动作的一个追加式状态记录。"""

    schema_version: int
    attempt_id: str
    reservation_sha256: str
    step_sequence: int
    resource_kind: str
    resource_id: str
    intent: TransactionActionIntent
    operation_sha256: str
    before_sha256: str
    after_sha256: str
    state: TransactionActionState
    control_lease_epoch_audit: int
    control_token_sha256_audit: str
    control_lease_record_sha256_audit: str
    recorded_at: str

    def __post_init__(self) -> None:
        from codev_platform.runtime_transaction_contract_validation import (
            require_action_fields,
        )

        require_action_fields(self)


@dataclass(frozen=True, slots=True)
class TransactionJournal:
    """绑定 attempt/genesis 的不可变追加式动作历史。"""

    schema_version: int
    attempt_id: str
    reservation_sha256: str
    journal_genesis_sha256: str
    status: TransactionJournalStatus
    actions: tuple[TransactionAction, ...]
    terminal_evidence_sha256: str | None
    created_at: str
    updated_at: str
    completed_at: str | None

    def __post_init__(self) -> None:
        from codev_platform.runtime_transaction_contract_validation import (
            require_completed_journal,
            require_journal_fields,
            require_journal_history,
            require_journal_size,
        )

        require_journal_fields(self)
        require_journal_history(self)
        require_completed_journal(self)
        require_journal_size(self)


def transaction_action_key(value: TransactionAction) -> str:
    """只由 attempt、步骤序号、资源种类和资源 ID 计算动作键。"""
    if type(value) is not TransactionAction:
        raise TransactionContractError("只接受 TransactionAction")
    return canonical_sha256(
        {
            "attempt_id": value.attempt_id,
            "resource_id": value.resource_id,
            "resource_kind": value.resource_kind,
            "step_sequence": value.step_sequence,
        }
    )


def transaction_journal_sha256(value: TransactionJournal) -> str:
    """计算覆盖 genesis 与完整追加历史的 journal head 摘要。"""
    if type(value) is not TransactionJournal:
        raise TransactionContractError("只接受 TransactionJournal")
    return canonical_sha256(value)


__all__ = [
    "CURRENT_SERVE_PERMIT_RESOURCE_ID",
    "MAX_TRANSACTION_JOURNAL_BYTES",
    "SERVE_PERMIT_RESOURCE_KIND",
    "TransactionAction",
    "TransactionActionIntent",
    "TransactionActionState",
    "TransactionContractError",
    "TransactionJournal",
    "TransactionJournalStatus",
    "TransactionRecoveryDirective",
    "transaction_action_key",
    "transaction_journal_sha256",
]
