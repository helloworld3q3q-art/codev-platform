"""日常薄发布切换、验收与补偿边界测试。"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import ActivationResult
from codev_platform.runtime_thin_promotion import (
    DAILY_RUNTIME_SERVICE_UNITS,
    ThinPromotionError,
    ThinPromotionPorts,
    promote_staged_release,
)


_TARGET = "a" * 64
_ROLLBACK = "b" * 64
_SYSTEMD_EVIDENCE = "c" * 64
_MCP_EVIDENCE = "d" * 64
_INGRESS_EVIDENCE = "e" * 64


def _ports(events: list[str], **overrides) -> ThinPromotionPorts:
    defaults = {
        "promotion_lock": nullcontext,
        "preflight": lambda: events.append("前置"),
        "publish_target_service_access": lambda target: events.append(
            f"发布目标访问:{target}"
        ),
        "activate": lambda _root, target, rollback: (
            events.append(f"激活:{target}:{rollback}")
            or ActivationResult(target, rollback)
        ),
        "rollback": lambda _root, target, rollback: (
            events.append(f"回滚:{target}:{rollback}")
            or ActivationResult(rollback, rollback)
        ),
        "install_current_units": lambda: events.append("刷新unit"),
        "restart_runtime_services": lambda: events.append("重启服务"),
        "verify_runtime_services": lambda: events.append("验证服务") or _SYSTEMD_EVIDENCE,
        "verify_mcp_services": lambda: events.append("验证MCP") or _MCP_EVIDENCE,
        "verify_ingress": lambda: events.append("验证入口") or _INGRESS_EVIDENCE,
    }
    defaults.update(overrides)
    return ThinPromotionPorts(**defaults)


def test_成功前移后刷新unit重启并完成全部验收() -> None:
    events: list[str] = []

    result = promote_staged_release(
        Path("/runtime"),
        target_release_id=_TARGET,
        rollback_release_id=_ROLLBACK,
        ports=_ports(events),
    )

    assert result.active_release == _TARGET
    assert result.rollback_release == _ROLLBACK
    assert result.systemd_evidence_sha256 == _SYSTEMD_EVIDENCE
    assert result.mcp_evidence_sha256 == _MCP_EVIDENCE
    assert result.ingress_evidence_sha256 == _INGRESS_EVIDENCE
    assert events == [
        "前置",
        f"发布目标访问:{_TARGET}",
        f"激活:{_TARGET}:{_ROLLBACK}",
        "刷新unit",
        "重启服务",
        "验证服务",
        "验证MCP",
        "验证入口",
    ]


def test_激活后MCP验收失败会回滚并复验旧版本() -> None:
    events: list[str] = []
    calls = 0

    def fail_mcp() -> str:
        nonlocal calls
        calls += 1
        events.append("验证MCP")
        if calls == 1:
            raise RuntimeError("不应回显的内部错误")
        return _MCP_EVIDENCE

    with pytest.raises(ThinPromotionError, match="已回滚") as caught:
        promote_staged_release(
            Path("/runtime"),
            target_release_id=_TARGET,
            rollback_release_id=_ROLLBACK,
            ports=_ports(events, verify_mcp_services=fail_mcp),
        )

    assert str(caught.value) == "日常薄发布失败，已回滚到原版本"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert events == [
        "前置",
        f"发布目标访问:{_TARGET}",
        f"激活:{_TARGET}:{_ROLLBACK}",
        "刷新unit",
        "重启服务",
        "验证服务",
        "验证MCP",
        f"回滚:{_TARGET}:{_ROLLBACK}",
        "刷新unit",
        "重启服务",
        "验证服务",
        "验证MCP",
        "验证入口",
    ]


def test_回滚后的服务状态无法证明时失败关闭() -> None:
    events: list[str] = []

    def fail_mcp() -> str:
        events.append("验证MCP")
        raise RuntimeError("内部错误")

    def fail_rollback(*_args) -> ActivationResult:
        events.append("回滚失败")
        raise RuntimeError("内部错误")

    with pytest.raises(ThinPromotionError, match="回滚状态无法证明") as caught:
        promote_staged_release(
            Path("/runtime"),
            target_release_id=_TARGET,
            rollback_release_id=_ROLLBACK,
            ports=_ports(
                events,
                verify_mcp_services=fail_mcp,
                rollback=fail_rollback,
            ),
        )

    assert str(caught.value) == "日常薄发布失败且回滚状态无法证明"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_前置校验失败不会先切换current() -> None:
    events: list[str] = []

    def fail_preflight() -> None:
        events.append("前置失败")
        raise RuntimeError("维护态")

    with pytest.raises(ThinPromotionError, match="前置校验失败"):
        promote_staged_release(
            Path("/runtime"),
            target_release_id=_TARGET,
            rollback_release_id=_ROLLBACK,
            ports=_ports(events, preflight=fail_preflight),
        )

    assert events == ["前置失败"]


def test_目标服务访问发布失败时不切换current() -> None:
    events: list[str] = []

    def fail_target_access(target: str) -> None:
        assert target == _TARGET
        events.append("目标访问失败")
        raise RuntimeError("不应回显的服务访问细节")

    with pytest.raises(ThinPromotionError, match="目标服务访问发布失败") as caught:
        promote_staged_release(
            Path("/runtime"),
            target_release_id=_TARGET,
            rollback_release_id=_ROLLBACK,
            ports=_ports(events, publish_target_service_access=fail_target_access),
        )

    assert str(caught.value) == "日常薄发布目标服务访问发布失败"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert events == ["前置", "目标访问失败"]


def test_日常重启集合固定覆盖全部常驻服务与Webhook() -> None:
    assert DAILY_RUNTIME_SERVICE_UNITS == (
        "codev-mcp-platform-docs.service",
        "codev-mcp-codegraph.service",
        "codev-mcp-agent-memory.service",
        "codev-mcp-graph.service",
        "codev-reindex.service",
        "codev-agent.service",
        "codev-web.service",
        "codev-webhook.service",
    )


def test_独占锁覆盖整个切换和验收窗口() -> None:
    events: list[str] = []

    @contextmanager
    def lock():
        events.append("取得锁")
        try:
            yield
        finally:
            events.append("释放锁")

    promote_staged_release(
        Path("/runtime"),
        target_release_id=_TARGET,
        rollback_release_id=_ROLLBACK,
        ports=_ports(events, promotion_lock=lock),
    )

    assert events == [
        "取得锁",
        "前置",
        f"发布目标访问:{_TARGET}",
        f"激活:{_TARGET}:{_ROLLBACK}",
        "刷新unit",
        "重启服务",
        "验证服务",
        "验证MCP",
        "验证入口",
        "释放锁",
    ]
