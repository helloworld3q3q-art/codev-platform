"""control lease record、history 与 current 的描述符绑定持久化。"""

from __future__ import annotations

from codev_platform.runtime_fencing import (
    ControlLeaseRecord,
    FencingContractError,
    control_lease_record_sha256,
    decode_control_lease_record,
    encode_control_lease_record,
)
from codev_platform.runtime_control_lease_transition import (
    ControlLeaseTransitionError,
    ControlLeaseTransitionIntent,
    MAX_CONTROL_LEASE_TRANSITION_BYTES,
    decode_control_lease_transition_intent,
    encode_control_lease_transition_intent,
)
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    create_managed_bytes_exclusive_at,
    read_managed_bytes_at,
    read_optional_managed_bytes_at,
    remove_managed_bytes_exact_at,
    write_managed_bytes_atomic_at,
)
from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBindingError,
)
from codev_platform.runtime_storage import (
    control_lease_history_path,
    control_lease_pending_transition_path,
    control_lease_record_path,
)
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeStorePolicy,
    private_managed_file_policy,
)


_CONTROL_RECORD_MAX_BYTES = 16_384


class ControlLeasePersistenceError(RuntimeError):
    """control lease 受管 record、history 或 current 无法严格持久化。"""


class ControlLeasePersistence:
    """只在调用方持有的同 policy 根租约内操作 control lease 文件。"""

    def __init__(self, policy: RuntimeStorePolicy) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        self._policy = policy
        self._root = policy.root
        self._record_policy = private_managed_file_policy(
            policy.owner_uid,
            _CONTROL_RECORD_MAX_BYTES,
        )
        self._pending_policy = private_managed_file_policy(
            policy.owner_uid,
            MAX_CONTROL_LEASE_TRANSITION_BYTES,
        )

    def load_current_or_none(
        self,
        root: BoundRuntimeRoot,
    ) -> ControlLeaseSnapshot | None:
        """严格读取 current；安全缺失才返回 ``None``，否则同步复验 history。"""
        self._require_bound_root(root)
        payload = self._read_optional(
            control_lease_record_path(self._root),
            root,
            "current control lease",
        )
        if payload is None:
            self._require_bound_root(root)
            return None
        snapshot = self._decode_snapshot(payload, "current control lease")
        self._load_history_exact(root, snapshot.record)
        self._require_bound_root(root)
        return snapshot

    def load_current_required(self, root: BoundRuntimeRoot) -> ControlLeaseSnapshot:
        """严格读取已存在 current。"""
        current = self.load_current_or_none(root)
        if current is None:
            raise ControlLeasePersistenceError("当前 control lease 不存在")
        return current

    def load_pending_transition_or_none(
        self,
        root: BoundRuntimeRoot,
    ) -> ControlLeaseTransitionIntent | None:
        """严格读取唯一 pending intent；安全缺失才返回 ``None``。"""
        self._require_bound_root(root)
        payload = self._read_optional(
            control_lease_pending_transition_path(self._root),
            root,
            "pending control lease transition",
            policy=self._pending_policy,
        )
        if payload is None:
            self._require_bound_root(root)
            return None
        intent = self._decode_transition(payload)
        self._require_bound_root(root)
        return intent

    def prepare_pending_transition(
        self,
        root: BoundRuntimeRoot,
        intent: ControlLeaseTransitionIntent,
    ) -> ControlLeaseTransitionIntent:
        """以 O_EXCL 固化唯一可恢复提交决定；同字节重试保持幂等。"""
        self._require_bound_root(root)
        self._require_transition(intent)
        payload = self._encode_transition(intent)
        path = control_lease_pending_transition_path(self._root)
        try:
            create_managed_bytes_exclusive_at(
                path,
                payload,
                root=root,
                policy=self._pending_policy,
            )
        except FileExistsError:
            existing = self.load_pending_transition_or_none(root)
            if existing != intent:
                raise ControlLeasePersistenceError(
                    "pending control lease transition 已存在且内容漂移",
                ) from None
            return existing
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise ControlLeasePersistenceError(
                "pending control lease transition 无法安全创建",
            ) from error
        self._require_bound_root(root)
        return intent

    def finalize_pending_transition(
        self,
        root: BoundRuntimeRoot,
        intent: ControlLeaseTransitionIntent,
        expected: ControlLeaseSnapshot | None,
    ) -> ControlLeaseSnapshot:
        """在同一外层锁内完成 freeze、CAS 与 pending 精确清理。"""
        self._require_bound_root(root)
        self._require_transition(intent)
        self._require_expected_intent_snapshot(intent, expected)
        self._require_pending_transition_exact(root, intent)
        self.freeze_history(root, intent.next_record)
        self._require_pending_transition_exact(root, intent)
        current = self.compare_and_swap_current(root, expected, intent.next_record)
        if current.record != intent.next_record:
            raise ControlLeasePersistenceError("current 未精确切换到 pending next_record")
        self._require_pending_transition_exact(root, intent)
        self.clear_pending_transition_exact(root, intent)
        return current

    def clear_pending_transition_exact(
        self,
        root: BoundRuntimeRoot,
        intent: ControlLeaseTransitionIntent,
    ) -> None:
        """只删除与已验证 intent 逐字节一致的 pending 叶子。"""
        self._require_bound_root(root)
        self._require_transition(intent)
        try:
            removed = remove_managed_bytes_exact_at(
                control_lease_pending_transition_path(self._root),
                self._encode_transition(intent),
                root=root,
                policy=self._pending_policy,
            )
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise ControlLeasePersistenceError(
                "pending control lease transition 无法安全精确清理",
            ) from error
        if not removed:
            raise ControlLeasePersistenceError("pending control lease transition 已缺失")
        self._require_bound_root(root)

    def load_history(
        self,
        root: BoundRuntimeRoot,
        attempt_id: str,
        record_sha256: str,
    ) -> ControlLeaseSnapshot:
        """严格读取由 attempt 与摘要定位的不可变 history record。"""
        self._require_bound_root(root)
        path = control_lease_history_path(self._root, attempt_id, record_sha256)
        snapshot = self._decode_snapshot(
            self._read_required(path, root, "control lease history"),
            "control lease history",
        )
        if snapshot.sha256 != record_sha256:
            raise ControlLeasePersistenceError(
                "control lease history 路径摘要与载荷不一致",
            )
        if snapshot.record.attempt_id != attempt_id:
            raise ControlLeasePersistenceError(
                "control lease history 路径 attempt 与载荷身份不一致",
            )
        self._require_bound_root(root)
        return snapshot

    def load_history_exact(
        self,
        root: BoundRuntimeRoot,
        record: ControlLeaseRecord,
    ) -> ControlLeaseSnapshot:
        """严格读取且证明 history 与给定规范 record 完全一致。"""
        self._require_record(record)
        return self._load_history_exact(root, record)

    def freeze_history(
        self,
        root: BoundRuntimeRoot,
        record: ControlLeaseRecord,
    ) -> ControlLeaseSnapshot:
        """先 O_EXCL 冻结不可变 history；相同规范 record 可恢复重试。"""
        self._require_bound_root(root)
        self._require_record(record)
        payload = self._encode_record(record)
        digest = control_lease_record_sha256(record)
        path = control_lease_history_path(self._root, record.attempt_id, digest)
        try:
            create_managed_bytes_exclusive_at(
                path,
                payload,
                root=root,
                policy=self._record_policy,
            )
        except FileExistsError:
            existing = self.load_history(root, record.attempt_id, digest)
            if existing.record != record:
                raise ControlLeasePersistenceError(
                    "control lease history 已存在且内容漂移",
                ) from None
            return existing
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise ControlLeasePersistenceError(
                "control lease history 无法安全冻结",
            ) from error
        self._require_bound_root(root)
        return ControlLeaseSnapshot(record=record, sha256=digest)

    def compare_and_swap_current(
        self,
        root: BoundRuntimeRoot,
        expected: ControlLeaseSnapshot | None,
        next_record: ControlLeaseRecord,
    ) -> ControlLeaseSnapshot:
        """在调用方已持部署锁时，以完整旧快照为前置条件发布 current。"""
        self._require_bound_root(root)
        if expected is not None and type(expected) is not ControlLeaseSnapshot:
            raise ControlLeasePersistenceError("current 期望快照类型无效")
        self._require_record(next_record)
        self._load_history_exact(root, next_record)
        path = control_lease_record_path(self._root)
        payload = self._encode_record(next_record)
        if expected is None:
            try:
                create_managed_bytes_exclusive_at(
                    path,
                    payload,
                    root=root,
                    policy=self._record_policy,
                )
            except FileExistsError:
                raise ControlLeasePersistenceError("current control lease CAS 已变化") from None
            except (ManagedFileError, RuntimeRootBindingError) as error:
                raise ControlLeasePersistenceError(
                    "current control lease 无法安全发布",
                ) from error
            return self.load_current_required(root)
        actual = self.load_current_required(root)
        self._require_current_snapshot(actual, expected)
        try:
            write_managed_bytes_atomic_at(
                path,
                payload,
                root=root,
                policy=self._record_policy,
            )
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise ControlLeasePersistenceError(
                "current control lease 无法安全切换",
            ) from error
        return self.load_current_required(root)

    def _load_history_exact(
        self,
        root: BoundRuntimeRoot,
        record: ControlLeaseRecord,
    ) -> ControlLeaseSnapshot:
        snapshot = self.load_history(
            root,
            record.attempt_id,
            control_lease_record_sha256(record),
        )
        if snapshot.record != record:
            raise ControlLeasePersistenceError(
                "control lease history 与记录内容漂移",
            )
        return snapshot

    def _read_required(
        self,
        path,
        root: BoundRuntimeRoot,
        label: str,
        *,
        policy=None,
    ) -> bytes:
        try:
            return read_managed_bytes_at(
                path,
                root=root,
                policy=self._record_policy if policy is None else policy,
            )
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise ControlLeasePersistenceError(f"{label} 无法安全加载") from error

    def _read_optional(
        self,
        path,
        root: BoundRuntimeRoot,
        label: str,
        *,
        policy=None,
    ) -> bytes | None:
        try:
            return read_optional_managed_bytes_at(
                path,
                root=root,
                policy=self._record_policy if policy is None else policy,
            )
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise ControlLeasePersistenceError(f"{label} 无法安全检查") from error

    def _decode_snapshot(self, payload: bytes, label: str) -> ControlLeaseSnapshot:
        try:
            record = decode_control_lease_record(payload)
            canonical = encode_control_lease_record(record)
        except FencingContractError as error:
            raise ControlLeasePersistenceError(f"{label} 无法严格解码") from error
        if canonical != payload:
            raise ControlLeasePersistenceError(f"{label} 不是规范序列化")
        return ControlLeaseSnapshot(
            record=record,
            sha256=control_lease_record_sha256(record),
        )

    def _encode_record(self, record: ControlLeaseRecord) -> bytes:
        try:
            return encode_control_lease_record(record)
        except FencingContractError as error:
            raise ControlLeasePersistenceError("control lease record 无法规范编码") from error

    def _decode_transition(self, payload: bytes) -> ControlLeaseTransitionIntent:
        try:
            return decode_control_lease_transition_intent(payload)
        except ControlLeaseTransitionError as error:
            raise ControlLeasePersistenceError(
                "pending control lease transition 无法严格解码",
            ) from error

    def _encode_transition(self, intent: ControlLeaseTransitionIntent) -> bytes:
        try:
            return encode_control_lease_transition_intent(intent)
        except ControlLeaseTransitionError as error:
            raise ControlLeasePersistenceError(
                "pending control lease transition 无法规范编码",
            ) from error

    def _require_pending_transition_exact(
        self,
        root: BoundRuntimeRoot,
        expected: ControlLeaseTransitionIntent,
    ) -> None:
        actual = self.load_pending_transition_or_none(root)
        if actual != expected:
            raise ControlLeasePersistenceError(
                "pending control lease transition 已变化或缺失",
            )

    def _require_expected_intent_snapshot(
        self,
        intent: ControlLeaseTransitionIntent,
        expected: ControlLeaseSnapshot | None,
    ) -> None:
        if expected is not None and type(expected) is not ControlLeaseSnapshot:
            raise ControlLeasePersistenceError("current 期望快照类型无效")
        expected_record = intent.expected_record
        if expected_record is None:
            if expected is not None:
                raise ControlLeasePersistenceError("pending expected_record 与 CAS 前置不一致")
            return
        if expected is None or expected.record != expected_record:
            raise ControlLeasePersistenceError("pending expected_record 与 CAS 前置不一致")

    def _require_transition(self, intent: ControlLeaseTransitionIntent) -> None:
        if type(intent) is not ControlLeaseTransitionIntent:
            raise ControlLeasePersistenceError("pending transition 类型无效")

    def _require_bound_root(self, root: BoundRuntimeRoot) -> None:
        try:
            self._policy.root_binding.require_bound(root)
        except RuntimeRootBindingError as error:
            raise ControlLeasePersistenceError("运行时根租约来自其他绑定或已失效") from error

    def _require_record(self, record: ControlLeaseRecord) -> None:
        if type(record) is not ControlLeaseRecord:
            raise ControlLeasePersistenceError("control lease record 类型无效")

    def _require_current_snapshot(
        self,
        actual: ControlLeaseSnapshot,
        expected: ControlLeaseSnapshot,
    ) -> None:
        if actual.sha256 != expected.sha256 or actual.record != expected.record:
            raise ControlLeasePersistenceError("current control lease CAS 已变化")


__all__ = ["ControlLeasePersistence", "ControlLeasePersistenceError"]
