"""稳定运行文件的路径真值源，禁止调用方从 import 包或当前仓拼接。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.paths import data_root
from codev_platform.core.project_id import validate as validate_project_id


@dataclass(frozen=True, slots=True)
class RuntimeArtifactLayout:
    """一次解析的数据根视图，保证同次聚合不会跨环境重读配置。"""

    root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path) or not self.root.is_absolute():
            raise ValueError("运行文件数据根必须是绝对路径")

    @classmethod
    def current(cls) -> RuntimeArtifactLayout:
        return cls(data_root())

    @property
    def logs_root(self) -> Path:
        return self.root / "logs"

    def chroma_mcp_log_path(self) -> Path:
        return self.logs_root / "chroma_mcp_server.log"

    def chroma_recall_usage_path(self) -> Path:
        return self.logs_root / "search_recall.jsonl"

    def chroma_daemon_log_path(self) -> Path:
        return self.logs_root / "chroma_daemon.log"

    def chroma_spawn_lock_path(self) -> Path:
        return self.root / "run" / "chroma_daemon.spawn.lock"

    def chroma_daemon_lifecycle_lock_path(self) -> Path:
        return self.root / "run" / "chroma_daemon.lifecycle.lock"

    def codegraph_mcp_log_path(self) -> Path:
        return self.logs_root / "codegraph_mcp_server.log"

    def codegraph_usage_path(self) -> Path:
        return self.logs_root / "codegraph_usage.jsonl"

    def graph_usage_path(self) -> Path:
        return self.logs_root / "graph_usage.jsonl"

    def agent_memory_mcp_log_path(self) -> Path:
        return self.logs_root / "agent_memory_mcp_server.log"

    def agent_memory_usage_path(self) -> Path:
        return self.logs_root / "memory_mcp_usage.jsonl"

    def serve_mcp_log_dir(self) -> Path:
        return self.root / "mcp_serve_logs"

    def health_snapshot_path(self, project_id: str) -> Path:
        selected = validate_project_id(project_id)
        return self.root / "platform_meta" / "health" / f"{selected}.json"


def chroma_mcp_log_path() -> Path:
    """platform-docs MCP 服务日志。"""
    return RuntimeArtifactLayout.current().chroma_mcp_log_path()


def chroma_recall_usage_path() -> Path:
    """platform-docs 召回使用率 JSONL。"""
    return RuntimeArtifactLayout.current().chroma_recall_usage_path()


def chroma_daemon_log_path() -> Path:
    """本地 platform-docs daemon 的标准输出与错误日志。"""
    return RuntimeArtifactLayout.current().chroma_daemon_log_path()


def chroma_spawn_lock_path() -> Path:
    """跨会话串行拉起 platform-docs daemon 的短期文件锁。"""
    return RuntimeArtifactLayout.current().chroma_spawn_lock_path()


def chroma_daemon_lifecycle_lock_path() -> Path:
    """platform-docs daemon 从预热到退出全程持有的生命周期锁。"""
    return RuntimeArtifactLayout.current().chroma_daemon_lifecycle_lock_path()


def codegraph_mcp_log_path() -> Path:
    """CodeGraph MCP 服务日志。"""
    return RuntimeArtifactLayout.current().codegraph_mcp_log_path()


def codegraph_usage_path() -> Path:
    """CodeGraph MCP 使用率 JSONL。"""
    return RuntimeArtifactLayout.current().codegraph_usage_path()


def graph_usage_path() -> Path:
    """统一图谱 MCP 使用率 JSONL。"""
    return RuntimeArtifactLayout.current().graph_usage_path()


def agent_memory_mcp_log_path() -> Path:
    """Agent Memory MCP 服务日志。"""
    return RuntimeArtifactLayout.current().agent_memory_mcp_log_path()


def agent_memory_usage_path() -> Path:
    """Agent Memory MCP 使用率 JSONL。"""
    return RuntimeArtifactLayout.current().agent_memory_usage_path()


def serve_mcp_log_dir() -> Path:
    """本地 serve-mcp 拉起端点的分端点日志目录。"""
    return RuntimeArtifactLayout.current().serve_mcp_log_dir()


def health_snapshot_path(project_id: str) -> Path:
    """默认健康快照路径；显式 ``--json-out`` 不经过本函数。"""
    return RuntimeArtifactLayout.current().health_snapshot_path(project_id)


__all__ = [
    "agent_memory_mcp_log_path",
    "agent_memory_usage_path",
    "RuntimeArtifactLayout",
    "chroma_daemon_lifecycle_lock_path",
    "chroma_daemon_log_path",
    "chroma_mcp_log_path",
    "chroma_recall_usage_path",
    "chroma_spawn_lock_path",
    "codegraph_mcp_log_path",
    "codegraph_usage_path",
    "graph_usage_path",
    "health_snapshot_path",
    "serve_mcp_log_dir",
]
