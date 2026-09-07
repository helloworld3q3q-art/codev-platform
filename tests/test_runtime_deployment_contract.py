"""部署计划 schema 2 与旧 schema 1 审计边界测试。"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import canonical_sha256
from codev_platform.runtime_managed_file import (
    RootOwnedRegularFileSnapshot,
)
from codev_platform.ops.runtime_deploy import _public_receipt, load_deployment_plan
from codev_platform.runtime_deployment_contract import (
    DEPLOYMENT_PHASES,
    DeploymentPlan,
    DeploymentReceipt,
    LegacyDeploymentPlanAudit,
    LegacyDeploymentReceiptAudit,
    RuntimeDeploymentError,
    decode_deployment_plan,
    decode_legacy_deployment_plan_audit,
    decode_legacy_deployment_receipt_audit,
    encode_deployment_plan,
)
from codev_platform.runtime_deployment_coordinator import DeploymentCoordinator
from codev_platform.runtime_deployment_receipt import RuntimeDeploymentReceiptStore
from codev_platform.runtime_production_deployment import run_production_deployment
from tests.runtime_legacy_deployment_fixtures import (
    LEGACY_PLAN_GOLDEN_JSON,
    LEGACY_PLAN_GOLDEN_MAPPING,
    LEGACY_PLAN_GOLDEN_SHA256,
    LEGACY_RECEIPT_GOLDEN_JSON,
    LEGACY_RECEIPT_GOLDEN_MAPPING,
    legacy_plan_audit,
    legacy_receipt_audit,
)
from tests.runtime_contract_malicious_support import strict_json_mutations


def _plan(**changes: object) -> DeploymentPlan:
    values: dict[str, object] = {
        "schema_version": 2,
        "target_revision": "a" * 40,
        "source_repo": "/srv/codev-platform",
        "runtime_root": "/var/lib/codev-platform/runtime",
        "requirements_lock": "/srv/codev-platform/requirements.lock",
        "approved_requirements": "/srv/codev-platform/approved.txt",
        "wheelhouse": "/srv/codev-platform/wheelhouse",
        "candidate_root": "/var/lib/codev-platform/runtime/candidates",
        "config_source": "/srv/codev-platform/config.json",
        "environment_source": "/srv/codev-platform/runtime.env",
        "service_user": "codev",
        "project_id": "codev-platform",
        "require_cuda": True,
        "legacy_takeover_policy_sha256": None,
    }
    values.update(changes)
    return DeploymentPlan(**values)


def test_schema2_plan字段精确且不携带baseline_revision() -> None:
    assert tuple(field.name for field in dataclasses.fields(DeploymentPlan)) == (
        "schema_version",
        "target_revision",
        "source_repo",
        "runtime_root",
        "requirements_lock",
        "approved_requirements",
        "wheelhouse",
        "candidate_root",
        "config_source",
        "environment_source",
        "service_user",
        "project_id",
        "require_cuda",
        "legacy_takeover_policy_sha256",
    )
    plan = _plan()
    assert "baseline_revision" not in plan.to_mapping()
    assert plan.schema_version == 2


def test_schema2_plan摘要使用共享canonical实现() -> None:
    plan = _plan()
    assert plan.digest == canonical_sha256(plan.to_mapping())
    takeover = _plan(legacy_takeover_policy_sha256="d" * 64)
    assert takeover.digest != plan.digest


def test_schema2_plan接管策略构造参数可省略且规范映射保留null() -> None:
    values = _plan().to_mapping()
    values.pop("legacy_takeover_policy_sha256")

    plan = DeploymentPlan(**values)

    assert plan.legacy_takeover_policy_sha256 is None
    assert plan.to_mapping()["legacy_takeover_policy_sha256"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": 1},
        {"schema_version": True},
        {"legacy_takeover_policy_sha256": ""},
        {"legacy_takeover_policy_sha256": "A" * 64},
        {"legacy_takeover_policy_sha256": "0" * 64},
    ],
)
def test_schema2_plan拒绝旧版本与非法接管策略(changes: dict[str, object]) -> None:
    with pytest.raises(RuntimeDeploymentError):
        _plan(**changes)


@pytest.mark.parametrize(
    "project_id",
    (
        "Codev-platform",
        " codev-platform",
        "codev-platform ",
        "codev_platform",
        "codev.platform",
        "Legacy.Project_1",
        "a" * 65,
        "-codev",
        "",
        object(),
    ),
)
def test_schema2_plan拒绝非规范project_id且不静默归一化(project_id: object) -> None:
    with pytest.raises(RuntimeDeploymentError, match="项目标识"):
        _plan(project_id=project_id)


@pytest.mark.parametrize("project_id", ("a", "a" * 64, "a-1"))
def test_schema2_plan接受核心project_id合法边界(project_id: str) -> None:
    assert _plan(project_id=project_id).project_id == project_id


def _legacy_plan_payload() -> dict[str, object]:
    return dict(LEGACY_PLAN_GOLDEN_MAPPING)


def _legacy_receipt_payload() -> dict[str, object]:
    return dict(LEGACY_RECEIPT_GOLDEN_MAPPING)


def test_schema1历史golden字节映射与摘要均为冻结常量() -> None:
    plan = decode_legacy_deployment_plan_audit(LEGACY_PLAN_GOLDEN_JSON)
    receipt = decode_legacy_deployment_receipt_audit(LEGACY_RECEIPT_GOLDEN_JSON)

    assert json.loads(LEGACY_PLAN_GOLDEN_JSON) == LEGACY_PLAN_GOLDEN_MAPPING
    assert plan.to_mapping() == LEGACY_PLAN_GOLDEN_MAPPING
    assert plan.digest == LEGACY_PLAN_GOLDEN_SHA256
    assert json.loads(LEGACY_RECEIPT_GOLDEN_JSON) == LEGACY_RECEIPT_GOLDEN_MAPPING
    assert receipt.to_mapping() == LEGACY_RECEIPT_GOLDEN_MAPPING
    assert receipt.plan_sha256 == LEGACY_PLAN_GOLDEN_SHA256
    assert legacy_receipt_audit(plan) == receipt


def test_schema2旧baseline访问显式fail_closed而非attribute_error() -> None:
    with pytest.raises(RuntimeDeploymentError, match="schema 2"):
        _ = _plan().baseline_revision


def test_schema1_plan只通过显式审计类型读取() -> None:
    encoded = json.dumps(
        _legacy_plan_payload(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    audit = decode_legacy_deployment_plan_audit(encoded)

    assert type(audit) is LegacyDeploymentPlanAudit
    assert audit.schema_version == 1
    assert audit.baseline_revision == "b" * 40
    assert not isinstance(audit, DeploymentPlan)


def test_schema1审计解码拒绝未知重复字段与schema2伪装() -> None:
    unknown = _legacy_plan_payload()
    unknown["legacy_takeover_policy_sha256"] = "d" * 64
    with pytest.raises(RuntimeDeploymentError):
        decode_legacy_deployment_plan_audit(json.dumps(unknown).encode("utf-8"))
    schema2 = _legacy_plan_payload()
    schema2["schema_version"] = 2
    with pytest.raises(RuntimeDeploymentError):
        decode_legacy_deployment_plan_audit(json.dumps(schema2).encode("utf-8"))
    duplicate = json.dumps(_legacy_plan_payload()).replace(
        '"schema_version": 1',
        '"schema_version": 1, "schema_version": 1',
    )
    with pytest.raises(RuntimeDeploymentError):
        decode_legacy_deployment_plan_audit(duplicate.encode("utf-8"))


def test旧schema1_receipt只读且schema2计划不能创建或推进() -> None:
    encoded = json.dumps(
        _legacy_receipt_payload(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt = decode_legacy_deployment_receipt_audit(encoded)

    assert type(receipt) is LegacyDeploymentReceiptAudit
    assert DeploymentReceipt is LegacyDeploymentReceiptAudit
    with pytest.raises(RuntimeDeploymentError, match="只读审计"):
        DeploymentReceipt.start(_plan(), now="2026-07-19T10:00:00Z")
    with pytest.raises(RuntimeDeploymentError, match="只读审计"):
        receipt.apply(None, None, now="2026-07-19T10:01:00Z")  # type: ignore[arg-type]


def test_schema2_plan规范往返且v1不能伪装进入生产解码() -> None:
    plan = _plan()
    assert decode_deployment_plan(encode_deployment_plan(plan)) == plan
    with pytest.raises(RuntimeDeploymentError):
        decode_deployment_plan(json.dumps(_legacy_plan_payload()).encode("utf-8"))


def test_schema2_plan解码唯一允许缺失可选接管策略并补null() -> None:
    payload = _plan().to_mapping()
    payload.pop("legacy_takeover_policy_sha256")

    decoded = decode_deployment_plan(json.dumps(payload).encode("utf-8"))

    assert decoded.legacy_takeover_policy_sha256 is None
    assert b'"legacy_takeover_policy_sha256":null' in encode_deployment_plan(decoded)


def test_schema2_plan解码拒绝未知与重复字段() -> None:
    unknown = json.loads(encode_deployment_plan(_plan()))
    unknown["baseline_revision"] = "b" * 40
    with pytest.raises(RuntimeDeploymentError):
        decode_deployment_plan(json.dumps(unknown).encode("utf-8"))
    duplicate = encode_deployment_plan(_plan()).replace(
        b'"schema_version":2',
        b'"schema_version":2,"schema_version":2',
    )
    with pytest.raises(RuntimeDeploymentError):
        decode_deployment_plan(duplicate)


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"updated_at": "2026-07-19T10:00:00+00:00"},
        {"updated_at": "2026-02-30T10:00:00Z"},
    ],
)
def test_legacy_receipt审计仍严格校验schema与时间(changes: dict[str, object]) -> None:
    payload = {**_legacy_receipt_payload(), **changes}
    with pytest.raises(RuntimeDeploymentError):
        decode_legacy_deployment_receipt_audit(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    "changes",
    (
        {"status": "running", "failed_phase": "target_verified"},
        {
            "status": "complete",
            "completed_phases": [phase.value for phase in DEPLOYMENT_PHASES],
            "failed_phase": "ingress_opened",
        },
        {"status": "failed_safe", "failed_phase": None},
        {"status": "safety_unproven", "failed_phase": None},
        {
            "status": "failed_safe",
            "completed_phases": ["target_verified"],
            "failed_phase": "target_verified",
        },
        {
            "status": "failed_safe",
            "completed_phases": [phase.value for phase in DEPLOYMENT_PHASES],
            "failed_phase": None,
        },
    ),
)
def test_legacy_receipt状态与失败阶段必须一致(changes: dict[str, object]) -> None:
    payload = {**_legacy_receipt_payload(), **changes}
    with pytest.raises(RuntimeDeploymentError, match="状态|失败阶段"):
        decode_legacy_deployment_receipt_audit(json.dumps(payload).encode("utf-8"))


def test_legacy_receipt真实golden载荷规范往返() -> None:
    payload = {
        **_legacy_receipt_payload(),
        "status": "failed_safe",
        "completed_phases": ["target_verified", "artifacts_verified"],
        "base_id": "d" * 64,
        "failed_phase": "release_staged",
        "last_evidence_sha256": "e" * 64,
    }
    decoded = decode_legacy_deployment_receipt_audit(json.dumps(payload).encode("utf-8"))
    encoded = json.dumps(
        decoded.to_mapping(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert decode_legacy_deployment_receipt_audit(encoded) == decoded


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("target_revision", "a" * 39),
        ("target_revision", "A" * 40),
        ("runtime_root", "/tmp/runtime"),
        ("service_user", "root"),
        ("service_user", "Codev"),
        ("service_user", "codev;id"),
        ("service_user", "codev worker"),
        ("service_user", "codev$(id)"),
        ("project_id", ""),
        ("project_id", "bad/project"),
        ("require_cuda", 1),
    ),
)
def test_schema2_plan恢复关键安全字段门禁(field: str, value: object) -> None:
    with pytest.raises(RuntimeDeploymentError):
        _plan(**{field: value})


@pytest.mark.parametrize(
    "field",
    (
        "source_repo",
        "requirements_lock",
        "approved_requirements",
        "wheelhouse",
        "candidate_root",
        "config_source",
        "environment_source",
    ),
)
@pytest.mark.parametrize(
    "value",
    (
        "relative/path",
        "/",
        "/safe//path",
        "/safe/./path",
        "/safe/../path",
        "/safe/path/.",
        "/safe/path/..",
        "C:/windows/path",
    ),
)
def test_schema2_plan所有路径必须是规范posix绝对路径(field: str, value: str) -> None:
    with pytest.raises(RuntimeDeploymentError, match=field):
        _plan(**{field: value})


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        pytest.param(decoder, payload, id=f"{name}-{mutation}")
        for name, decoder, encoded, wrong_field in (
            (
                "schema2-plan",
                decode_deployment_plan,
                encode_deployment_plan(_plan()),
                "service_user",
            ),
            (
                "schema1-plan-audit",
                decode_legacy_deployment_plan_audit,
                LEGACY_PLAN_GOLDEN_JSON,
                "service_user",
            ),
            (
                "schema1-receipt-audit",
                decode_legacy_deployment_receipt_audit,
                LEGACY_RECEIPT_GOLDEN_JSON,
                "status",
            ),
        )
        for mutation, payload in strict_json_mutations(
            encoded,
            max_bytes=32_768,
            wrong_field=wrong_field,
        )
    ],
)
def test_deployment每个decoder逐项拒绝单变量恶意载荷(
    decoder,
    payload: object,
) -> None:
    with pytest.raises(RuntimeDeploymentError):
        decoder(payload)  # type: ignore[arg-type]


def test_schema2进入旧coordinator时在任何端口副作用前受控拒绝() -> None:
    touched: list[str] = []

    class Store:
        def lock(self, _plan):
            touched.append("lock")
            raise AssertionError("不应触达旧锁")

        def load(self, _plan):
            touched.append("load")
            raise AssertionError("不应读取旧回执")

        def save(self, _plan, _receipt):
            touched.append("save")
            raise AssertionError("不应写入旧回执")

    coordinator = DeploymentCoordinator(
        store=Store(),  # type: ignore[arg-type]
        steps=(),
        settle_failure=lambda *_args: False,
        verify_boundary=lambda *_args: touched.append("boundary"),
        now=lambda: "2026-07-19T10:00:00Z",
    )

    with pytest.raises(RuntimeDeploymentError, match="schema 2.*旧部署"):
        coordinator.run(_plan())
    assert touched == []


def test_schema2进入旧receipt_store时在锁读写前受控拒绝(monkeypatch) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    touched: list[str] = []
    monkeypatch.setattr(
        receipt_module,
        "deployment_lock",
        lambda _root: touched.append("lock"),
    )
    monkeypatch.setattr(
        receipt_module,
        "read_optional_root_owned_regular_file_snapshot",
        lambda *_args, **_kwargs: touched.append("read"),
    )
    monkeypatch.setattr(
        receipt_module,
        "write_root_owned_regular_file_atomic",
        lambda *_args, **_kwargs: touched.append("write"),
        raising=False,
    )
    plan = _plan()
    store = RuntimeDeploymentReceiptStore(Path(plan.runtime_root))
    receipt = decode_legacy_deployment_receipt_audit(
        json.dumps(_legacy_receipt_payload()).encode("utf-8")
    )

    actions = (
        lambda: store.lock(plan),
        lambda: store.load(plan),
        lambda: store.save(plan, receipt),
    )
    for action in actions:
        with pytest.raises(RuntimeDeploymentError, match="schema 2.*旧部署"):
            action()
    assert touched == []


def test安全文件加载层读取schema2并拒绝schema1(tmp_path: Path) -> None:
    path = tmp_path / "deployment-plan.json"
    path.write_bytes(encode_deployment_plan(_plan()))
    assert load_deployment_plan(path) == _plan()

    path.write_text(json.dumps(_legacy_plan_payload()), encoding="utf-8")
    with pytest.raises(RuntimeDeploymentError, match="部署计划"):
        load_deployment_plan(path)


def test_schema2进入旧production_runner时依赖与根初始化零调用(monkeypatch) -> None:
    touched: list[str] = []

    def dependencies():
        touched.append("dependencies")
        raise AssertionError("不应初始化旧生产依赖")

    monkeypatch.setattr(
        "codev_platform.runtime_production_deployment._default_dependencies",
        dependencies,
    )

    with pytest.raises(RuntimeDeploymentError, match="schema 2.*旧部署"):
        run_production_deployment(_plan())
    assert touched == []


def test_cli_public_receipt拒绝把v1审计回执当作schema2成功结果() -> None:
    plan = _plan(target_revision="c" * 40)
    legacy_plan = legacy_plan_audit()
    receipt = legacy_receipt_audit(
        legacy_plan,
        completed=len(DEPLOYMENT_PHASES),
        status="complete",
        release_id="d" * 64,
    )

    assert receipt.plan_sha256 == LEGACY_PLAN_GOLDEN_SHA256
    assert receipt.target_revision == legacy_plan.target_revision
    assert receipt.plan_sha256 != plan.digest
    assert receipt.target_revision != plan.target_revision

    with pytest.raises(RuntimeDeploymentError, match="旧 schema 1.*成功"):
        _public_receipt(plan, receipt)


def test_receipt_store只读加载可信v1审计并核对三重身份(monkeypatch) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    plan = decode_legacy_deployment_plan_audit(LEGACY_PLAN_GOLDEN_JSON)
    encoded = LEGACY_RECEIPT_GOLDEN_JSON
    reads: list[Path] = []

    def read(path: Path, *, max_bytes: int):
        reads.append(path)
        assert max_bytes == 32 * 1024
        return RootOwnedRegularFileSnapshot(encoded, 0o600, 0, 0)

    monkeypatch.setattr(
        receipt_module,
        "read_optional_root_owned_regular_file_snapshot",
        read,
    )
    store = RuntimeDeploymentReceiptStore(Path(plan.runtime_root))

    assert store.load_legacy_audit(plan) == decode_legacy_deployment_receipt_audit(encoded)
    assert reads == [
        Path(plan.runtime_root) / "deployments" / f"{plan.target_revision}.json"
    ]


@pytest.mark.parametrize(
    "field",
    ["plan_sha256", "target_revision", "baseline_revision"],
)
def test_receipt_store拒绝v1审计回执身份漂移(monkeypatch, field: str) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    plan = decode_legacy_deployment_plan_audit(
        json.dumps(_legacy_plan_payload()).encode("utf-8")
    )
    changes = {
        "plan_sha256": "d" * 64,
        "target_revision": "e" * 40,
        "baseline_revision": "f" * 40,
    }
    payload = {
        **_legacy_receipt_payload(),
        "plan_sha256": plan.digest,
        "target_revision": plan.target_revision,
        "baseline_revision": plan.baseline_revision,
        field: changes[field],
    }
    encoded = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(
        receipt_module,
        "read_optional_root_owned_regular_file_snapshot",
        lambda *_args, **_kwargs: RootOwnedRegularFileSnapshot(encoded, 0o600, 0, 0),
    )

    with pytest.raises(RuntimeDeploymentError, match="审计回执与计划身份"):
        RuntimeDeploymentReceiptStore(Path(plan.runtime_root)).load_legacy_audit(plan)


def test_receipt_store对v1与v2均拒绝写入且writer零调用(monkeypatch) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    touched: list[str] = []
    monkeypatch.setattr(
        receipt_module,
        "write_root_owned_regular_file_atomic",
        lambda *_args, **_kwargs: touched.append("write"),
        raising=False,
    )
    legacy_plan = decode_legacy_deployment_plan_audit(
        json.dumps(_legacy_plan_payload()).encode("utf-8")
    )
    receipt = decode_legacy_deployment_receipt_audit(
        json.dumps(_legacy_receipt_payload()).encode("utf-8")
    )
    store = RuntimeDeploymentReceiptStore(Path(legacy_plan.runtime_root))

    for plan in (legacy_plan, _plan()):
        with pytest.raises(RuntimeDeploymentError):
            store.save(plan, receipt)  # type: ignore[arg-type]
    assert touched == []
