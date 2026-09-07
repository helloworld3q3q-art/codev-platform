"""运行时分域 store 共用的窄策略、快照和控制协议。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, Protocol, TypeVar

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    require_sha256,
)
from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    attempt_reservation_sha256,
)
from codev_platform.runtime_control_lease_lineage import (
    ControlLeaseLineageError,
    verify_current_active_lineage,
)
from codev_platform.runtime_deployment_lock_capability import BoundDeploymentLock
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ControlLeaseStatus,
    control_lease_record_sha256,
)
from codev_platform.runtime_managed_file import ManagedFilePolicy
from codev_platform.runtime_recovery_contract import (
    RecoveryContractError,
    RecoveryEnvelope,
    recovery_envelope_sha256,
    verify_recovery_envelope,
)
from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBinding,
    RuntimeRootBindingError,
)
from codev_platform.runtime_storage import RuntimeStoragePathError, normalize_runtime_root
from codev_platform.runtime_transaction_contract import (
    TransactionJournal,
    create_transaction_journal,
    transaction_journal_sha256,
)


_Value = TypeVar("_Value")
_PRIVATE_FILE_MODE = 0o600


@dataclass(frozen=True, slots=True)
class RuntimeStorePolicy:
    """固定受管根与写入属主，禁止由各 store 放宽属主策略。"""

    root: Path
    owner_uid: int
    _root_binding: RuntimeRootBinding = field(
        init=False,
        repr=False,
        compare=False,
        hash=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            raise ValueError("root 必须是 Path")
        try:
            root = normalize_runtime_root(self.root)
        except (RuntimeStoragePathError, RuntimeRootBindingError) as error:
            raise ValueError(str(error)) from None
        if type(self.owner_uid) is not int or self.owner_uid < 0:
            raise ValueError("owner_uid 必须是非负整数")
        object.__setattr__(self, "root", root)
        object.__setattr__(
            self,
            "_root_binding",
            RuntimeRootBinding(root, self.owner_uid),
        )

    @property
    def root_binding(self) -> RuntimeRootBinding:
        """返回同一 policy 内各 store 共享的根目录身份绑定。"""
        return self._root_binding


def private_managed_file_policy(owner_uid: int, max_bytes: int) -> ManagedFilePolicy:
    """生成所有运行时分域 store 共用的私有受管文件策略。"""
    return ManagedFilePolicy(
        mode=_PRIVATE_FILE_MODE,
        require_uid=owner_uid,
        max_bytes=max_bytes,
    )


@dataclass(frozen=True, slots=True)
class StoredSnapshot(Generic[_Value]):
    """已严格验证且以规范摘要标识的不可变读取结果。"""

    value: _Value
    sha256: str

    def __post_init__(self) -> None:
        try:
            require_sha256(self.sha256)
        except RuntimeContractSupportError as error:
            raise ValueError(str(error)) from None


@dataclass(frozen=True, slots=True)
class ControlLeaseSnapshot:
    """跨分域传递的、与规范记录摘要精确绑定的 control lease 快照。"""

    record: ControlLeaseRecord
    sha256: str

    def __post_init__(self) -> None:
        if type(self.record) is not ControlLeaseRecord:
            raise ValueError("record 必须是 ControlLeaseRecord")
        try:
            require_sha256(self.sha256)
        except RuntimeContractSupportError as error:
            raise ValueError(str(error)) from None
        if self.sha256 != control_lease_record_sha256(self.record):
            raise ValueError("control lease 快照摘要与规范记录不一致")


@dataclass(frozen=True, slots=True)
class RuntimeMutationScope:
    """活动控制临界区内唯一可传递的租约快照与根能力。"""

    snapshot: ControlLeaseSnapshot
    bound_root: BoundRuntimeRoot
    control_lease_lineage: tuple[ControlLeaseRecord, ...]
    deployment_lock: BoundDeploymentLock

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ControlLeaseSnapshot:
            raise ValueError("活动控制 scope 快照类型无效")
        if self.snapshot.record.status is not ControlLeaseStatus.ACTIVE:
            raise ValueError("活动控制 scope 只能持有 ACTIVE 快照")
        if type(self.bound_root) is not BoundRuntimeRoot:
            raise ValueError("活动控制 scope 根租约类型无效")
        if type(self.control_lease_lineage) is not tuple:
            raise ValueError("活动控制 scope 谱系类型无效")
        if type(self.deployment_lock) is not BoundDeploymentLock:
            raise ValueError("活动控制 scope deployment-lock capability 类型无效")
        try:
            verify_current_active_lineage(
                self.snapshot.record,
                self.control_lease_lineage,
            )
        except ControlLeaseLineageError as error:
            raise ValueError("活动控制 scope 谱系无效") from error


@dataclass(frozen=True, slots=True)
class RuntimeTerminalCleanupScope:
    """退休清理临界区内唯一可传递的 tombstone 与根能力。"""

    snapshot: ControlLeaseSnapshot
    bound_root: BoundRuntimeRoot
    deployment_lock: BoundDeploymentLock

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ControlLeaseSnapshot:
            raise ValueError("终态清理 scope 快照类型无效")
        if self.snapshot.record.status is not ControlLeaseStatus.RETIRED:
            raise ValueError("终态清理 scope 只能持有 RETIRED 快照")
        if type(self.bound_root) is not BoundRuntimeRoot:
            raise ValueError("终态清理 scope 根租约类型无效")
        if type(self.deployment_lock) is not BoundDeploymentLock:
            raise ValueError("终态清理 scope deployment-lock capability 类型无效")


@dataclass(frozen=True, slots=True)
class ActiveBootstrapContext:
    """同一活动 attempt 的 reservation、稳定 genesis 与活动 envelope 快照。"""

    reservation: StoredSnapshot[AttemptReservation]
    journal_genesis: StoredSnapshot[TransactionJournal]
    active_envelope: StoredSnapshot[RecoveryEnvelope]

    def __post_init__(self) -> None:
        if (
            type(self.reservation.value) is not AttemptReservation
            or type(self.journal_genesis.value) is not TransactionJournal
            or type(self.active_envelope.value) is not RecoveryEnvelope
        ):
            raise ValueError("活动 bootstrap context 类型无效")
        expected_journal = create_transaction_journal(
            self.reservation.value,
            created_at=self.journal_genesis.value.created_at,
        )
        if self.journal_genesis.value != expected_journal:
            raise ValueError("活动 bootstrap context journal 不是空 genesis")
        try:
            verify_recovery_envelope(
                self.active_envelope.value,
                self.reservation.value,
                self.journal_genesis.value,
            )
        except RecoveryContractError as error:
            raise ValueError("活动 bootstrap context 绑定无效") from error
        expected_digests = (
            attempt_reservation_sha256(self.reservation.value),
            transaction_journal_sha256(self.journal_genesis.value),
            recovery_envelope_sha256(self.active_envelope.value),
        )
        actual_digests = (
            self.reservation.sha256,
            self.journal_genesis.sha256,
            self.active_envelope.sha256,
        )
        if actual_digests != expected_digests:
            raise ValueError("活动 bootstrap context 快照摘要不一致")


class RuntimeControlGate(Protocol):
    """向分域 store 注入当前活动 control lease 的最小授权边界。"""

    def mutation(
        self,
        proof: ControlLeaseProof,
    ) -> AbstractContextManager[RuntimeMutationScope]:
        """在同一控制临界区内验证 proof 并授予活动 scope。"""


class RuntimeTerminalCleanupGate(Protocol):
    """向终态清理路径注入无 token 的精确 tombstone 门禁。"""

    def terminal_cleanup(
        self,
        tombstone: ControlLeaseSnapshot,
    ) -> AbstractContextManager[RuntimeTerminalCleanupScope]:
        """仅在当前 tombstone 与不可变 history 精确一致时授予清理 scope。"""


class RuntimeTransactionControlGate(
    RuntimeControlGate,
    RuntimeTerminalCleanupGate,
    Protocol,
):
    """同时覆盖活动写入与退休后精确清理的事务控制门禁。"""


__all__ = [
    "ActiveBootstrapContext",
    "BoundRuntimeRoot",
    "ControlLeaseSnapshot",
    "RuntimeControlGate",
    "RuntimeRootBinding",
    "RuntimeRootBindingError",
    "RuntimeMutationScope",
    "RuntimeStorePolicy",
    "RuntimeTerminalCleanupGate",
    "RuntimeTerminalCleanupScope",
    "RuntimeTransactionControlGate",
    "StoredSnapshot",
    "private_managed_file_policy",
]
