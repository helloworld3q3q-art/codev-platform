"""稳定运行文件路径单一真值源测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

import codev_platform
from codev_platform.core import runtime_artifacts
from codev_platform.core.project_id import ProjectIdError


def test_全部默认运行文件位于data_root且不回写release包目录(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    package_root = Path(codev_platform.__file__).resolve().parent

    paths = (
        runtime_artifacts.chroma_mcp_log_path(),
        runtime_artifacts.chroma_recall_usage_path(),
        runtime_artifacts.chroma_daemon_log_path(),
        runtime_artifacts.chroma_spawn_lock_path(),
        runtime_artifacts.chroma_daemon_lifecycle_lock_path(),
        runtime_artifacts.codegraph_mcp_log_path(),
        runtime_artifacts.codegraph_usage_path(),
        runtime_artifacts.graph_usage_path(),
        runtime_artifacts.agent_memory_mcp_log_path(),
        runtime_artifacts.agent_memory_usage_path(),
        runtime_artifacts.serve_mcp_log_dir(),
        runtime_artifacts.health_snapshot_path("codev-platform"),
    )

    assert all(path.is_relative_to(tmp_path.resolve()) for path in paths)
    assert all(not path.is_relative_to(package_root) for path in paths)
    assert runtime_artifacts.serve_mcp_log_dir().name == "mcp_serve_logs"
    assert runtime_artifacts.chroma_daemon_lifecycle_lock_path() == (
        tmp_path.resolve() / "run" / "chroma_daemon.lifecycle.lock"
    )
    assert runtime_artifacts.health_snapshot_path("codev-platform").name == ("codev-platform.json")


def test_运行文件路径查询保持纯函数且不创建数据目录(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "尚未创建"
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(data))

    assert runtime_artifacts.codegraph_usage_path() == data / "logs" / "codegraph_usage.jsonl"
    assert runtime_artifacts.serve_mcp_log_dir() == data / "mcp_serve_logs"
    assert not data.exists()

    from codev_platform.core.paths import logs_dir

    assert logs_dir() == data / "logs"
    assert not data.exists()


def test_健康快照拒绝把项目标识解释为路径(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))

    with pytest.raises(ProjectIdError):
        runtime_artifacts.health_snapshot_path("../outside")


def test_运行文件写入者和读取者不再拼接包目录() -> None:
    package_root = Path(codev_platform.__file__).resolve().parent
    sources = {
        "mcp_runtime": package_root / "mcp_runtime.py",
        "ops_logs": package_root / "ops" / "logs.py",
        "chroma_launcher": package_root / "chroma" / "launcher.py",
        "codegraph_server": package_root / "codegraph" / "server.py",
        "ops_metrics": package_root / "ops" / "metrics.py",
        "platform_status": package_root / "platform_status.py",
    }

    for name, source_path in sources.items():
        source = source_path.read_text(encoding="utf-8")
        assert "core.runtime_artifacts" in source, name
    assert 'Path(__file__).resolve().parent / "mcp_serve_logs"' not in sources[
        "mcp_runtime"
    ].read_text(encoding="utf-8")
    assert 'SCRIPT_DIR / "daemon.log"' not in sources["chroma_launcher"].read_text(encoding="utf-8")


def test_稳定运行文件写侧统一经过安全权限边界() -> None:
    package_root = Path(codev_platform.__file__).resolve().parent
    writers = {
        "mcp_runtime": package_root / "mcp_runtime.py",
        "chroma_launcher": package_root / "chroma" / "launcher.py",
        "chroma_obslog": package_root / "chroma" / "_obslog.py",
        "codegraph": package_root / "codegraph" / "server.py",
        "graph": package_root / "graph" / "mcp_server.py",
        "agent_memory": package_root / "agent" / "memory_mcp.py",
        "health": package_root / "ops" / "health" / "__init__.py",
        "webhook": package_root / "webhook" / "server.py",
        "reindex_worker": package_root / "reindex" / "worker.py",
        "reindex_runner": package_root / "reindex" / "runner_logs.py",
        "reindex_health": package_root / "reindex" / "isolated_worker_composer.py",
    }

    for name, source_path in writers.items():
        source = source_path.read_text(encoding="utf-8")
        assert "core.runtime_artifact_io" in source, name


def test_健康快照默认路径与显式路径分流(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops import health

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    explicit = tmp_path / "custom.json"

    assert health._resolve_health_snapshot_path("", "codev-platform") == (
        tmp_path / "platform_meta" / "health" / "codev-platform.json"
    )
    assert health._resolve_health_snapshot_path(str(explicit), "codev-platform") == explicit
    assert health._resolve_health_snapshot_path("", None) is None
