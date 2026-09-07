"""隔离 worker 的生产模式与 containment 后端选择。"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .attempt_artifacts import AttemptArtifactPaths
from .execution_mode import ExecutionModeError, execution_mode
from .isolated_worker_startup import QueueStartupContext, load_queue_startup_context

_PROCESS_BACKEND_METHODS = (
    "assert_ready",
    "prepare",
    "activate",
    "poll",
    "terminate",
    "recover",
    "recover_handle",
    "confirm_dead",
    "confirm_reference_dead",
)


class ProductionBackendUnavailable(RuntimeError):
    """生产 containment 后端无法安全构造，禁止领取 queue。"""


class LegacyQueueMigrationRequired(RuntimeError):
    """检测到旧格式队列记录，必须先运行显式迁移命令。"""


@dataclass(frozen=True, slots=True)
class IsolatedWorkerRuntime:
    """生产组合根的最小产物；外层 CLI 只驱动 loop。"""

    loop: object = field(repr=False)
    queue: object = field(repr=False)
    queue_owner_token: str = field(repr=False)


def _default_cgroup_backend() -> object:
    from .cgroup_process import CgroupAttemptProcessBackend

    return CgroupAttemptProcessBackend()


def _default_windows_backend() -> object:
    from .windows_job import WindowsJobAttemptProcessBackend
    from .windows_job_native import CtypesWindowsJobNative

    return WindowsJobAttemptProcessBackend(native=CtypesWindowsJobNative())


def select_production_backend(
    *,
    platform_name: str | None = None,
    cgroup_factory: Callable[[], object] = _default_cgroup_backend,
    windows_factory: Callable[[], object] = _default_windows_backend,
) -> object:
    """只选择可证明 containment 的 Windows Job 或 delegated cgroup。"""
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str:
        raise ValueError("platform_name 必须是字符串或 None")
    if platform.startswith("linux"):
        return _construct_backend(cgroup_factory, "cgroup")
    if platform.startswith("win"):
        return _construct_backend(windows_factory, "Windows Job")
    raise ProductionBackendUnavailable("当前平台不支持隔离 reindex containment")


def _construct_backend(factory: Callable[[], object], label: str) -> object:
    if not callable(factory):
        raise ValueError("生产后端工厂必须可调用")
    try:
        backend = factory()
    except MemoryError:
        raise
    except Exception as error:
        raise ProductionBackendUnavailable(f"生产 {label} 后端不可用") from error
    if backend is None or any(
        not callable(getattr(backend, method, None)) for method in _PROCESS_BACKEND_METHODS
    ):
        raise ProductionBackendUnavailable(f"生产 {label} 后端构造结果无效")
    return backend


def build_observer_argv(
    interpreter: str | Path,
    paths: AttemptArtifactPaths,
) -> tuple[str, ...]:
    """固定 outer observer 与 inner executor 共用同一已证明解释器。"""
    executable = Path(interpreter)
    if not executable.is_absolute():
        raise ValueError("隔离 observer 解释器必须是绝对路径")
    if type(paths) is not AttemptArtifactPaths:
        raise ValueError("隔离 observer 必须使用 AttemptArtifactPaths")
    required_paths = (paths.spec, paths.result, paths.receipt)
    if any(not path.is_absolute() for path in required_paths):
        raise ValueError("隔离 observer artifact 路径必须是绝对路径")
    resolved = str(executable)
    return (
        resolved,
        "-I",
        "-m",
        "codev_platform.reindex.executor_observer",
        "--spec",
        str(paths.spec),
        "--result",
        str(paths.result),
        "--receipt",
        str(paths.receipt),
        "--",
        resolved,
        "-I",
        "-m",
        "codev_platform.reindex.executor",
        "--spec",
        str(paths.spec),
        "--result",
        str(paths.result),
    )


def build_isolated_worker(cfg: dict, instance_token: str) -> IsolatedWorkerRuntime:
    """构造默认 isolated worker；任何前置失败均发生在 claim 之前。"""
    if execution_mode(cfg) != "isolated":
        raise ExecutionModeError("legacy 模式不得构造 isolated worker")
    queue = _open_strict_queue()
    startup = load_queue_startup_context(queue=queue, cfg=cfg)
    backend = select_production_backend()
    identity = _load_runtime_identity()
    return _assemble_isolated_runtime(
        cfg=cfg,
        instance_token=instance_token,
        queue=queue,
        startup=startup,
        backend=backend,
        identity=identity,
    )


def _open_strict_queue() -> object:
    from . import open_default_queue

    return open_default_queue(fail_soft=False)


def _load_runtime_identity() -> object:
    from codev_platform.core.runtime_identity import runtime_identity

    return runtime_identity()


def _assemble_isolated_runtime(
    *,
    cfg: dict,
    instance_token: str,
    queue: object,
    startup: QueueStartupContext,
    backend: object,
    identity: object,
) -> IsolatedWorkerRuntime:
    from codev_platform.core.paths import data_root, index_manifest_path, logs_dir
    from codev_platform.core.repos import configured_project_ids, project_repo_specs
    from codev_platform.core.runtime_artifact_io import prepare_runtime_artifact_directory
    from codev_platform.core.runtime_interpreter import proven_interpreter_path

    from .attempt_artifacts import FilesystemAttemptArtifactStore
    from .attempt_cleanup import AttemptCleanupRouter, ConfiguredAttemptCleanup
    from .dependency_gate import ManifestDependencyGate
    from .health_refresh import ContainedHealthRefresher, HealthRefreshCommand
    from .isolated_worker_loop import IsolatedWorkerLoop
    from .orchestrator import AttemptOrchestrator
    from .orchestrator_models import SystemControlClock
    from .result_publisher import ResultPublisher
    from .spec_factory import AttemptSpecFactory, ConfiguredInputSelector
    from .supervisor_status_adapter import SupervisorStatusAdapter

    interpreter = proven_interpreter_path(identity)
    settings = _orchestrator_settings()
    status = SupervisorStatusAdapter(instance_token)
    manifest = index_manifest_path()
    artifacts = FilesystemAttemptArtifactStore(data_root() / "reindex_attempts")
    health_logs = prepare_runtime_artifact_directory(logs_dir() / "reindex-health")

    def health_command(project_id: str) -> HealthRefreshCommand:
        specs = project_repo_specs(project_id, cfg=cfg)
        if not specs:
            raise ValueError("health 项目未配置可用仓库")
        digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
        repo = specs[0].root
        return HealthRefreshCommand(
            (
                str(interpreter),
                "-I",
                "-m",
                "codev_platform.cli",
                "health",
                "--mode",
                "light",
                "--repo",
                str(repo),
                "--project",
                project_id,
                "--json-out",
            ),
            repo,
            health_logs / f"{digest}.bootstrap.log",
        )

    clock = SystemControlClock()
    health = ContainedHealthRefresher(
        process_backend=backend,
        journal=startup.journal,
        status=status,
        command_factory=health_command,
        timeout_sec=settings.health_timeout_sec,
        kill_grace_sec=settings.kill_grace_sec,
        poll_interval_sec=settings.poll_sec,
    )
    factory = AttemptSpecFactory(
        runtime_identity=identity,
        input_selector=ConfiguredInputSelector(),
        timeout_for_kind=lambda kind: _attempt_timeout(cfg, kind),
        attempt_id_factory=_new_identifier,
        fence_factory=_new_identifier,
    )
    orchestrator = AttemptOrchestrator(
        queue=queue,
        process_backend=backend,
        artifacts=artifacts,
        dependency_gate=ManifestDependencyGate(
            manifest_path=manifest,
            queue_view=queue,
            queue_timeout_sec=settings.queue_op_timeout_sec,
        ),
        publisher=ResultPublisher(manifest),
        journal=startup.journal,
        cleanup_router=AttemptCleanupRouter({"configured": ConfiguredAttemptCleanup()}),
        spec_factory=factory,
        observer_argv=lambda paths: build_observer_argv(interpreter, paths),
        health_refresh=health,
        clock=clock,
        settings=settings,
        owner_token=startup.owner.token,
        projects=set(configured_project_ids(cfg)),
        cwd=Path.cwd().resolve(),
        status=status,
    )
    loop = IsolatedWorkerLoop(
        orchestrator=orchestrator,
        health_refresh=health,
        projects=configured_project_ids(cfg),
        clock=clock,
        health_timeout_sec=settings.health_timeout_sec,
        legacy_audit=lambda: _require_no_legacy_queue_entries(queue, startup.owner.token),
    )
    return IsolatedWorkerRuntime(loop, queue, startup.owner.token)


def _orchestrator_settings() -> object:
    from .orchestrator_models import OrchestratorSettings

    return OrchestratorSettings(
        poll_sec=1.0,
        queue_op_timeout_sec=5.0,
        heartbeat_sec=10.0,
        renew_sec=20.0,
        lease_ttl_sec=90.0,
        kill_grace_sec=10.0,
        kill_timeout_sec=30.0,
        cleanup_timeout_sec=30.0,
        startup_timeout_sec=30.0,
        readiness_timeout_sec=5.0,
        recovery_timeout_sec=60.0,
        health_timeout_sec=120.0,
    )


_ATTEMPT_TIMEOUT_GRACE_SEC = 300.0


def _attempt_timeout(cfg: dict, kind: str) -> float:
    """外层 attempt 覆盖 runner 时限并保留结果落盘与进程回收窗口。"""
    from .runners import _runner_timeout, get_runner

    runner = get_runner(kind)
    timeout_for_runner = getattr(runner, "runner_timeout", None)
    if callable(timeout_for_runner):
        runner_timeout = float(timeout_for_runner(cfg))
    else:
        runner_timeout = _runner_timeout(cfg)
    return runner_timeout + _ATTEMPT_TIMEOUT_GRACE_SEC


def _new_identifier() -> str:
    from uuid import uuid4

    return uuid4().hex


def _require_no_legacy_queue_entries(queue: object, stable_owner_token: str) -> None:
    from .queue_migration import detect_legacy_queue_entries

    entries = detect_legacy_queue_entries(
        queue.snapshot(),
        stable_owner_token=stable_owner_token,
    )
    if entries:
        raise LegacyQueueMigrationRequired(
            f"检测到 {len(entries)} 条旧格式队列记录，必须先执行显式迁移"
        )


__all__ = [
    "ExecutionModeError",
    "IsolatedWorkerRuntime",
    "LegacyQueueMigrationRequired",
    "ProductionBackendUnavailable",
    "build_isolated_worker",
    "build_observer_argv",
    "execution_mode",
    "select_production_backend",
]
