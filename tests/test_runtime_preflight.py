"""版本运行时目标用户权限探针测试。"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from pathlib import Path

import pytest

import codev_platform
import codev_platform.runtime_preflight as preflight
from codev_platform.core.runtime_models import SystemdRuntime
from codev_platform.runtime_release_environment import MANAGED_IMPORTS
from codev_platform.runtime_preflight import (
    PathRequirement,
    RuntimePreflightError,
    main,
    permission_requirements,
    probe_current_user,
    probe_current_user_until,
)


def test_受管导入包含_chroma_实际生命周期入口() -> None:
    assert "codev_platform.chroma.daemon_entry" in MANAGED_IMPORTS


def minimal_config(root: Path) -> dict[str, object]:
    """构造不引入 Web 依赖的最小运行时配置。"""
    return {
        "runtime": {
            "release_root": str(root),
            "chroma_venv": str(root / "legacy" / ".venv"),
        },
        "projects": {},
    }


_POSIX = os.name == "posix"
_FILE_CHECKS = (
    "runtime_identity_release",
    "runtime_root_traverse",
    "runtime_release_read_execute",
    "runtime_python_read_execute",
    "runtime_base_read_execute",
    "runtime_base_python_read_execute",
    "application_import_source",
    "data_root_read_execute",
    "logs_directory_rw",
    "chroma_directory_rw",
    "codegraph_directory_rw",
    "graph_store_directory_rw",
    "agent_memory_vector_directory_rw",
    "file_queue_operational_rw",
    "journal_atomic_replace",
    "run_lock_create_flock",
)


def _runtime_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    backend: str = "file",
) -> tuple[dict[str, object], SystemdRuntime, Path, Path]:
    runtime_root = tmp_path / "runtime"
    current = runtime_root / "current"
    python = current / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    python.chmod(0o755)
    base_python = current / "base" / "venv" / "bin" / "python"
    base_python.parent.mkdir(parents=True)
    base_python.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    base_python.chmod(0o755)

    package = current / "venv" / "lib" / "site-packages" / "codev_platform"
    package.mkdir(parents=True)
    package_file = package / "__init__.py"
    package_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(codev_platform, "__file__", str(package_file))
    module_files: dict[str, Path] = {}
    for module_name in MANAGED_IMPORTS:
        relative = Path(*module_name.split(".")[1:]).with_suffix(".py")
        module_file = package / relative
        module_file.parent.mkdir(parents=True, exist_ok=True)
        module_file.write_text("", encoding="utf-8")
        module_files[module_name] = module_file
    monkeypatch.setattr(
        preflight,
        "_import_managed_module",
        lambda module_name: SimpleNamespace(__file__=str(module_files[module_name])),
        raising=False,
    )
    monkeypatch.setattr(
        "codev_platform.core.runtime_identity.runtime_identity",
        lambda: SimpleNamespace(
            mode="release",
            environment_prefix=str(current / "venv"),
            interpreter_realpath=str(python.resolve()),
        ),
    )

    data_root = tmp_path / "platform-data"
    queue_root = data_root / "reindex_queue"
    for phase in ("pending", "active", "results", "quarantined", "locks"):
        (queue_root / phase).mkdir(parents=True, exist_ok=True)
    (data_root / "run").mkdir(parents=True)
    for relative in ("logs", "chroma", "codegraph_ext", "graph_store"):
        (data_root / relative).mkdir(parents=True)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(data_root))

    cfg = minimal_config(runtime_root)
    cfg["data"] = {"platform_data_dir": str(data_root)}
    cfg["reindex"] = {"queue_backend": backend}
    return cfg, SystemdRuntime(runtime_root), data_root, package_file


def _sentinels(root: Path) -> list[Path]:
    return list(root.rglob(".codev-preflight-*"))


def test_permission_requirements_reuse_runtime_and_reindex_path_truths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, data_root, _package = _runtime_layout(tmp_path, monkeypatch)

    requirements = permission_requirements(cfg, runtime)

    assert isinstance(requirements, tuple)
    assert tuple(item.name for item in requirements) == _FILE_CHECKS
    by_name = {item.name: item for item in requirements}
    assert by_name["runtime_identity_release"].path == runtime.release_root / "current"
    assert by_name["runtime_root_traverse"].path == runtime.release_root
    assert by_name["runtime_root_traverse"].kind == "directory_traverse"
    assert by_name["runtime_release_read_execute"].path == runtime.release_root / "current"
    assert by_name["runtime_python_read_execute"].path == runtime.python
    assert by_name["runtime_base_read_execute"].path == runtime.release_root / "current" / "base"
    assert by_name["runtime_base_python_read_execute"].path == (
        runtime.release_root / "current" / "base" / "venv" / "bin" / "python"
    )
    assert by_name["file_queue_operational_rw"].path == data_root / "reindex_queue"
    assert by_name["logs_directory_rw"].path == data_root / "logs"
    assert by_name["chroma_directory_rw"].path == data_root / "chroma"
    assert by_name["codegraph_directory_rw"].path == data_root / "codegraph_ext"
    assert by_name["graph_store_directory_rw"].path == data_root / "graph_store"
    assert by_name["agent_memory_vector_directory_rw"].path == data_root / "chroma"
    assert by_name["journal_atomic_replace"].path == data_root / "run"
    assert by_name["run_lock_create_flock"].path == data_root / "run"
    assert all(str(tmp_path) not in repr(item) for item in requirements)


def test_permission_requirements_are_pure_and_pg_uses_readonly_backend_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    data_root = tmp_path / "missing-data"
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(data_root))
    cfg = minimal_config(runtime_root)
    cfg["data"] = {"platform_data_dir": str(data_root)}
    cfg["reindex"] = {"queue_backend": "pg"}

    requirements = permission_requirements(cfg, SystemdRuntime(runtime_root))

    names = tuple(item.name for item in requirements)
    assert "queue_backend_readonly" in names
    assert "file_queue_operational_rw" not in names
    assert next(item for item in requirements if item.name == "queue_backend_readonly").path is None
    assert not runtime_root.exists()
    assert not data_root.exists()


def test_permission_requirements_resolve_every_write_path_from_same_cfg_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    configured_data = tmp_path / "configured-data"
    monkeypatch.delenv("PLATFORM_DATA_DIR", raising=False)
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: pytest.fail("权限计划不得二次加载配置"),
    )
    cfg = minimal_config(runtime_root)
    cfg["data"] = {"platform_data_dir": str(configured_data)}
    cfg["reindex"] = {"queue_backend": "file"}

    requirements = permission_requirements(cfg, SystemdRuntime(runtime_root))

    by_name = {item.name: item for item in requirements}
    expected = {
        "data_root_read_execute": configured_data,
        "logs_directory_rw": configured_data / "logs",
        "chroma_directory_rw": configured_data / "chroma",
        "codegraph_directory_rw": configured_data / "codegraph_ext",
        "graph_store_directory_rw": configured_data / "graph_store",
        "agent_memory_vector_directory_rw": configured_data / "chroma",
        "file_queue_operational_rw": configured_data / "reindex_queue",
        "journal_atomic_replace": configured_data / "run",
        "run_lock_create_flock": configured_data / "run",
    }
    assert {name: by_name[name].path for name in expected} == expected
    assert not configured_data.exists()


def test_permission_requirements_reject_invalid_contracts(tmp_path: Path) -> None:
    runtime = SystemdRuntime(tmp_path / "runtime")
    with pytest.raises(TypeError, match="配置"):
        permission_requirements([], runtime)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SystemdRuntime"):
        permission_requirements({}, object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="queue_backend"):
        permission_requirements({"reindex": {"queue_backend": "memory"}}, runtime)


def test_probe_rejects_non_release_identity_before_path_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, _data_root, _package = _runtime_layout(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "codev_platform.core.runtime_identity.runtime_identity",
        lambda: SimpleNamespace(
            mode="editable",
            environment_prefix=str(runtime.release_root / "current" / "venv"),
            interpreter_realpath=str(runtime.python),
        ),
    )

    with pytest.raises(RuntimePreflightError) as caught:
        probe_current_user(permission_requirements(cfg, runtime))

    assert caught.value.check_name == "runtime_identity_release"


@pytest.mark.skipif(not _POSIX, reason="真实权限、目录 fsync 与 flock 仅在 WSL/Linux 验证")
def test_probe_current_user_uses_only_random_sentinels_and_preserves_real_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, data_root, _package = _runtime_layout(tmp_path, monkeypatch)
    quarantine_record = data_root / "reindex_queue" / "quarantined" / "real.json"
    journal = data_root / "run" / "reindex-control-journal.json"
    run_lock = data_root / "run" / "reindex-worker-run.lock"
    quarantine_record.write_text("queue-real", encoding="utf-8")
    journal.write_text("journal-real", encoding="utf-8")
    run_lock.write_text("lock-real", encoding="utf-8")

    result = probe_current_user(permission_requirements(cfg, runtime))

    assert result is None
    assert quarantine_record.read_text(encoding="utf-8") == "queue-real"
    assert journal.read_text(encoding="utf-8") == "journal-real"
    assert run_lock.read_text(encoding="utf-8") == "lock-real"
    assert _sentinels(data_root) == []


@pytest.mark.skipif(not _POSIX, reason="真实权限仅在 WSL/Linux 验证")
def test_probe_rejects_non_executable_current_python_with_fixed_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, _data_root, _package = _runtime_layout(tmp_path, monkeypatch)
    runtime.python.chmod(0o644)

    with pytest.raises(RuntimePreflightError) as caught:
        probe_current_user(permission_requirements(cfg, runtime))

    assert caught.value.check_name == "runtime_python_read_execute"
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.skipif(not _POSIX, reason="真实导入来源仅在 WSL/Linux 验证")
def test_probe_rejects_application_import_outside_release_or_from_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, _data_root, _package = _runtime_layout(tmp_path, monkeypatch)
    outside = tmp_path / "checkout" / "codev_platform" / "__init__.py"
    outside.parent.mkdir(parents=True)
    outside.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        preflight,
        "_import_managed_module",
        lambda _module_name: SimpleNamespace(__file__=str(outside)),
        raising=False,
    )

    with pytest.raises(RuntimePreflightError) as outside_error:
        probe_current_user(permission_requirements(cfg, runtime))
    assert outside_error.value.check_name == "application_import_source"
    assert str(outside) not in str(outside_error.value)

    base_package = runtime.release_root / "current" / "base" / "codev_platform" / "__init__.py"
    base_package.parent.mkdir(parents=True)
    base_package.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        preflight,
        "_import_managed_module",
        lambda _module_name: SimpleNamespace(__file__=str(base_package)),
        raising=False,
    )
    with pytest.raises(RuntimePreflightError) as base_error:
        probe_current_user(permission_requirements(cfg, runtime))
    assert base_error.value.check_name == "application_import_source"


@pytest.mark.skipif(not _POSIX, reason="真实导入来源仅在 WSL/Linux 验证")
def test_probe_rejects_release_nested_in_git_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, _data_root, _package = _runtime_layout(tmp_path, monkeypatch)
    (runtime.release_root / ".git").mkdir()

    with pytest.raises(RuntimePreflightError) as caught:
        probe_current_user(permission_requirements(cfg, runtime))

    assert caught.value.check_name == "application_import_source"


def test_probe_imports_every_managed_entrypoint_from_current_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, _data_root, _package = _runtime_layout(tmp_path, monkeypatch)
    imported: list[str] = []
    release_package = (
        runtime.release_root / "current" / "venv" / "lib" / "site-packages" / "codev_platform"
    )

    def import_managed(module_name: str) -> SimpleNamespace:
        imported.append(module_name)
        module_file = release_package / Path(*module_name.split(".")[1:]).with_suffix(".py")
        return SimpleNamespace(__file__=str(module_file))

    monkeypatch.setattr(preflight, "_import_managed_module", import_managed, raising=False)
    requirement = next(
        item
        for item in permission_requirements(cfg, runtime)
        if item.name == "application_import_source"
    )

    probe_current_user((requirement,))

    assert tuple(imported) == MANAGED_IMPORTS


@pytest.mark.skipif(not _POSIX, reason="真实目录权限仅在 WSL/Linux 验证")
@pytest.mark.parametrize("phase", ["pending", "active", "results", "quarantined", "locks"])
def test_probe_rejects_unwritable_file_queue_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    cfg, runtime, data_root, _package = _runtime_layout(tmp_path, monkeypatch)
    phase_dir = data_root / "reindex_queue" / phase
    phase_dir.chmod(0o500)
    try:
        with pytest.raises(RuntimePreflightError) as caught:
            probe_current_user(permission_requirements(cfg, runtime))
    finally:
        phase_dir.chmod(0o700)

    assert caught.value.check_name == "file_queue_operational_rw"
    assert _sentinels(data_root) == []


@pytest.mark.skipif(not _POSIX, reason="真实目录 fsync 仅在 WSL/Linux 验证")
def test_probe_reports_atomic_replace_failure_without_leaking_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, data_root, _package = _runtime_layout(tmp_path, monkeypatch)

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("secret-path=/do/not/expose")

    monkeypatch.setattr(
        "codev_platform.runtime_preflight_filesystem.os.replace",
        fail_replace,
    )
    with pytest.raises(RuntimePreflightError) as caught:
        probe_current_user(permission_requirements(cfg, runtime))

    assert caught.value.check_name == "journal_atomic_replace"
    assert "secret" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)
    assert _sentinels(data_root) == []


@pytest.mark.skipif(not _POSIX, reason="真实 flock 仅在 WSL/Linux 验证")
def test_probe_reports_run_lock_flock_failure_and_cleans_sentinel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, runtime, data_root, _package = _runtime_layout(tmp_path, monkeypatch)

    def fail_flock(_descriptor: int) -> None:
        raise OSError("secret lock failure")

    monkeypatch.setattr(
        "codev_platform.runtime_preflight_filesystem._flock_exclusive",
        fail_flock,
    )
    with pytest.raises(RuntimePreflightError) as caught:
        probe_current_user(permission_requirements(cfg, runtime))

    assert caught.value.check_name == "run_lock_create_flock"
    assert "secret" not in str(caught.value)
    assert _sentinels(data_root) == []


def test_pg_probe_delegates_to_readonly_queue_status_without_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SystemdRuntime(tmp_path / "runtime")
    requirement = PathRequirement("queue_backend_readonly", "queue_readonly", None)
    called: list[str] = []
    monkeypatch.setattr(
        preflight,
        "_probe_queue_readonly",
        lambda _dsn, _deadline: called.append("readonly"),
    )
    probe_current_user((requirement,))

    assert runtime.release_root == tmp_path / "runtime"
    assert called == ["readonly"]


def test_pg_requirement_uses_dsn_from_planning_snapshot_without_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_dsn = "postgresql://snapshot@example.invalid/platform"
    cfg = minimal_config(tmp_path / "runtime")
    cfg["data"] = {"platform_data_dir": str(tmp_path / "data")}
    cfg["reindex"] = {"queue_backend": "pg"}
    cfg["memory"] = {"pg_dsn": snapshot_dsn}
    monkeypatch.delenv("CODEV_PLATFORM_MEMORY_DSN", raising=False)
    requirement = next(
        item
        for item in permission_requirements(cfg, SystemdRuntime(tmp_path / "runtime"))
        if item.name == "queue_backend_readonly"
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: pytest.fail("数据库探针不得二次加载配置"),
    )
    monkeypatch.setattr(
        preflight,
        "_probe_queue_readonly",
        lambda dsn, _deadline: calls.append(dsn),
    )

    probe_current_user((requirement,))

    assert calls == [snapshot_dsn]


def test_probe_has_injectable_overall_deadline_and_reports_current_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirement = PathRequirement(
        "runtime_root_traverse",
        "directory_traverse",
        tmp_path,
    )
    now = 0.0

    def monotonic() -> float:
        return now

    def slow_probe(_requirement: PathRequirement, _deadline: object) -> None:
        nonlocal now
        now = 2.0

    monkeypatch.setattr(preflight, "_probe_requirement", slow_probe)

    with pytest.raises(RuntimePreflightError) as caught:
        probe_current_user((requirement,), timeout_sec=1.0, monotonic=monotonic)

    assert caught.value.check_name == "runtime_root_traverse"


def test_probe_until_reuses_the_exact_absolute_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requirement = PathRequirement("runtime_root_traverse", "directory_traverse", tmp_path)
    deadline = preflight.ProbeDeadline.after(5.0, lambda: 10.0)
    seen: list[object] = []
    monkeypatch.setattr(
        preflight,
        "_probe_requirement",
        lambda _requirement, actual_deadline: seen.append(actual_deadline),
    )

    probe_current_user_until((requirement,), deadline)

    assert seen == [deadline]
    assert seen[0] is deadline


def test_runtime_preflight_main_is_lightweight_aggregation() -> None:
    source = Path(preflight.__file__).read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 280


def test_probe_cli_emits_only_fixed_structured_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_root = tmp_path / "secret-runtime"
    requirements = (
        PathRequirement("runtime_root_traverse", "directory_traverse", secret_root),
        PathRequirement("journal_atomic_replace", "atomic_directory", secret_root),
    )
    monkeypatch.setattr(preflight, "_load_probe_requirements", lambda: requirements)
    monkeypatch.setattr(preflight, "probe_current_user", lambda _requirements: None)
    monkeypatch.setenv("SECRET_TOKEN", "do-not-print")

    assert main(["probe"]) == 0

    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload == {
        "checks": [
            {"name": "runtime_root_traverse", "status": "passed"},
            {"name": "journal_atomic_replace", "status": "passed"},
        ],
        "ok": True,
        "schema_version": 1,
    }
    assert str(secret_root) not in raw
    assert "do-not-print" not in raw


def test_probe_cli_failure_contains_only_fixed_check_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    requirement = PathRequirement(
        "journal_atomic_replace",
        "atomic_directory",
        tmp_path / "secret-path",
    )
    monkeypatch.setattr(preflight, "_load_probe_requirements", lambda: (requirement,))

    def fail(_requirements: tuple[PathRequirement, ...]) -> None:
        raise RuntimePreflightError("journal_atomic_replace")

    monkeypatch.setattr(preflight, "probe_current_user", fail)

    assert main(["probe"]) == 1

    raw = capsys.readouterr().out
    assert json.loads(raw) == {
        "checks": [{"name": "journal_atomic_replace", "status": "failed"}],
        "ok": False,
        "schema_version": 1,
    }
    assert str(tmp_path) not in raw
