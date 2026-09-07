"""事务 action 与 journal 的字段、历史及 capability 校验。"""

from __future__ import annotations

import hmac
import re

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    require_attempt_id,
    require_schema,
    require_sha256,
    require_strictly_later,
    require_utc_rfc3339_z,
)
from codev_platform.core.runtime_models import (
    RuntimeModelError,
    canonical_json_bytes,
    canonical_sha256,
)
from codev_platform.runtime_control_lease_lineage import (
    ControlLeaseAnchor,
    ControlLeaseLineageError,
    verify_anchor_event_window,
    verify_current_active_lineage,
)
from codev_platform.runtime_fencing import (
    ControlLeaseRecord,
    FencingContractError,
    control_lease_record_sha256,
    verify_control_lease,
)
from codev_platform.runtime_transaction_contract_model import (
    CURRENT_SERVE_PERMIT_RESOURCE_ID,
    MAX_TRANSACTION_JOURNAL_BYTES,
    SERVE_PERMIT_RESOURCE_KIND,
    TransactionAction,
    TransactionActionIntent,
    TransactionActionState,
    TransactionContractError,
    TransactionJournal,
    TransactionJournalStatus,
    transaction_action_key,
)


_MAX_ACTIONS = 4_096
_MAX_SEQUENCE = 2**31 - 1
_MAX_EPOCH = 2**63 - 1
_RESOURCE_KIND = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_RESOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}\Z")


def require_action_fields(value: TransactionAction) -> None:
    """校验单条动作的持久字段形状及完整 control 记录锚点。"""
    try:
        require_schema(value.schema_version, 1)
        require_attempt_id(value.attempt_id)
        require_sha256(value.reservation_sha256, field="reservation_sha256")
        for field, digest in (
            ("operation_sha256", value.operation_sha256),
            ("before_sha256", value.before_sha256),
            ("after_sha256", value.after_sha256),
            ("control_token_sha256_audit", value.control_token_sha256_audit),
            (
                "control_lease_record_sha256_audit",
                value.control_lease_record_sha256_audit,
            ),
        ):
            require_sha256(digest, field=field)
        require_utc_rfc3339_z(value.recorded_at, field="recorded_at")
    except RuntimeContractSupportError as exc:
        raise TransactionContractError(str(exc)) from None
    if type(value.step_sequence) is not int or not 1 <= value.step_sequence <= _MAX_SEQUENCE:
        raise TransactionContractError("step_sequence 必须是有效正整数")
    if (
        type(value.resource_kind) is not str
        or _RESOURCE_KIND.fullmatch(value.resource_kind) is None
    ):
        raise TransactionContractError("resource_kind 必须是受限小写标识")
    if type(value.resource_id) is not str or _RESOURCE_ID.fullmatch(value.resource_id) is None:
        raise TransactionContractError("resource_id 必须是受限非空标识")
    if type(value.intent) is not TransactionActionIntent:
        raise TransactionContractError("intent 必须是 TransactionActionIntent")
    if value.before_sha256 == value.after_sha256:
        raise TransactionContractError("动作前后资源摘要不能相同")
    if type(value.state) is not TransactionActionState:
        raise TransactionContractError("state 必须是 TransactionActionState")
    _require_epoch(value.control_lease_epoch_audit)


def require_journal_fields(value: TransactionJournal) -> None:
    """校验 journal 的不可变身份、生命周期字段和时间投影。"""
    try:
        require_schema(value.schema_version, 1)
        require_attempt_id(value.attempt_id)
        require_sha256(value.reservation_sha256, field="reservation_sha256")
        require_sha256(value.journal_genesis_sha256, field="journal_genesis_sha256")
        require_utc_rfc3339_z(value.created_at, field="created_at")
        require_utc_rfc3339_z(value.updated_at, field="updated_at")
    except RuntimeContractSupportError as exc:
        raise TransactionContractError(str(exc)) from None
    if type(value.actions) is not tuple or len(value.actions) > _MAX_ACTIONS:
        raise TransactionContractError("actions 必须是固定上限内的 tuple")
    if type(value.status) is not TransactionJournalStatus:
        raise TransactionContractError("status 必须是 TransactionJournalStatus")
    if value.journal_genesis_sha256 != compute_genesis(
        value.attempt_id,
        value.reservation_sha256,
        value.created_at,
    ):
        raise TransactionContractError("journal genesis 与不可变身份不一致")
    active_updated_at = value.actions[-1].recorded_at if value.actions else value.created_at
    if value.status is TransactionJournalStatus.ACTIVE:
        if value.terminal_evidence_sha256 is not None or value.completed_at is not None:
            raise TransactionContractError("活动 journal 不得含终态证据")
        if value.updated_at != active_updated_at:
            raise TransactionContractError("journal updated_at 与追加历史不一致")
        return
    try:
        require_sha256(value.terminal_evidence_sha256, field="terminal_evidence_sha256")
        require_utc_rfc3339_z(value.completed_at, field="completed_at")
    except RuntimeContractSupportError as exc:
        raise TransactionContractError(str(exc)) from None
    if value.updated_at != value.completed_at:
        raise TransactionContractError("完成 journal 的 updated_at 必须等于 completed_at")


def require_journal_history(value: TransactionJournal) -> None:
    """验证追加顺序、动作状态机与跨 epoch 的 control 审计连续性。"""
    latest: dict[str, TransactionAction] = {}
    last_new: TransactionAction | None = None
    previous_action: TransactionAction | None = None
    previous_recorded_at = value.created_at
    for action in value.actions:
        if type(action) is not TransactionAction or action.attempt_id != value.attempt_id:
            raise TransactionContractError("journal 动作与 attempt 身份不一致")
        if action.reservation_sha256 != value.reservation_sha256:
            raise TransactionContractError("journal 动作与 reservation 摘要不一致")
        require_later_timestamp(action.recorded_at, previous_recorded_at, "recorded_at")
        previous_recorded_at = action.recorded_at
        if previous_action is not None:
            require_control_successor(
                previous_action.control_lease_epoch_audit,
                previous_action.control_token_sha256_audit,
                previous_action.control_lease_record_sha256_audit,
                action.control_lease_epoch_audit,
                action.control_token_sha256_audit,
                action.control_lease_record_sha256_audit,
            )
        previous_action = action
        key = transaction_action_key(action)
        previous = latest.get(key)
        if previous is None:
            if last_new is not None:
                previous_key = transaction_action_key(last_new)
                if latest[previous_key].state is not TransactionActionState.COMMITTED:
                    raise TransactionContractError("前一动作尚未 COMMITTED")
            require_new_action_after(last_new, action)
            last_new = action
        else:
            if action_identity(previous) != action_identity(action):
                raise TransactionContractError("同一动作键的资源操作身份漂移")
            if action.state is not next_action_state(previous.state):
                raise TransactionContractError("journal 动作状态边无效")
        latest[key] = action


def require_completed_journal(value: TransactionJournal) -> None:
    """completed journal 必须有完整动作收口，且完成时间严格后置。"""
    if value.status is not TransactionJournalStatus.COMPLETED:
        return
    if not value.actions:
        raise TransactionContractError("完成 journal 不得为空")
    latest: dict[str, TransactionAction] = {}
    for action in value.actions:
        latest[transaction_action_key(action)] = action
    if any(action.state is not TransactionActionState.COMMITTED for action in latest.values()):
        raise TransactionContractError("完成 journal 的最新动作必须全部 COMMITTED")
    require_later_timestamp(
        value.completed_at,
        value.actions[-1].recorded_at,
        "completed_at",
    )


def require_journal_size(value: TransactionJournal) -> None:
    """限制持久 journal 的规范 JSON 尺寸，避免无限增长。"""
    try:
        encoded = canonical_json_bytes(value)
    except RuntimeModelError:
        raise TransactionContractError("TransactionJournal 无法规范序列化") from None
    if len(encoded) > MAX_TRANSACTION_JOURNAL_BYTES:
        raise TransactionContractError("TransactionJournal 序列化载荷超出 1 MiB 上限")


def require_new_action(current: TransactionJournal, action: TransactionAction) -> None:
    """确认一个新动作不会绕过前一动作的 COMMITTED 收口。"""
    previous = current.actions[-1] if current.actions else None
    if previous is not None and previous.state is not TransactionActionState.COMMITTED:
        raise TransactionContractError("前一动作尚未 COMMITTED")
    require_new_action_after(previous, action)


def require_new_action_after(
    previous: TransactionAction | None,
    action: TransactionAction,
) -> None:
    """校验新动作从 PREPARED 起步，并保持步骤序号连续。"""
    if action.state is not TransactionActionState.PREPARED:
        raise TransactionContractError("新动作状态边必须从 PREPARED 开始")
    if previous is None:
        if (
            action.intent is not TransactionActionIntent.REVOKE_CURRENT_SERVE_PERMIT
            or action.resource_kind != SERVE_PERMIT_RESOURCE_KIND
            or action.resource_id != CURRENT_SERVE_PERMIT_RESOURCE_ID
        ):
            raise TransactionContractError("journal 第一个入口动作必须撤销 serve permit")
        expected = 1
    else:
        expected = previous.step_sequence + 1
    if action.step_sequence != expected:
        raise TransactionContractError("journal 步骤序号必须连续")


def require_action_audit(
    action: TransactionAction,
    lease_record: ControlLeaseRecord,
) -> None:
    """确认动作审计的 epoch、token 与完整 record 同时匹配当前 lease。"""
    if lease_record.epoch != action.control_lease_epoch_audit:
        raise TransactionContractError("ControlLeaseRecord epoch 与动作审计不一致")
    if not hmac.compare_digest(
        lease_record.token_sha256,
        action.control_token_sha256_audit,
    ):
        raise TransactionContractError("ControlLeaseRecord token 与动作审计不一致")
    if not hmac.compare_digest(
        control_lease_record_sha256(lease_record),
        action.control_lease_record_sha256_audit,
    ):
        raise TransactionContractError("ControlLeaseRecord 摘要与动作审计不一致")


def require_live_lease_event(
    lease_record: ControlLeaseRecord,
    recorded_at: str,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...] | None,
) -> tuple[ControlLeaseRecord, ...]:
    """确认即将写入的动作发生在当前控制租约的有效时间窗。"""
    try:
        lineage = verify_current_active_lineage(
            lease_record,
            control_lease_lineage,
        )
        verify_anchor_event_window(
            ControlLeaseAnchor.from_record(lease_record),
            recorded_at,
            lineage,
            lease_record,
            event_field="action recorded_at",
        )
        return lineage
    except ControlLeaseLineageError as exc:
        raise TransactionContractError(str(exc)) from None


def require_action_lineage_event(
    action: TransactionAction,
    *,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...] | None,
) -> ControlLeaseRecord:
    """确认已有动作的审计锚点与记录时间可由当前链复验。"""
    try:
        lineage = verify_current_active_lineage(
            current_lease,
            control_lease_lineage,
        )
        return verify_anchor_event_window(
            ControlLeaseAnchor(
                record_sha256=action.control_lease_record_sha256_audit,
                attempt_id=action.attempt_id,
                reservation_sha256=action.reservation_sha256,
                epoch=action.control_lease_epoch_audit,
                token_sha256=action.control_token_sha256_audit,
            ),
            action.recorded_at,
            lineage,
            current_lease,
            event_field="action recorded_at",
        )
    except ControlLeaseLineageError as exc:
        raise TransactionContractError(str(exc)) from None


def require_lease(
    lease_record: object,
    lease: object,
    attempt_id: str,
) -> None:
    """复验当前 capability，并限制到同一 attempt。"""
    try:
        verify_control_lease(lease_record, lease)
    except FencingContractError as exc:
        raise TransactionContractError(str(exc)) from None
    if lease_record.attempt_id != attempt_id:
        raise TransactionContractError("ControlLeaseRecord attempt 身份不一致")


def require_control_successor(
    previous_epoch: int,
    previous_token_sha256: str,
    previous_record_sha256: str,
    next_epoch: int,
    next_token_sha256: str,
    next_record_sha256: str,
) -> None:
    """限制审计记录只能沿同记录或合法 token-rotation 前进。"""
    if next_epoch < previous_epoch:
        raise TransactionContractError("control lease epoch 不得倒退")
    same_token = hmac.compare_digest(previous_token_sha256, next_token_sha256)
    if next_epoch == previous_epoch and not same_token:
        raise TransactionContractError("相同 control epoch 不得更换 token")
    if next_epoch == previous_epoch and not hmac.compare_digest(
        previous_record_sha256,
        next_record_sha256,
    ):
        raise TransactionContractError("相同 control epoch 不得漂移完整 lease 记录")
    if next_epoch > previous_epoch and same_token:
        raise TransactionContractError("control epoch 提升必须轮换 token")


def next_action_state(
    state: TransactionActionState,
) -> TransactionActionState | None:
    """返回 action 状态机允许的唯一下一状态。"""
    if state is TransactionActionState.PREPARED:
        return TransactionActionState.APPLIED
    if state is TransactionActionState.APPLIED:
        return TransactionActionState.COMMITTED
    return None


def history_for_key(
    actions: tuple[TransactionAction, ...],
    key: str,
) -> tuple[TransactionAction, ...]:
    """取同一动作键的全量追加历史。"""
    return tuple(action for action in actions if transaction_action_key(action) == key)


def action_identity(value: TransactionAction) -> tuple[object, ...]:
    """同键动作不随状态变动的不可变资源操作身份。"""
    return (
        value.schema_version,
        value.attempt_id,
        value.reservation_sha256,
        value.step_sequence,
        value.resource_kind,
        value.resource_id,
        value.intent,
        value.operation_sha256,
        value.before_sha256,
        value.after_sha256,
    )


def require_later_timestamp(value: object, boundary: object, field: str) -> None:
    """统一映射动作历史的严格时间单调错误。"""
    try:
        require_strictly_later(
            value,
            boundary,
            field=field,
            boundary_field="前序审计时间",
        )
    except RuntimeContractSupportError as exc:
        raise TransactionContractError(str(exc)) from None


def compute_genesis(
    attempt_id: str,
    reservation_sha256: str,
    created_at: str,
) -> str:
    """从 reservation 身份和创建时间派生不可替换的 journal genesis。"""
    try:
        require_attempt_id(attempt_id)
        require_sha256(reservation_sha256, field="reservation_sha256")
        require_utc_rfc3339_z(created_at, field="created_at")
    except RuntimeContractSupportError as exc:
        raise TransactionContractError(str(exc)) from None
    return canonical_sha256(
        {
            "attempt_id": attempt_id,
            "created_at": created_at,
            "reservation_sha256": reservation_sha256,
            "schema_version": 1,
        }
    )


def _require_epoch(value: object) -> None:
    if type(value) is not int or not 1 <= value <= _MAX_EPOCH:
        raise TransactionContractError("control lease epoch 必须在 1..2^63-1")


__all__ = [
    "action_identity",
    "compute_genesis",
    "history_for_key",
    "next_action_state",
    "require_action_audit",
    "require_action_fields",
    "require_action_lineage_event",
    "require_completed_journal",
    "require_control_successor",
    "require_journal_fields",
    "require_journal_history",
    "require_journal_size",
    "require_later_timestamp",
    "require_lease",
    "require_live_lease_event",
    "require_new_action",
]
