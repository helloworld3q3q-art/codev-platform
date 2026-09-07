"""回滚包受保护载荷、路径与 manifest 契约测试。"""

from __future__ import annotations

import dataclasses
import json

import pytest

from codev_platform.mcp_systemd_unit_registry import MANAGED_SYSTEMD_UNIT_NAMES
from codev_platform.runtime_rollback_contract import (
    PayloadCategory,
    PayloadIdentityKind,
    PayloadLogicalRole,
    ProtectedPayloadRef,
    RollbackContractError,
    configuration_manifest_sha256,
    decode_protected_payload_ref,
    encode_protected_payload_ref,
    systemd_receipt_manifest_sha256,
)
from tests.runtime_rollback_test_support import (
    ATTEMPT_ID,
    bound_bundle,
    protected_groups,
    protected_ref,
)


def test_payload模型冻结且分类身份值精确() -> None:
    assert tuple(item.value for item in PayloadCategory) == (
        "systemd",
        "configuration",
        "receipt",
    )
    assert tuple(item.value for item in PayloadIdentityKind) == (
        "sha256-v1",
        "hmac-sha256-v1",
        "ciphertext-sha256-v1",
    )
    assert tuple(field.name for field in dataclasses.fields(ProtectedPayloadRef)) == (
        "category",
        "logical_role",
        "target_key",
        "relative_path",
        "identity_kind",
        "identity_sha256",
        "protection_context_sha256",
        "mode",
        "uid",
        "gid",
    )
    assert ProtectedPayloadRef.__dataclass_params__.frozen is True
    assert "__dict__" not in ProtectedPayloadRef.__slots__


def test_三类合法载荷绑定角色命名空间权限与保护上下文() -> None:
    systemd, configuration, receipts = protected_groups()

    assert systemd[0].logical_role is PayloadLogicalRole.SYSTEMD_UNIT
    assert systemd[0].target_key == "codev-platform.service"
    assert systemd[0].mode == 0o644
    assert systemd[0].protection_context_sha256 is None
    assert configuration[0].identity_kind is PayloadIdentityKind.CIPHERTEXT_SHA256
    assert configuration[0].mode == 0o600
    assert configuration[0].protection_context_sha256 == "b" * 64
    assert receipts[0].mode == 0o600


@pytest.mark.parametrize(
    "path",
    (
        "",
        "/absolute/path",
        "C:/absolute/path",
        "../escape",
        "safe/../escape",
        "safe//duplicate",
        "safe/./dot",
        "safe/",
        "safe\\windows",
        "safe/\x00nul",
        "safe/\x1fcontrol",
        "control/active-generation.json",
        (
            f"rollback-bundles/{ATTEMPT_ID}/protected/"
            "configuration/codev-platform.service"
        ),
        f"{'a' * 256}/payload",
        "a" * 4096,
    ),
)
def test_payload路径拒绝不规范控制面与错分类命名空间(path: str) -> None:
    systemd = protected_groups()[0][0]
    with pytest.raises(RollbackContractError, match="relative_path|命名空间"):
        dataclasses.replace(systemd, relative_path=path)


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"logical_role": PayloadLogicalRole.RUNTIME_CONFIGURATION}, "logical_role"),
        ({"mode": 0o600}, "mode"),
        ({"mode": 0o755}, "mode"),
        ({"uid": True}, "uid"),
        ({"uid": -1}, "uid"),
        ({"gid": 2**32}, "gid"),
        ({"uid": 1}, "root:root"),
        ({"protection_context_sha256": "c" * 64}, "不得携带"),
    ),
)
def test_systemd拒绝错角色权限owner与伪上下文(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RollbackContractError, match=message):
        dataclasses.replace(protected_groups()[0][0], **changes)


@pytest.mark.parametrize(
    "changes",
    (
        {"mode": 0o644},
        {"mode": 0o755},
        {"uid": 1},
        {"gid": 1},
        {"identity_kind": PayloadIdentityKind.PUBLIC_SHA256},
        {"protection_context_sha256": None},
        {"protection_context_sha256": "0" * 64},
    ),
)
def test_configuration拒绝公开身份弱权限非root与缺失上下文(
    changes: dict[str, object],
) -> None:
    with pytest.raises(RollbackContractError):
        dataclasses.replace(protected_groups()[1][0], **changes)


def test_receipt只接受root_0600且公开身份无上下文() -> None:
    receipt = protected_groups()[2][0]
    with pytest.raises(RollbackContractError, match="mode"):
        dataclasses.replace(receipt, mode=0o644)
    with pytest.raises(RollbackContractError, match="不得携带"):
        dataclasses.replace(receipt, protection_context_sha256="c" * 64)


@pytest.mark.parametrize(
    "target_key",
    (
        "",
        ".",
        "..",
        "unit/name",
        "unit\\name",
        "Unit.service",
        "unit service",
        "unit\x00service",
        "服务.service",
        "a" * 256,
    ),
)
def test_target_key拒绝路径语义控制字符非ASCII与超长值(target_key: str) -> None:
    with pytest.raises(RollbackContractError, match="target_key"):
        dataclasses.replace(protected_groups()[0][0], target_key=target_key)


def test_systemd同角色可承载真实多service_timer与drop_in目标() -> None:
    services = (
        "codev-mcp-platform-docs.service",
        "codev-mcp-codegraph.service",
    )
    timers = (
        "codev-clock-resync.timer",
        "codev-memory-maintenance.timer",
    )
    assert set((*services, *timers)) <= MANAGED_SYSTEMD_UNIT_NAMES
    payloads = [
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_UNIT,
            unit,
            target_key=unit,
        )
        for unit in (*services, *timers)
    ]
    payloads.extend(
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_DROP_IN,
            f"drop-ins/{unit}.d/10-runtime.conf",
            target_key=f"{unit}@10-runtime.conf",
        )
        for unit in services
    )
    systemd = tuple(sorted(payloads, key=lambda item: item.relative_path))

    bundle = dataclasses.replace(bound_bundle(), systemd_payloads=systemd)

    assert len(bundle.systemd_payloads) == 6
    assert sum(
        item.logical_role is PayloadLogicalRole.SYSTEMD_UNIT
        for item in bundle.systemd_payloads
    ) == 4
    assert sum(
        item.logical_role is PayloadLogicalRole.SYSTEMD_DROP_IN
        for item in bundle.systemd_payloads
    ) == 2


def test_同分类仅target_key唯一而logical_role允许重复() -> None:
    first = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "first.service",
        target_key="same.service",
    )
    second = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "second.service",
        target_key="same.service",
    )

    with pytest.raises(RollbackContractError, match="target_key"):
        dataclasses.replace(bound_bundle(), systemd_payloads=(first, second))


def test_bundle路径祖先检查不受中间非后代路径干扰() -> None:
    prefix = f"rollback-bundles/{ATTEMPT_ID}/protected/systemd"
    payloads = (
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_UNIT,
            "a",
        ),
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_DROP_IN,
            "a-keep",
        ),
        protected_ref(
            PayloadCategory.SYSTEMD,
            PayloadLogicalRole.SYSTEMD_SOCKET_UNIT,
            "a/child",
        ),
    )
    assert tuple(item.relative_path for item in payloads) == (
        f"{prefix}/a",
        f"{prefix}/a-keep",
        f"{prefix}/a/child",
    )
    with pytest.raises(RollbackContractError, match="冲突"):
        dataclasses.replace(bound_bundle(), systemd_payloads=payloads)


def test_manifest摘要排除attempt命名空间但绑定权威载荷事实() -> None:
    systemd, configuration, receipts = protected_groups(attempt_id="1" * 32)
    other_systemd, other_configuration, other_receipts = protected_groups(
        attempt_id="2" * 32
    )

    assert systemd_receipt_manifest_sha256(systemd, receipts) == (
        systemd_receipt_manifest_sha256(other_systemd, other_receipts)
    )
    assert configuration_manifest_sha256(configuration) == (
        configuration_manifest_sha256(other_configuration)
    )
    changed = dataclasses.replace(systemd[0], identity_sha256="c" * 64)
    assert systemd_receipt_manifest_sha256((changed,), receipts) != (
        systemd_receipt_manifest_sha256(systemd, receipts)
    )
    changed_key = dataclasses.replace(systemd[0], target_key="other.service")
    assert systemd_receipt_manifest_sha256((changed_key,), receipts) != (
        systemd_receipt_manifest_sha256(systemd, receipts)
    )
    changed_role = dataclasses.replace(
        systemd[0],
        logical_role=PayloadLogicalRole.SYSTEMD_DROP_IN,
    )
    assert systemd_receipt_manifest_sha256((changed_role,), receipts) != (
        systemd_receipt_manifest_sha256(systemd, receipts)
    )


def test_payload规范往返并保留受限枚举而无destination() -> None:
    payload = protected_groups()[1][0]
    encoded = encode_protected_payload_ref(payload)

    assert decode_protected_payload_ref(encoded) == payload
    assert b'"category":"configuration"' in encoded
    assert b'"logical_role":"runtime-configuration"' in encoded
    assert b'"target_key":"runtime.env.enc"' in encoded
    assert b"destination" not in encoded


def test_payload解码拒绝嵌套未知重复与错误枚举() -> None:
    encoded = encode_protected_payload_ref(protected_groups()[0][0])
    unknown = json.loads(encoded)
    unknown["destination"] = "/etc/systemd/system/codev-platform.service"
    with pytest.raises(RollbackContractError):
        decode_protected_payload_ref(json.dumps(unknown).encode("utf-8"))
    missing = json.loads(encoded)
    missing.pop("target_key")
    with pytest.raises(RollbackContractError):
        decode_protected_payload_ref(json.dumps(missing).encode("utf-8"))
    duplicate = encoded.replace(b'"uid":0', b'"uid":0,"uid":0')
    with pytest.raises(RollbackContractError):
        decode_protected_payload_ref(duplicate)
    bad = json.loads(encoded)
    bad["logical_role"] = "arbitrary-destination"
    with pytest.raises(RollbackContractError):
        decode_protected_payload_ref(json.dumps(bad).encode("utf-8"))
