"""退役回执存储的拒绝写入与显式只读审计测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codev_platform.runtime_managed_file import (
    RootOwnedRegularFileSnapshot,
)
from codev_platform.runtime_deployment_contract import (
    DeploymentPlan,
    RuntimeDeploymentError,
)
from codev_platform.runtime_deployment_receipt import RuntimeDeploymentReceiptStore
from tests.runtime_legacy_deployment_fixtures import (
    legacy_plan_audit,
    legacy_receipt_audit,
)


def _plan() -> DeploymentPlan:
    return DeploymentPlan(
        schema_version=2,
        target_revision="a" * 40,
        source_repo="/home/helloworld/work/codev-platform",
        runtime_root="/var/lib/codev-platform/runtime",
        requirements_lock="/srv/codev-artifacts/wsl-runtime.lock",
        approved_requirements="/srv/codev-artifacts/wsl-runtime.freeze",
        wheelhouse="/srv/codev-artifacts/wheelhouse",
        candidate_root="/var/lib/codev-platform/runtime/candidates",
        config_source="/home/helloworld/.codev-platform/config.json",
        environment_source="/etc/codev-platform/platform.source.env",
        service_user="helloworld",
        project_id="codev-platform",
        require_cuda=True,
    )


def _legacy_plan():
    return legacy_plan_audit()


def _audit_payload() -> bytes:
    legacy_plan = _legacy_plan()
    receipt = legacy_receipt_audit(legacy_plan)
    return json.dumps(
        receipt.to_mapping(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_生产锁读写对v2与v1审计均在任何io前拒绝(monkeypatch) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    events: list[str] = []
    monkeypatch.setattr(
        receipt_module,
        "deployment_lock",
        lambda _root: events.append("lock"),
    )
    monkeypatch.setattr(
        receipt_module,
        "read_optional_root_owned_regular_file_snapshot",
        lambda *_args, **_kwargs: events.append("read"),
    )
    monkeypatch.setattr(
        receipt_module,
        "write_root_owned_regular_file_atomic",
        lambda *_args, **_kwargs: events.append("write"),
        raising=False,
    )
    receipt = legacy_receipt_audit(_legacy_plan())
    store = RuntimeDeploymentReceiptStore(Path(_plan().runtime_root))

    for plan in (_plan(), _legacy_plan()):
        with pytest.raises(RuntimeDeploymentError):
            store.lock(plan)  # type: ignore[arg-type]
        with pytest.raises(RuntimeDeploymentError):
            store.load(plan)  # type: ignore[arg-type]
        with pytest.raises(RuntimeDeploymentError):
            store.save(plan, receipt)  # type: ignore[arg-type]
    assert events == []


def test_只读审计加载可信历史回执且缺失时返回none(monkeypatch) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    plan = _legacy_plan()
    reads: list[Path] = []
    snapshots = iter(
        (
            None,
            RootOwnedRegularFileSnapshot(_audit_payload(), 0o600, 0, 0),
        )
    )

    def read(path: Path, *, max_bytes: int):
        assert max_bytes == 32 * 1024
        reads.append(path)
        return next(snapshots)

    monkeypatch.setattr(
        receipt_module,
        "read_optional_root_owned_regular_file_snapshot",
        read,
    )
    store = RuntimeDeploymentReceiptStore(Path(plan.runtime_root))

    assert store.load_legacy_audit(plan) is None
    assert store.load_legacy_audit(plan).plan_sha256 == plan.digest  # type: ignore[union-attr]
    assert reads == [
        Path(plan.runtime_root) / "deployments" / f"{plan.target_revision}.json",
    ] * 2


@pytest.mark.parametrize(
    "snapshot",
    (
        RootOwnedRegularFileSnapshot(_audit_payload(), 0o640, 0, 0),
        RootOwnedRegularFileSnapshot(_audit_payload(), 0o600, 0, 1000),
    ),
)
def test_只读审计拒绝不受信任的权限元数据(monkeypatch, snapshot) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    monkeypatch.setattr(
        receipt_module,
        "read_optional_root_owned_regular_file_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )

    with pytest.raises(RuntimeDeploymentError, match="元数据不受信任"):
        RuntimeDeploymentReceiptStore(Path(_legacy_plan().runtime_root)).load_legacy_audit(
            _legacy_plan()
        )


def test_只读审计拒绝畸形json且不泄露解析细节(monkeypatch) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    payload = b'{"schema_version":1,"schema_version":1}'
    monkeypatch.setattr(
        receipt_module,
        "read_optional_root_owned_regular_file_snapshot",
        lambda *_args, **_kwargs: RootOwnedRegularFileSnapshot(payload, 0o600, 0, 0),
    )

    with pytest.raises(RuntimeDeploymentError, match="旧部署回执审计载荷无效"):
        RuntimeDeploymentReceiptStore(Path(_legacy_plan().runtime_root)).load_legacy_audit(
            _legacy_plan()
        )


def test_只读审计运行时根漂移时读取器零调用(monkeypatch) -> None:
    import codev_platform.runtime_deployment_receipt as receipt_module

    events: list[str] = []
    monkeypatch.setattr(
        receipt_module,
        "read_optional_root_owned_regular_file_snapshot",
        lambda *_args, **_kwargs: events.append("read"),
    )

    with pytest.raises(RuntimeDeploymentError, match="运行时根不匹配"):
        RuntimeDeploymentReceiptStore(Path("/different/root")).load_legacy_audit(
            _legacy_plan()
        )
    assert events == []
