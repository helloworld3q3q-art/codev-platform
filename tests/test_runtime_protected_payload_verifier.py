"""受保护回滚载荷 verifier 的 descriptor-safe 安全回归。"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import codev_platform.runtime_protected_payload_verifier as protected_verifier
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    create_managed_bytes_exclusive_at,
)
from codev_platform.runtime_protected_payload_verifier import (
    ProtectedPayloadVerificationError,
    RuntimeProtectedPayloadVerifier,
    ciphertext_payload_associated_data,
    protected_payload_identity_material,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBinding
from codev_platform.runtime_rollback_contract import (
    PayloadCategory,
    PayloadIdentityKind,
    PayloadLogicalRole,
    ProtectedPayloadRef,
)
from tests.runtime_rollback_test_support import protected_ref


_ROOT_POSIX = os.name == "posix" and os.geteuid() == 0


class _RejectingHmacKeyProvider:
    """公开或密文路径不得意外读取 HMAC 密钥。"""

    def __init__(self) -> None:
        self.calls = 0

    def load_hmac_key(self, _protection_context_sha256: str) -> bytes:
        self.calls += 1
        raise AssertionError("非 HMAC 载荷不得读取 HMAC 密钥")


class _RejectingCiphertextVerifier:
    """公开或 HMAC 路径不得意外进入密文上下文端口。"""

    def __init__(self) -> None:
        self.calls = 0

    def verify(
        self,
        _payload: object,
        _content: bytes,
        *,
        associated_data: bytes,
    ) -> None:
        del associated_data
        self.calls += 1
        raise AssertionError("非密文载荷不得调用密文上下文端口")


class _StaticHmacKeyProvider:
    """测试用 provider：只在调用期返回密钥，不把密钥交给 store。"""

    def __init__(self, key: bytes) -> None:
        self._key = key
        self.contexts: list[str] = []

    def load_hmac_key(self, protection_context_sha256: str) -> bytes:
        self.contexts.append(protection_context_sha256)
        return self._key


class _FailingHmacKeyProvider:
    """模拟不应泄露给公开调用方的底层密钥读取异常。"""

    def load_hmac_key(self, _protection_context_sha256: str) -> bytes:
        raise OSError("/run/credentials/runtime-hmac-key 不可读取")


class _RecordingCiphertextVerifier:
    """验证密文 AAD，并可模拟凭据撤销。"""

    def __init__(self, expected_associated_data: bytes) -> None:
        self._expected_associated_data = expected_associated_data
        self.calls: list[tuple[ProtectedPayloadRef, bytes, bytes]] = []
        self.rejected = False

    def verify(
        self,
        payload: ProtectedPayloadRef,
        content: bytes,
        *,
        associated_data: bytes,
    ) -> None:
        self.calls.append((payload, content, associated_data))
        if self.rejected or not hmac.compare_digest(
            associated_data,
            self._expected_associated_data,
        ):
            raise ValueError("密文保护上下文已撤销或 AAD 不匹配")


class _FailingCiphertextVerifier:
    """模拟不应泄露给公开调用方的底层凭据读取异常。"""

    def verify(
        self,
        _payload: ProtectedPayloadRef,
        _content: bytes,
        *,
        associated_data: bytes,
    ) -> None:
        del associated_data
        raise OSError("/run/credentials/runtime-ciphertext-context 不可读取")


@contextmanager
def _bound_root(tmp_path: Path) -> Iterator[BoundRuntimeRoot]:
    """建立 root:root 临时根，供真实 descriptor-safe 调用验证。"""
    root_path = tmp_path / "runtime"
    root_path.mkdir(mode=0o700)
    binding = RuntimeRootBinding(root_path, owner_uid=0)
    with binding.bind() as root:
        yield root


def _write_payload(
    payload: ProtectedPayloadRef,
    content: bytes,
    *,
    root: BoundRuntimeRoot,
) -> None:
    """按引用声明创建一次真实 root-owned 叶子。"""
    create_managed_bytes_exclusive_at(
        root.path / payload.relative_path,
        content,
        root=root,
        policy=ManagedFilePolicy(
            mode=payload.mode,
            require_uid=payload.uid,
            require_gid=payload.gid,
            max_bytes=1_048_576,
        ),
    )


def _forged_payload_with_mode(
    payload: ProtectedPayloadRef,
    mode: int,
) -> ProtectedPayloadRef:
    """仅在边界回归中绕过冻结 DTO，模拟不受信反序列化输入。"""
    forged = object.__new__(ProtectedPayloadRef)
    for field in dataclasses.fields(ProtectedPayloadRef):
        value = mode if field.name == "mode" else getattr(payload, field.name)
        object.__setattr__(forged, field.name, value)
    return forged


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def test公开载荷在同一根租约内校验文件元数据与摘要(tmp_path: Path) -> None:
    """公开载荷应通过 _at 读取，并把 mode、uid、gid 和 SHA 纳入同一验证。"""
    content = b"[Service]\nExecStart=/srv/codev/bin/serve\n"
    payload = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "codev-platform.service",
        identity_sha256=hashlib.sha256(content).hexdigest(),
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_RejectingHmacKeyProvider(),
        ciphertext_context_verifier=_RejectingCiphertextVerifier(),
    )

    with _bound_root(tmp_path) as root:
        _write_payload(payload, content, root=root)
        verifier.verify(payload, root=root)

        payload_path = root.path / payload.relative_path
        payload_path.unlink()
        external = root.path / "outside.service"
        external.write_bytes(content)
        payload_path.symlink_to(external)
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(payload, root=root)


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def test公开载荷拒绝gid漂移(tmp_path: Path) -> None:
    """受保护引用声明 root:root 时，真实叶子 gid 漂移必须闭锁。"""
    content = b"[Unit]\nDescription=codev runtime\n"
    payload = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "gid-checked.service",
        identity_sha256=hashlib.sha256(content).hexdigest(),
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_RejectingHmacKeyProvider(),
        ciphertext_context_verifier=_RejectingCiphertextVerifier(),
    )

    with _bound_root(tmp_path) as root:
        _write_payload(payload, content, root=root)
        os.chown(root.path / payload.relative_path, 0, 1)
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(payload, root=root)


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def test公开载荷拒绝权限漂移(tmp_path: Path) -> None:
    """受保护引用声明的精确 mode 漂移时，摘要正确也不得放行。"""
    content = b"[Unit]\nWants=network-online.target\n"
    payload = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "mode-checked.service",
        identity_sha256=hashlib.sha256(content).hexdigest(),
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_RejectingHmacKeyProvider(),
        ciphertext_context_verifier=_RejectingCiphertextVerifier(),
    )

    with _bound_root(tmp_path) as root:
        _write_payload(payload, content, root=root)
        os.chmod(root.path / payload.relative_path, 0o600)
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(payload, root=root)


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def test公开载荷拒绝祖先目录符号链接(tmp_path: Path) -> None:
    """descriptor-safe 读取不得沿 payload 父目录的符号链接继续解析。"""
    content = b"[Unit]\nAfter=network-online.target\n"
    payload = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "ancestor-link.service",
        identity_sha256=hashlib.sha256(content).hexdigest(),
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_RejectingHmacKeyProvider(),
        ciphertext_context_verifier=_RejectingCiphertextVerifier(),
    )

    with _bound_root(tmp_path) as root:
        _write_payload(payload, content, root=root)
        payload_path = root.path / payload.relative_path
        payload_path.unlink()
        parent = payload_path.parent
        parent.rmdir()
        external = root.path / "external-systemd"
        external.mkdir()
        (external / payload_path.name).write_bytes(content)
        parent.symlink_to(external, target_is_directory=True)
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(payload, root=root)


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def test公开载荷摘要漂移不会调用秘密端口(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公开 SHA 不匹配时必须在秘密端口前拒绝。"""
    content = b"expected-content"
    payload = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "digest.service",
        identity_sha256=hashlib.sha256(content).hexdigest(),
    )
    hmac_key_provider = _RejectingHmacKeyProvider()
    ciphertext_verifier = _RejectingCiphertextVerifier()
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=hmac_key_provider,
        ciphertext_context_verifier=ciphertext_verifier,
    )
    with _bound_root(tmp_path) as root:
        monkeypatch.setattr(
            protected_verifier,
            "read_managed_bytes_at",
            lambda _path, *, root, policy: b"changed-content",
        )
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(payload, root=root)

    assert hmac_key_provider.calls == 0
    assert ciphertext_verifier.calls == 0


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def testHMAC载荷绑定正文与引用元数据(tmp_path: Path) -> None:
    """同一正文被换 target_key 时，HMAC 身份必须拒绝重放。"""
    content = b"DATABASE_URL=postgresql://runtime\n"
    context_sha256 = "c" * 64
    unsigned = protected_ref(
        PayloadCategory.CONFIGURATION,
        PayloadLogicalRole.RUNTIME_CONFIGURATION,
        "runtime-env",
        identity_sha256="a" * 64,
        identity_kind=PayloadIdentityKind.HMAC_SHA256,
        protection_context_sha256=context_sha256,
    )
    provider = _StaticHmacKeyProvider(b"task6-hmac-test-key")
    payload = dataclasses.replace(
        unsigned,
        identity_sha256=hmac.new(
            b"task6-hmac-test-key",
            protected_payload_identity_material(unsigned, content),
            hashlib.sha256,
        ).hexdigest(),
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=provider,
        ciphertext_context_verifier=_RejectingCiphertextVerifier(),
    )

    with _bound_root(tmp_path) as root:
        _write_payload(payload, content, root=root)
        verifier.verify(payload, root=root)

        replayed = dataclasses.replace(payload, target_key="other-runtime-env")
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(replayed, root=root)

    assert provider.contexts == [context_sha256, context_sha256]


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def testHMAC密钥端口异常不泄露底层文本(tmp_path: Path) -> None:
    """密钥 provider 的系统异常必须被无秘密文本的安全错误收敛。"""
    content = b"DATABASE_URL=postgresql://runtime\n"
    payload = protected_ref(
        PayloadCategory.CONFIGURATION,
        PayloadLogicalRole.RUNTIME_CONFIGURATION,
        "provider-error.env",
        identity_sha256="a" * 64,
        identity_kind=PayloadIdentityKind.HMAC_SHA256,
        protection_context_sha256="e" * 64,
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_FailingHmacKeyProvider(),
        ciphertext_context_verifier=_RejectingCiphertextVerifier(),
    )

    with _bound_root(tmp_path) as root:
        _write_payload(payload, content, root=root)
        with pytest.raises(ProtectedPayloadVerificationError) as captured:
            verifier.verify(payload, root=root)

    assert captured.value.__cause__ is None
    assert "/run/credentials" not in str(captured.value)


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def test密文上下文端口异常不泄露底层文本(tmp_path: Path) -> None:
    """密文上下文端口的系统异常必须被无秘密文本的安全错误收敛。"""
    content = b"ciphertext-v1:\x00\x91\x04\xfe"
    payload = protected_ref(
        PayloadCategory.CONFIGURATION,
        PayloadLogicalRole.RUNTIME_CONFIGURATION,
        "context-error.env",
        identity_sha256=hashlib.sha256(content).hexdigest(),
        identity_kind=PayloadIdentityKind.CIPHERTEXT_SHA256,
        protection_context_sha256="f" * 64,
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_RejectingHmacKeyProvider(),
        ciphertext_context_verifier=_FailingCiphertextVerifier(),
    )

    with _bound_root(tmp_path) as root:
        _write_payload(payload, content, root=root)
        with pytest.raises(ProtectedPayloadVerificationError) as captured:
            verifier.verify(payload, root=root)

    assert captured.value.__cause__ is None
    assert "/run/credentials" not in str(captured.value)


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def test伪造载荷DTO也被收敛为安全错误(tmp_path: Path) -> None:
    """公开 verifier 不能仅依赖 store 已做过的 bundle 严格解码。"""
    content = b"[Unit]\nDescription=forged payload boundary\n"
    payload = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "forged.service",
        identity_sha256=hashlib.sha256(content).hexdigest(),
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_RejectingHmacKeyProvider(),
        ciphertext_context_verifier=_RejectingCiphertextVerifier(),
    )

    with _bound_root(tmp_path) as root:
        with pytest.raises(ProtectedPayloadVerificationError) as captured:
            verifier.verify(_forged_payload_with_mode(payload, 0o777), root=root)

    assert captured.value.__cause__ is None


@pytest.mark.skipif(
    not _ROOT_POSIX, reason="真实 root:root descriptor 验证仅在 root WSL/Linux 运行"
)
def test密文载荷先校验摘要再校验保护上下文(tmp_path: Path) -> None:
    """密文摘要正确仍必须经上下文端口批准，撤销后读取立即闭锁。"""
    content = b"ciphertext-v1:\x00\x91\x04\xfe"
    payload = protected_ref(
        PayloadCategory.CONFIGURATION,
        PayloadLogicalRole.RUNTIME_CONFIGURATION,
        "runtime.env.enc",
        identity_sha256=hashlib.sha256(content).hexdigest(),
        identity_kind=PayloadIdentityKind.CIPHERTEXT_SHA256,
        protection_context_sha256="d" * 64,
    )
    ciphertext_verifier = _RecordingCiphertextVerifier(
        ciphertext_payload_associated_data(payload),
    )
    verifier = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_RejectingHmacKeyProvider(),
        ciphertext_context_verifier=ciphertext_verifier,
    )

    with _bound_root(tmp_path) as root:
        _write_payload(payload, content, root=root)
        verifier.verify(payload, root=root)

        wrong_digest = dataclasses.replace(
            payload,
            identity_sha256=hashlib.sha256(b"other-ciphertext").hexdigest(),
        )
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(wrong_digest, root=root)
        assert ciphertext_verifier.calls == [
            (payload, content, ciphertext_payload_associated_data(payload))
        ]

        replayed = dataclasses.replace(payload, target_key="other-runtime-env")
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(replayed, root=root)

        ciphertext_verifier.rejected = True
        with pytest.raises(ProtectedPayloadVerificationError):
            verifier.verify(payload, root=root)

    assert ciphertext_verifier.calls == [
        (payload, content, ciphertext_payload_associated_data(payload)),
        (replayed, content, ciphertext_payload_associated_data(replayed)),
        (payload, content, ciphertext_payload_associated_data(payload)),
    ]
