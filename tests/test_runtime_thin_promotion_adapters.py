"""日常薄发布生产适配器的输入边界测试。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from codev_platform.runtime_thin_promotion import ThinPromotionError
from codev_platform.runtime_thin_promotion_adapters import (
    _daily_promotion_lock,
    _manifest_path,
    _publish_target_service_access,
    _preflight_normal_runtime,
    _verify_mcp_services,
)
from codev_platform.runtime_service_process import ServiceAccount
from codev_platform.runtime_staged_release_access import RuntimeStagedReleaseAccessProof


def test_install_only安装包只接受既有固定命令形状() -> None:
    package = {
        "transaction_argv": (
            "/var/lib/codev-platform/runtime/releases/" + "a" * 64 + "/venv/bin/python",
            "-I",
            "-m",
            "codev_platform.mcp_systemd_install_transaction",
            "--manifest",
            "/root/codev-systemd/install-manifest.json",
            "--install-only",
        )
    }

    assert _manifest_path(package) == Path("/root/codev-systemd/install-manifest.json")


@pytest.mark.parametrize(
    "package",
    (
        {},
        {"transaction_argv": ()},
        {"transaction_argv": ("--manifest", "/root/manifest", "--maintenance-stage")},
        {"transaction_argv": ("--manifest", "relative.json", "--install-only")},
        {
            "transaction_argv": (
                "--manifest",
                "/root/one.json",
                "--manifest",
                "/root/two.json",
                "--install-only",
            )
        },
    ),
)
def test_install_only安装包拒绝歧义或非受控路径(package: object) -> None:
    with pytest.raises(ThinPromotionError, match="安装包无效"):
        _manifest_path(package)


def test_日常薄发布锁覆盖维护会话与运行时部署锁(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """维护转换不能在 current 切换与服务验收之间插入。"""
    from codev_platform.reindex import maintenance_gate
    from codev_platform import runtime_storage

    events: list[str] = []

    @contextmanager
    def 维护会话():
        events.append("进入维护会话")
        try:
            yield
        finally:
            events.append("退出维护会话")

    @contextmanager
    def 部署锁(root: Path):
        assert root == Path("/runtime")
        events.append("进入部署锁")
        try:
            yield
        finally:
            events.append("退出部署锁")

    monkeypatch.setattr(maintenance_gate, "maintenance_systemd_transition_session", 维护会话)
    monkeypatch.setattr(runtime_storage, "deployment_lock", 部署锁)

    with _daily_promotion_lock(Path("/runtime")):
        events.append("发布体")

    assert events == ["进入维护会话", "进入部署锁", "发布体", "退出部署锁", "退出维护会话"]


def test_日常薄发布前置校验只接受常态(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import mcp_systemd_install_systemd
    from codev_platform.reindex import maintenance_gate

    events: list[str] = []
    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: False)
    monkeypatch.setattr(
        mcp_systemd_install_systemd,
        "default_runtime_mask_proof",
        lambda: events.append("验证未屏蔽"),
    )

    _preflight_normal_runtime()

    assert events == ["验证未屏蔽"]


def test_日常薄发布前置校验拒绝维护态(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import mcp_systemd_install_systemd
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)
    monkeypatch.setattr(
        mcp_systemd_install_systemd,
        "default_runtime_mask_proof",
        lambda: (_ for _ in ()).throw(AssertionError("维护态不得继续验证运行态")),
    )

    with pytest.raises(ThinPromotionError, match="只允许常态"):
        _preflight_normal_runtime()


def test_日常薄发布前置校验拒绝CodeGraph维护屏蔽(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import mcp_systemd_install_systemd
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: False)
    monkeypatch.setattr(
        mcp_systemd_install_systemd,
        "default_runtime_mask_proof",
        lambda: (_ for _ in ()).throw(RuntimeError("CodeGraph 正处于维护窗口")),
    )

    with pytest.raises(ThinPromotionError, match="只允许常态"):
        _preflight_normal_runtime()


def test_日常薄发布目标访问必须由同一服务账号完成探针(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import runtime_staged_release_access

    account = ServiceAccount(
        name="worker",
        uid=23456,
        gid=23457,
        home=Path("/srv/worker"),
    )
    release_id = "a" * 64
    events: list[str] = []

    def publish(root: Path, *, account: ServiceAccount, release_id: str, dry_run: bool):
        assert root == Path("/runtime")
        assert account.uid == 23456
        assert release_id == "a" * 64
        assert dry_run is False
        events.append("publish")
        return RuntimeStagedReleaseAccessProof(
            access_profile="root-service-group-read-v1",
            service_uid=23456,
            service_gid=23457,
            base_id="b" * 64,
            release_id=release_id,
            dry_run=False,
            target_user_evidence_sha256="c" * 64,
        )

    monkeypatch.setattr(runtime_staged_release_access, "publish_staged_release_service_access", publish)

    _publish_target_service_access(Path("/runtime"), account, release_id)

    assert events == ["publish"]


def _mcp_rows(status: str, *, names: tuple[str, ...] | None = None) -> list[dict[str, str]]:
    selected = (
        "agent-memory",
        "codegraph",
        "graph",
        "platform-docs",
    ) if names is None else names
    return [{"name": name, "status": status} for name in selected]


def test_MCP验收要求同轮就绪并使用固定等待预算(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import mcp_source_client

    received: dict[str, object] = {}

    def wait(
        config: dict[str, object], target: str, **kwargs: object,
    ) -> list[dict[str, str]]:
        received["config"] = config
        received["target"] = target
        received.update(kwargs)
        return _mcp_rows("ok")

    monkeypatch.setattr(mcp_source_client, "wait_until_source_serving", wait)

    evidence = _verify_mcp_services({"projects": {}})

    assert len(evidence) == 64
    assert received == {
        "config": {"projects": {}},
        "target": "local",
        "timeout": 90.0,
        "interval": 2.0,
    }


@pytest.mark.parametrize(
    "rows",
    (
        _mcp_rows("ok", names=("agent-memory",)),
        _mcp_rows("timeout"),
    ),
)
def test_MCP验收拒绝集合漂移和非OK结果(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, str]],
) -> None:
    from codev_platform import mcp_source_client

    monkeypatch.setattr(
        mcp_source_client, "wait_until_source_serving", lambda *_args, **_kwargs: rows,
    )

    with pytest.raises(ThinPromotionError, match="MCP 健康验收失败"):
        _verify_mcp_services({"projects": {}})


def test_MCP验收不采用允许TCP回退的旧探针(monkeypatch: pytest.MonkeyPatch) -> None:
    from codev_platform import mcp_serve, mcp_source_client

    monkeypatch.setattr(
        mcp_serve, "wait_until_serving", lambda *_args, **_kwargs: _mcp_rows("ok"),
    )
    monkeypatch.setattr(
        mcp_source_client,
        "wait_until_source_serving",
        lambda *_args, **_kwargs: _mcp_rows("timeout"),
    )

    with pytest.raises(ThinPromotionError, match="MCP 健康验收失败"):
        _verify_mcp_services({"projects": {}})
