"""冻结 schema 1 project_id 的只读审计兼容边界测试。"""

from __future__ import annotations

import json

import pytest

from codev_platform.runtime_deployment_contract import (
    RuntimeDeploymentError,
    decode_legacy_deployment_plan_audit,
    decode_legacy_deployment_receipt_audit,
)
from tests.runtime_legacy_deployment_fixtures import (
    LEGACY_PLAN_GOLDEN_MAPPING,
    legacy_receipt_audit,
)


def _legacy_plan_payload() -> dict[str, object]:
    return dict(LEGACY_PLAN_GOLDEN_MAPPING)


def test_schema1审计解码接受历史project_id且保持原值() -> None:
    payload = _legacy_plan_payload()
    payload["project_id"] = "Legacy.Project_1"
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    audit = decode_legacy_deployment_plan_audit(encoded)
    receipt = legacy_receipt_audit(audit)
    encoded_receipt = json.dumps(
        receipt.to_mapping(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    assert audit.project_id == "Legacy.Project_1"
    assert audit.to_mapping()["project_id"] == "Legacy.Project_1"
    assert decode_legacy_deployment_receipt_audit(encoded_receipt) == receipt
    assert receipt.plan_sha256 == audit.digest


@pytest.mark.parametrize("project_id", ("A", "A" * 128))
def test_schema1审计接受冻结project_id边界(project_id: str) -> None:
    payload = _legacy_plan_payload()
    payload["project_id"] = project_id

    audit = decode_legacy_deployment_plan_audit(json.dumps(payload).encode("utf-8"))

    assert audit.project_id == project_id


@pytest.mark.parametrize(
    "project_id",
    (
        "",
        " Legacy.Project_1",
        "Legacy.Project_1 ",
        "-legacy",
        ".legacy",
        "_legacy",
        "legacy/id",
        "legacy\\id",
        "A" * 129,
        "项目",
        1,
    ),
)
def test_schema1审计拒绝冻结规则外project_id且不归一化(project_id: object) -> None:
    payload = _legacy_plan_payload()
    payload["project_id"] = project_id

    with pytest.raises(RuntimeDeploymentError, match="项目标识"):
        decode_legacy_deployment_plan_audit(json.dumps(payload).encode("utf-8"))
