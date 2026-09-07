"""回滚包受控存储的安全边界回归。"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import codev_platform.runtime_rollback_store as rollback_store_module
from codev_platform.runtime_rollback_contract import (
    ProtectedPayloadRef,
    RollbackBundle,
    encode_rollback_bundle,
)
from codev_platform.runtime_attempt_contract import encode_deployment_attempt
from codev_platform.runtime_managed_file import (
    create_managed_bytes_exclusive_at,
    remove_managed_bytes_exact_at,
    write_managed_bytes_atomic_at,
)
from codev_platform.runtime_rollback_store import (
    RuntimeRollbackStoreError,
    _InjectedRuntimeRollbackStore,
)
from codev_platform.runtime_storage import (
    attempt_record_path,
    rollback_bundle_path,
    rollback_bundle_pending_path,
)
from codev_platform.runtime_store_protocols import private_managed_file_policy
from tests.runtime_generation_store_support import build_generation_store_input
from tests.runtime_rollback_test_support import bound_bundle
from tests.runtime_rollback_store_support import (
    build_rollback_store_input,
    valid_bundle,
)


_POSIX = __import__("os").name == "posix"


class _RejectingGate:
    """证明公开输入校验失败时 store 不得进入控制临界区。"""

    def __init__(self) -> None:
        self.entered = False

    @contextmanager
    def mutation(self, _proof: object) -> Iterator[object]:
        self.entered = True
        raise AssertionError("越界回滚包不得进入 gate")
        yield None


class _RecordingPayloadVerifier:
    """记录 verifier 是否被调用；本用例的越界输入不得抵达该边界。"""

    def __init__(self) -> None:
        self.payloads: list[ProtectedPayloadRef] = []

    def verify(self, payload: ProtectedPayloadRef, *, root: object) -> None:
        del root
        self.payloads.append(payload)


def _forged_payload_with_path(
    payload: ProtectedPayloadRef,
    relative_path: str,
) -> ProtectedPayloadRef:
    """仅在安全回归中绕过冻结 DTO，模拟不受信反序列化输入。"""
    forged = object.__new__(ProtectedPayloadRef)
    for field in dataclasses.fields(ProtectedPayloadRef):
        value = relative_path if field.name == "relative_path" else getattr(payload, field.name)
        object.__setattr__(forged, field.name, value)
    return forged


def _forged_bundle_with_systemd_payload(
    bundle: RollbackBundle,
    payload: ProtectedPayloadRef,
) -> RollbackBundle:
    """仅在安全回归中构造绕过构造期校验的 bundle。"""
    forged = object.__new__(RollbackBundle)
    for field in dataclasses.fields(RollbackBundle):
        value = (payload,) if field.name == "systemd_payloads" else getattr(bundle, field.name)
        object.__setattr__(forged, field.name, value)
    return forged


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test回滚包在进入gate前拒绝越界受保护路径(tmp_path: Path) -> None:
    """冻结 dataclass 被绕过时，store 仍须重新严格验证受保护路径。"""
    inputs = build_generation_store_input(tmp_path)
    gate = _RejectingGate()
    verifier = _RecordingPayloadVerifier()
    store = _InjectedRuntimeRollbackStore(inputs.policy, gate, verifier)
    bundle = bound_bundle()
    forged = _forged_bundle_with_systemd_payload(
        bundle,
        _forged_payload_with_path(bundle.systemd_payloads[0], "../outside"),
    )

    with pytest.raises(RuntimeRollbackStoreError):
        store.write_bundle_once(forged, proof=inputs.proof)

    assert gate.entered is False
    assert verifier.payloads == []
    assert not rollback_bundle_path(inputs.root, forged.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test回滚包暂存封口与同字节重试均复验全部载荷(tmp_path: Path) -> None:
    """pending→final 封口与后续读取都必须重新验证全部载荷。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()

    first = inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    retried = inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    loaded = inputs.store.load_bundle(bundle.attempt_id)

    assert first.value == bundle
    assert retried == first
    assert loaded == bundle
    assert len(inputs.verifier.calls) == 12


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test回滚包既存时created_at漂移也必须冲突(tmp_path: Path) -> None:
    """幂等比较必须使用完整 bytes，不能使用排除创建时间的身份摘要。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    changed = dataclasses.replace(bundle, created_at="2026-07-21T11:02:00Z")

    with pytest.raises(RuntimeRollbackStoreError):
        inputs.store.write_bundle_once(changed, proof=inputs.proof)

    assert inputs.store.load_bundle(bundle.attempt_id) == bundle


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test回滚包后验校验失败只留下pending不发布final(tmp_path: Path) -> None:
    """第二阶段校验拒绝时，读取者不得看见尚未封口的 bundle。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.verifier.fail_at_call = 4

    with pytest.raises(RuntimeRollbackStoreError):
        inputs.store.write_bundle_once(bundle, proof=inputs.proof)

    assert rollback_bundle_pending_path(inputs.root, bundle.attempt_id).exists()
    assert not rollback_bundle_path(inputs.root, bundle.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test同字节pending可恢复封口为final(tmp_path: Path) -> None:
    """进程在 pending 后退出时，同字节重试必须只恢复 final 封口。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    policy = private_managed_file_policy(inputs.policy.owner_uid, 1_048_576)
    with inputs.policy.root_binding.bind() as root:
        create_managed_bytes_exclusive_at(
            rollback_bundle_pending_path(inputs.root, bundle.attempt_id),
            encode_rollback_bundle(bundle),
            root=root,
            policy=policy,
        )

    stored = inputs.store.write_bundle_once(bundle, proof=inputs.proof)

    assert stored.value == bundle
    assert rollback_bundle_path(inputs.root, bundle.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test无候选输入也可恢复已持久pending(tmp_path: Path) -> None:
    """进程重启后只凭活动 proof 和 pending 本身即可安全完成封口。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    policy = private_managed_file_policy(inputs.policy.owner_uid, 1_048_576)
    with inputs.policy.root_binding.bind() as root:
        create_managed_bytes_exclusive_at(
            rollback_bundle_pending_path(inputs.root, bundle.attempt_id),
            encode_rollback_bundle(bundle),
            root=root,
            policy=policy,
        )

    stored = inputs.store.recover_pending_bundle(proof=inputs.proof)

    assert stored.value == bundle
    assert rollback_bundle_path(inputs.root, bundle.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test异值既存回滚包不触发新的载荷校验(tmp_path: Path) -> None:
    """既存 final 的完整 bytes 不同应先冲突，不能额外触达秘密端口。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    before = len(inputs.verifier.calls)
    changed = dataclasses.replace(bundle, created_at="2026-07-21T11:02:00Z")

    with pytest.raises(RuntimeRollbackStoreError):
        inputs.store.write_bundle_once(changed, proof=inputs.proof)

    assert len(inputs.verifier.calls) == before


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test异值final不得遗留本次创建的pending(tmp_path: Path) -> None:
    """历史 final 缺少 pending 时，异值冲突也不得污染恢复锚点。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    policy = private_managed_file_policy(inputs.policy.owner_uid, 1_048_576)
    with inputs.policy.root_binding.bind() as root:
        assert remove_managed_bytes_exact_at(
            rollback_bundle_pending_path(inputs.root, bundle.attempt_id),
            encode_rollback_bundle(bundle),
            root=root,
            policy=policy,
        )
    changed = dataclasses.replace(bundle, created_at="2026-07-21T11:03:00Z")

    with pytest.raises(RuntimeRollbackStoreError):
        inputs.store.write_bundle_once(changed, proof=inputs.proof)

    assert not rollback_bundle_pending_path(inputs.root, bundle.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test既存final在pending发布前直接返回(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同字节 final 重试不得再尝试 pending O_EXCL 写入或补偿删除。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    original_create = rollback_store_module.create_managed_bytes_exclusive_at
    created_paths: list[Path] = []

    def recording_create(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        created_paths.append(path)
        return original_create(path, payload, root=root, policy=policy)

    monkeypatch.setattr(
        rollback_store_module,
        "create_managed_bytes_exclusive_at",
        recording_create,
    )

    assert inputs.store.write_bundle_once(bundle, proof=inputs.proof).value == bundle
    assert created_paths == []


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test受保护载荷校验失败时不发布且读取再次闭锁(tmp_path: Path) -> None:
    """HMAC、密文上下文或文件元数据由注入 verifier 拒绝时不得绕过。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.verifier.rejected_identity_sha256 = bundle.configuration_payloads[0].identity_sha256

    with pytest.raises(RuntimeRollbackStoreError):
        inputs.store.write_bundle_once(bundle, proof=inputs.proof)

    assert not rollback_bundle_path(inputs.root, bundle.attempt_id).exists()
    inputs.verifier.rejected_identity_sha256 = None
    inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    inputs.verifier.rejected_identity_sha256 = bundle.configuration_payloads[0].identity_sha256
    with pytest.raises(RuntimeRollbackStoreError):
        inputs.store.load_bundle(bundle.attempt_id)


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test读取将verifier运行期错误收敛为store错误(tmp_path: Path) -> None:
    """读取期不得把注入 verifier 的内部 RuntimeError 泄露给调用方。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    inputs.verifier.runtime_error_identity_sha256 = bundle.configuration_payloads[0].identity_sha256

    with pytest.raises(RuntimeRollbackStoreError) as captured:
        inputs.store.load_bundle(bundle.attempt_id)

    assert type(captured.value) is RuntimeRollbackStoreError
    assert captured.value.__cause__ is None


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test读取将verifier系统异常收敛为无泄露store错误(tmp_path: Path) -> None:
    """即使测试替身直接抛出 OSError，公开 store 边界也不得泄露其原因。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    inputs.verifier.os_error_identity_sha256 = bundle.configuration_payloads[0].identity_sha256

    with pytest.raises(RuntimeRollbackStoreError) as captured:
        inputs.store.load_bundle(bundle.attempt_id)

    assert captured.value.__cause__ is None
    assert "/run/credentials" not in str(captured.value)


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test读取拒绝冻结attempt的持久绑定漂移(tmp_path: Path) -> None:
    """bundle 写入后即使记录被替换，读取也必须重新核对绑定。"""
    inputs = build_rollback_store_input(tmp_path)
    bundle = valid_bundle()
    inputs.store.write_bundle_once(bundle, proof=inputs.proof)
    drifted = dataclasses.replace(
        inputs.facts.attempt,
        baseline_observation_sha256="e" * 64,
    )
    policy = private_managed_file_policy(inputs.policy.owner_uid, 16_384)
    with inputs.policy.root_binding.bind() as root:
        write_managed_bytes_atomic_at(
            attempt_record_path(inputs.root, bundle.attempt_id),
            encode_deployment_attempt(drifted),
            root=root,
            policy=policy,
        )

    with pytest.raises(RuntimeRollbackStoreError):
        inputs.store.load_bundle(bundle.attempt_id)


@pytest.mark.skipif(not _POSIX, reason="受管文件与控制 scope 仅在 WSL/Linux 验证")
def test回滚包拒绝与冻结attempt不一致的计划摘要(tmp_path: Path) -> None:
    """当前 lease 不能把其他计划的自洽 bundle 冻结到当前 attempt 路径。"""
    inputs = build_rollback_store_input(tmp_path)
    changed = dataclasses.replace(valid_bundle(), plan_sha256="f" * 64)

    with pytest.raises(RuntimeRollbackStoreError):
        inputs.store.write_bundle_once(changed, proof=inputs.proof)

    assert inputs.verifier.calls == []
    assert not rollback_bundle_path(inputs.root, changed.attempt_id).exists()
