"""生产部署计划加载与 CLI 无秘密输出测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codev_platform.ops.runtime_deploy import (
    _emit_progress,
    cmd_runtime_deploy,
    load_deployment_plan,
)
from codev_platform.runtime_deployment_contract import (
    DEPLOYMENT_PHASES,
    DeploymentPlan,
    RuntimeDeploymentError,
    decode_legacy_deployment_plan_audit,
)
from tests.runtime_legacy_deployment_fixtures import (
    legacy_plan_audit,
    legacy_receipt_audit,
)


def _mapping() -> dict[str, object]:
    return {
        "schema_version": 2,
        "target_revision": "a" * 40,
        "source_repo": "/home/helloworld/work/codev-platform",
        "runtime_root": "/var/lib/codev-platform/runtime",
        "requirements_lock": "/srv/codev-artifacts/wsl-runtime.lock",
        "approved_requirements": "/srv/codev-artifacts/wsl-runtime.freeze",
        "wheelhouse": "/srv/codev-artifacts/wheelhouse",
        "candidate_root": "/var/tmp/codev-platform-candidates/helloworld",
        "config_source": "/home/helloworld/.codev-platform/config.json",
        "environment_source": "/etc/codev-platform/platform.source.env",
        "service_user": "helloworld",
        "project_id": "codev-platform",
        "require_cuda": True,
        "legacy_takeover_policy_sha256": None,
    }


def _legacy_mapping() -> dict[str, object]:
    return legacy_plan_audit().to_mapping()


def _write_plan(path: Path, mapping: dict[str, object] | None = None) -> None:
    path.write_text(
        json.dumps(_mapping() if mapping is None else mapping, ensure_ascii=False),
        encoding="utf-8",
    )


def test_严格加载固定字段部署计划(tmp_path: Path) -> None:
    path = tmp_path / "deployment-plan.json"
    _write_plan(path)

    plan = load_deployment_plan(path)

    assert plan.to_mapping() == _mapping()


def test_v1只经审计解码且生产加载器拒绝(tmp_path: Path) -> None:
    encoded = json.dumps(_legacy_mapping()).encode("utf-8")
    assert decode_legacy_deployment_plan_audit(encoded).schema_version == 1
    path = tmp_path / "legacy-plan.json"
    path.write_bytes(encoded)

    with pytest.raises(RuntimeDeploymentError, match="部署计划"):
        load_deployment_plan(path)


def test_部署计划拒绝root作为服务账号() -> None:
    mapping = {**_mapping(), "service_user": "root"}

    with pytest.raises(RuntimeDeploymentError, match="非特权"):
        DeploymentPlan(**mapping)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "content",
    (
        '{"schema_version":2,"schema_version":2}',
        json.dumps({**_mapping(), "unknown": "value"}),
        "[]",
        "{not-json}",
    ),
)
def test_重复字段额外字段或畸形JSON全部拒绝(tmp_path: Path, content: str) -> None:
    path = tmp_path / "deployment-plan.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(RuntimeDeploymentError, match="部署计划"):
        load_deployment_plan(path)


def test_相对路径和超大计划在解码前拒绝(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "deployment-plan.json"
    path.write_bytes(b" " * (64 * 1024 + 1))

    with pytest.raises(RuntimeDeploymentError):
        load_deployment_plan(path)

    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeDeploymentError):
        load_deployment_plan(Path("deployment-plan.json"))


def test_cli进度适配器输出固定中文且不带计划内容(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _emit_progress(DEPLOYMENT_PHASES[0], "started")
    _emit_progress(DEPLOYMENT_PHASES[0], "completed")

    progress_lines = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert [item["message"] for item in progress_lines] == [
        "部署阶段 target_verified 开始",
        "部署阶段 target_verified 完成",
    ]


def test_cli拒绝把旧审计回执输出为schema2成功(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "deployment-plan.json"
    _write_plan(path)
    plan = DeploymentPlan(**_mapping())  # type: ignore[arg-type]
    receipt = legacy_receipt_audit(
        legacy_plan_audit(),
        completed=len(DEPLOYMENT_PHASES),
        status="complete",
        base_id="2" * 64,
        release_id="3" * 64,
        baseline_release_id="4" * 64,
        previous_release_id="4" * 64,
        last_evidence_sha256="1" * 64,
    )
    called: list[DeploymentPlan] = []

    def run(received: DeploymentPlan, *, progress):
        del progress
        called.append(received)
        return receipt

    monkeypatch.setattr("codev_platform.ops.runtime_deploy._deployment_runner", lambda: run)

    code = cmd_runtime_deploy(type("Args", (), {"plan": path})())

    captured = capsys.readouterr()
    assert called == [plan]
    assert code == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "runtime_deploy_failed",
        "kind": "deployment",
        "status": "error",
    }


def test_cli失败不泄露底层异常与计划路径(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "deployment-plan.json"
    _write_plan(path)

    called: list[DeploymentPlan] = []

    def run(received: DeploymentPlan, *, progress):
        del progress
        called.append(received)
        raise RuntimeDeploymentError("token=绝不能输出")

    monkeypatch.setattr("codev_platform.ops.runtime_deploy._deployment_runner", lambda: run)

    code = cmd_runtime_deploy(type("Args", (), {"plan": path})())

    captured = capsys.readouterr()
    assert called == [DeploymentPlan(**_mapping())]  # type: ignore[arg-type]
    assert code == 1
    assert json.loads(captured.err) == {
        "error": "runtime_deploy_failed",
        "kind": "deployment",
        "status": "error",
    }
    assert "token" not in captured.err
    assert str(path) not in captured.err
