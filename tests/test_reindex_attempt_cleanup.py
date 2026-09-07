"""attempt 父侧清理端口与固定路由测试。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from codev_platform.reindex.attempt_cleanup import AttemptCleanupRouter, ConfiguredAttemptCleanup
from codev_platform.reindex.attempt_process import Deadline
from codev_platform.reindex.attempts import (
    AttemptSpec,
    CanonicalJsonObject,
    CleanupReport,
    ConfirmedProcessDeath,
)

_OID = "a" * 40
_RUNTIME = "b" * 64


def _spec() -> AttemptSpec:
    return AttemptSpec(
        schema_version=1,
        attempt_id="attempt-1",
        fence="fence-1",
        project_id="demo",
        kind="chroma",
        input_kind="configured",
        input_payload=CanonicalJsonObject.from_value({"project_id": "demo"}),
        target_commit=_OID,
        timeout_sec=30.0,
        runtime_revision=_RUNTIME,
    )


def _death() -> ConfirmedProcessDeath:
    return ConfirmedProcessDeath(
        process_identity="pid:123:start:9",
        containment_kind="job_object",
        confirmed_at=10.0,
        evidence="active_processes=0",
    )


def _deadline() -> Deadline:
    return Deadline.start(1.0)


def test_cleanup_router_routes_by_input_kind_after_confirmed_death() -> None:
    calls: list[tuple[object, ConfirmedProcessDeath]] = []

    class _Cleanup:
        def release(
            self,
            spec: AttemptSpec,
            death: ConfirmedProcessDeath,
            deadline: Deadline,
        ) -> CleanupReport:
            assert isinstance(deadline, Deadline)
            calls.append((spec, death))
            return CleanupReport(True, spec.attempt_id, "已清理")

    spec = _spec()
    death = _death()
    report = AttemptCleanupRouter({"configured": _Cleanup()}).release(
        spec,
        death,
        _deadline(),
    )

    assert calls == [(spec, death)]
    assert report == CleanupReport(True, "attempt-1", "已清理")


def test_cleanup_router_rejects_substitute_death_before_adapter() -> None:
    calls: list[object] = []

    class _PermissiveCleanup:
        def release(
            self,
            spec: AttemptSpec,
            death: object,
            deadline: Deadline,
        ) -> CleanupReport:
            del deadline
            calls.append(death)
            return CleanupReport(True, spec.attempt_id, "不应执行")

    router = AttemptCleanupRouter({"configured": _PermissiveCleanup()})

    with pytest.raises(TypeError, match="confirmed process death|死亡证明"):
        router.release(_spec(), object(), _deadline())
    assert calls == []


def test_cleanup_router_unknown_input_kind_fails_closed() -> None:
    router = AttemptCleanupRouter({"configured": ConfiguredAttemptCleanup()})

    with pytest.raises(ValueError, match="unknown|未知|没有"):
        router.release(
            SimpleNamespace(input_kind="unknown", attempt_id="attempt-1"),
            _death(),
            _deadline(),
        )
    with pytest.raises(ValueError):
        AttemptCleanupRouter({})


def test_cleanup_router_copies_and_freezes_mapping() -> None:
    first = ConfiguredAttemptCleanup()
    source = {"configured": first}
    router = AttemptCleanupRouter(source)
    source["configured"] = object()

    assert router.release(_spec(), _death(), _deadline()).released is True
    with pytest.raises(TypeError):
        router._adapters["configured"] = object()


def test_configured_cleanup_is_noop_but_requires_confirmed_death() -> None:
    cleanup = ConfiguredAttemptCleanup()

    report = cleanup.release(_spec(), _death(), _deadline())
    assert report.released is True
    assert report.attempt_id == "attempt-1"
    assert "不持有父侧资源" in report.note

    with pytest.raises(TypeError, match="confirmed process death|死亡证明"):
        cleanup.release(_spec(), object(), _deadline())


def test_清理路由将同一截止时间传给从未启动适配器() -> None:
    calls: list[tuple[AttemptSpec, Deadline]] = []

    class _清理适配器:
        def release_unstarted(
            self,
            spec: AttemptSpec,
            deadline: Deadline,
        ) -> CleanupReport:
            calls.append((spec, deadline))
            return CleanupReport(True, spec.attempt_id, "未启动资源已释放")

    spec = _spec()
    deadline = _deadline()

    report = AttemptCleanupRouter({"configured": _清理适配器()}).release_unstarted(
        spec,
        deadline,
    )

    assert calls == [(spec, deadline)]
    assert report == CleanupReport(True, "attempt-1", "未启动资源已释放")


@pytest.mark.parametrize(
    "method_name",
    ["release", "release_unstarted"],
    ids=["死亡证明释放", "从未启动释放"],
)
def test_清理路由在预算过期前不调用任何适配器(method_name: str) -> None:
    calls: list[str] = []

    class _清理适配器:
        def release(self, *_args: object) -> CleanupReport:
            calls.append("release")
            return CleanupReport(True, "attempt-1", "不应执行")

        def release_unstarted(self, *_args: object) -> CleanupReport:
            calls.append("release_unstarted")
            return CleanupReport(True, "attempt-1", "不应执行")

    router = AttemptCleanupRouter({"configured": _清理适配器()})
    deadline = Deadline.start(0.0)

    with pytest.raises(TimeoutError, match="清理预算"):
        if method_name == "release":
            router.release(_spec(), _death(), deadline)
        else:
            router.release_unstarted(_spec(), deadline)

    assert calls == []


@pytest.mark.parametrize(
    "method_name",
    ["release", "release_unstarted"],
    ids=["死亡证明释放", "从未启动释放"],
)
def test_清理路由拒绝适配器在截止时间后返回成功(
    method_name: str,
    monkeypatch,
) -> None:
    expired = iter((False, True))
    monkeypatch.setattr(Deadline, "expired", lambda _self: next(expired))

    class _清理适配器:
        @staticmethod
        def release(
            spec: AttemptSpec,
            _death: ConfirmedProcessDeath,
            _deadline: Deadline,
        ) -> CleanupReport:
            return CleanupReport(True, spec.attempt_id, "过期成功")

        @staticmethod
        def release_unstarted(
            spec: AttemptSpec,
            _deadline: Deadline,
        ) -> CleanupReport:
            return CleanupReport(True, spec.attempt_id, "过期成功")

    router = AttemptCleanupRouter({"configured": _清理适配器()})

    with pytest.raises(TimeoutError, match="清理预算"):
        if method_name == "release":
            router.release(_spec(), _death(), _deadline())
        else:
            router.release_unstarted(_spec(), _deadline())


def test_配置输入从未启动释放可重复且结果一致() -> None:
    cleanup = ConfiguredAttemptCleanup()
    spec = _spec()

    first = cleanup.release_unstarted(spec, _deadline())
    second = cleanup.release_unstarted(spec, _deadline())

    assert first == second == CleanupReport(True, "attempt-1", "configured 输入不持有父侧资源")


def test_配置输入死亡证明释放可重复且结果一致() -> None:
    cleanup = ConfiguredAttemptCleanup()
    spec = _spec()
    death = _death()

    first = cleanup.release(spec, death, _deadline())
    second = cleanup.release(spec, death, _deadline())

    assert first == second == CleanupReport(True, "attempt-1", "configured 输入不持有父侧资源")
