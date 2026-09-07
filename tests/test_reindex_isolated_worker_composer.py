"""隔离 worker 生产组合根的选择与失败关闭测试。"""
from __future__ import annotations

import pytest

from codev_platform.reindex.attempt_artifacts import AttemptArtifactPaths
from codev_platform.reindex.queue_ports import Job, JobMeta, QueueSnapshot
from codev_platform.reindex.runtime_owner import (
    QueueBackendBinding,
    QueueOwnerIdentity,
)


class _Backend:
    def assert_ready(self, *args, **kwargs):
        del args, kwargs

    def prepare(self, *args, **kwargs):
        del args, kwargs

    def activate(self, *args, **kwargs):
        del args, kwargs

    def poll(self, *args, **kwargs):
        del args, kwargs

    def terminate(self, *args, **kwargs):
        del args, kwargs

    def recover(self, *args, **kwargs):
        del args, kwargs

    def recover_handle(self, *args, **kwargs):
        del args, kwargs

    def confirm_dead(self, *args, **kwargs):
        del args, kwargs

    def confirm_reference_dead(self, *args, **kwargs):
        del args, kwargs


def _startup_context():
    from codev_platform.reindex.isolated_worker_startup import QueueStartupContext

    return QueueStartupContext(
        QueueOwnerIdentity("a" * 32, QueueBackendBinding("file", "b" * 64), 1.0),
        object(),
        object(),
    )


def test_build_isolated_worker_opens_strict_queue_and_audits_before_backend(monkeypatch) -> None:
    import codev_platform.reindex.isolated_worker_composer as composer

    events: list[str] = []
    queue = object()
    context = _startup_context()
    backend = _Backend()
    identity = object()
    expected = object()
    monkeypatch.setattr(
        composer,
        "_open_strict_queue",
        lambda: events.append("queue") or queue,
        raising=False,
    )
    monkeypatch.setattr(
        composer,
        "load_queue_startup_context",
        lambda **kwargs: events.append("startup") or context,
        raising=False,
    )
    monkeypatch.setattr(
        composer,
        "select_production_backend",
        lambda: events.append("backend") or backend,
        raising=False,
    )
    monkeypatch.setattr(
        composer,
        "_load_runtime_identity",
        lambda: events.append("identity") or identity,
        raising=False,
    )
    monkeypatch.setattr(
        composer,
        "_assemble_isolated_runtime",
        lambda **kwargs: events.append("assemble") or expected,
        raising=False,
    )

    assert composer.build_isolated_worker({}, "instance-token") is expected
    assert events == ["queue", "startup", "backend", "identity", "assemble"]


def test_build_isolated_worker_refuses_explicit_legacy_mode(monkeypatch) -> None:
    import codev_platform.reindex.isolated_worker_composer as composer

    monkeypatch.setattr(
        composer,
        "_open_strict_queue",
        lambda: pytest.fail("legacy 不得打开 isolated queue"),
        raising=False,
    )

    with pytest.raises(composer.ExecutionModeError):
        composer.build_isolated_worker(
            {"reindex": {"execution_mode": "legacy"}},
            "instance-token",
        )


def test_execution_mode_defaults_to_isolated_and_legacy_must_be_explicit() -> None:
    from codev_platform.reindex.isolated_worker_composer import execution_mode

    assert execution_mode({}) == "isolated"
    assert execution_mode({"reindex": {"execution_mode": "legacy"}}) == "legacy"


@pytest.mark.parametrize("value", ["", "auto", "ISOLATED", 1, None])
def test_execution_mode_rejects_unknown_or_non_string_value(value: object) -> None:
    from codev_platform.reindex.isolated_worker_composer import ExecutionModeError, execution_mode

    with pytest.raises(ExecutionModeError):
        execution_mode({"reindex": {"execution_mode": value}})


def test_linux_selects_cgroup_backend_without_posix_fallback() -> None:
    from codev_platform.reindex.isolated_worker_composer import select_production_backend

    expected = _Backend()

    assert select_production_backend(
        platform_name="linux",
        cgroup_factory=lambda: expected,
        windows_factory=lambda: pytest.fail("Linux 不得构造 Windows 后端"),
    ) is expected


def test_linux_cgroup_construction_failure_is_fatal_without_fallback() -> None:
    from codev_platform.reindex.isolated_worker_composer import (
        ProductionBackendUnavailable,
        select_production_backend,
    )

    def _broken_cgroup() -> object:
        raise OSError("delegation unavailable")

    with pytest.raises(ProductionBackendUnavailable) as raised:
        select_production_backend(
            platform_name="linux",
            cgroup_factory=_broken_cgroup,
            windows_factory=lambda: pytest.fail("Linux 不得构造 Windows 后端"),
        )
    assert "cgroup" in str(raised.value)


def test_windows_selects_job_backend_and_factory_failure_is_fatal() -> None:
    from codev_platform.reindex.isolated_worker_composer import (
        ProductionBackendUnavailable,
        select_production_backend,
    )

    expected = _Backend()
    assert select_production_backend(
        platform_name="win32",
        cgroup_factory=lambda: pytest.fail("Windows 不得构造 cgroup 后端"),
        windows_factory=lambda: expected,
    ) is expected

    with pytest.raises(ProductionBackendUnavailable):
        select_production_backend(
            platform_name="win32",
            cgroup_factory=lambda: pytest.fail("Windows 不得构造 cgroup 后端"),
            windows_factory=lambda: (_ for _ in ()).throw(OSError("native unavailable")),
        )


def test_backend_factory_rejects_incomplete_object_before_worker_is_built() -> None:
    from codev_platform.reindex.isolated_worker_composer import (
        ProductionBackendUnavailable,
        select_production_backend,
    )

    with pytest.raises(ProductionBackendUnavailable):
        select_production_backend(
            platform_name="linux",
            cgroup_factory=object,
            windows_factory=lambda: pytest.fail("Linux 不得构造 Windows 后端"),
        )


def test_unsupported_platform_fails_before_constructing_any_backend() -> None:
    from codev_platform.reindex.isolated_worker_composer import (
        ProductionBackendUnavailable,
        select_production_backend,
    )

    with pytest.raises(ProductionBackendUnavailable):
        select_production_backend(
            platform_name="darwin",
            cgroup_factory=lambda: pytest.fail("不支持平台不得构造 cgroup"),
            windows_factory=lambda: pytest.fail("不支持平台不得构造 Windows Job"),
        )


def test_observer_argv_uses_proven_interpreter_for_outer_and_inner_executor(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_composer import build_observer_argv

    paths = AttemptArtifactPaths(
        root=tmp_path / "attempt",
        spec=tmp_path / "attempt" / "spec.json",
        result=tmp_path / "attempt" / "result.json",
        receipt=tmp_path / "attempt" / "completion.json",
        bootstrap_log=tmp_path / "attempt" / "bootstrap.log",
    )
    interpreter = tmp_path / "venv" / "bin" / "python"

    argv = build_observer_argv(interpreter, paths)

    separator = argv.index("--")
    assert argv[:separator] == (
        str(interpreter),
        "-I",
        "-m",
        "codev_platform.reindex.executor_observer",
        "--spec",
        str(paths.spec),
        "--result",
        str(paths.result),
        "--receipt",
        str(paths.receipt),
    )
    assert argv[separator + 1 :] == (
        str(interpreter),
        "-I",
        "-m",
        "codev_platform.reindex.executor",
        "--spec",
        str(paths.spec),
        "--result",
        str(paths.result),
    )


def test_observer_argv_rejects_non_absolute_interpreter(tmp_path) -> None:
    from codev_platform.reindex.isolated_worker_composer import build_observer_argv

    relative = AttemptArtifactPaths(
        root=tmp_path / "attempt",
        spec=tmp_path / "attempt" / "spec.json",
        result=tmp_path / "attempt" / "result.json",
        receipt=tmp_path / "attempt" / "completion.json",
        bootstrap_log=tmp_path / "attempt" / "bootstrap.log",
    )

    with pytest.raises(ValueError):
        build_observer_argv("python", relative)


def test_legacy_audit使用稳定owner区分现代active与foreign_active() -> None:
    from codev_platform.reindex.isolated_worker_composer import _require_no_legacy_queue_entries

    class _Queue:
        def snapshot(self) -> QueueSnapshot:
            return QueueSnapshot(expired_active=[Job(
                "demo",
                "chroma",
                1.0,
                token="claim",
                owner_token="stable-owner",
                lease_expires_at=1.0,
                meta=JobMeta(target_commit="a" * 40),
            )])

    _require_no_legacy_queue_entries(_Queue(), "stable-owner")
