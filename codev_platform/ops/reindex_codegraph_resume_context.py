"""CodeGraph 受控恢复所需的单一配置快照与仓集合。"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.config import (
    config_snapshot_digest,
    get as config_get,
    load_config,
)
from codev_platform.core.project_id import ProjectIdError, validate as validate_project_id
from codev_platform.core.repos import REINDEX_REPO_OVERRIDE_ENV, project_repo_specs
from codev_platform.core.runtime_interpreter import (
    ReleaseInterpreterIdentity,
    current_release_interpreter_identity,
)
from codev_platform.core.systemd_environment_file import (
    SystemdEnvironmentFilePathError,
    require_systemd_environment_file_path,
)
from codev_platform.reindex.target_commit import TargetCommitError, require_target_commit


_CONFIG_OVERLAY_ENV = "CODEV_PLATFORM_CONFIG"
_DATA_ROOT_ENV = "PLATFORM_DATA_DIR"


class CodegraphResumeContextError(RuntimeError):
    """CodeGraph 恢复无法从同一受控配置来源构造上下文。"""


@dataclass(frozen=True, slots=True)
class _OverlaySnapshot:
    path: Path
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class CodegraphResumeContext:
    """恢复全程复用的配置、仓根、manifest 路径快照。"""

    project_id: str
    target_commit: str
    runtime_release: ReleaseInterpreterIdentity
    config_path: Path
    config_digest: str
    data_root: Path
    manifest_path: Path
    repositories: tuple[Path, ...]
    health_url: str
    service_environment_path: Path | None = None

    @property
    def runtime_revision(self) -> str:
        """兼容现有索引清单字段，只从完整发布身份派生源码版本。"""
        return self.runtime_release.runtime_revision


def resolve_codegraph_resume_context(
    project_id: str,
    target_commit: str,
) -> CodegraphResumeContext:
    """只读取一次显式 overlay，并由同一 cfg 推导 manifest 与全部仓根。"""
    normalized_project = _require_project_id(project_id)
    normalized_target = _require_target_commit(target_commit)
    _require_no_runtime_repo_override()
    overlay = _require_explicit_regular_overlay()
    try:
        cfg = load_config()
    except Exception as error:
        raise CodegraphResumeContextError("恢复配置覆盖无法读取") from error
    _require_overlay_unchanged(overlay)
    try:
        digest = config_snapshot_digest(cfg)
    except Exception as error:
        raise CodegraphResumeContextError("恢复配置快照无法验证") from error
    data = _require_configured_data_root(cfg)
    _require_environment_data_root_matches(data)
    repositories = _resolve_repositories(normalized_project, cfg)
    health_url = _resolve_codegraph_health_url(cfg)
    service_environment_path = _resolve_service_environment_path(cfg)
    runtime_release = _resolve_runtime_release()
    return CodegraphResumeContext(
        project_id=normalized_project,
        target_commit=normalized_target,
        runtime_release=runtime_release,
        config_path=overlay.path,
        config_digest=digest,
        data_root=data,
        manifest_path=data / "index_manifest.sqlite",
        repositories=repositories,
        health_url=health_url,
        service_environment_path=service_environment_path,
    )


def _resolve_runtime_release() -> ReleaseInterpreterIdentity:
    """只从 core 取得完整发布身份，禁止退化为 Git OID 自证。"""
    try:
        identity = current_release_interpreter_identity()
    except Exception as error:
        raise CodegraphResumeContextError("恢复发布运行身份无法解析") from error
    if type(identity) is not ReleaseInterpreterIdentity:
        raise CodegraphResumeContextError("恢复发布运行身份无效")
    return identity


def _require_project_id(value: object) -> str:
    try:
        return validate_project_id(value)
    except (ProjectIdError, TypeError, ValueError):
        raise CodegraphResumeContextError("恢复项目标识无效") from None


def _require_target_commit(value: object) -> str:
    try:
        return require_target_commit(value)
    except (TargetCommitError, TypeError, ValueError):
        raise CodegraphResumeContextError("恢复目标提交无效") from None


def _require_no_runtime_repo_override() -> None:
    if REINDEX_REPO_OVERRIDE_ENV in os.environ:
        raise CodegraphResumeContextError("恢复不接受运行时仓覆盖环境")


def _require_explicit_regular_overlay() -> _OverlaySnapshot:
    raw = os.environ.get(_CONFIG_OVERLAY_ENV)
    if type(raw) is not str or not raw:
        raise CodegraphResumeContextError("恢复必须显式指定绝对配置覆盖")
    try:
        path = Path(raw)
        if not path.is_absolute():
            raise CodegraphResumeContextError("恢复配置覆盖必须为绝对路径")
        initial = path.lstat()
        if not stat.S_ISREG(initial.st_mode):
            raise CodegraphResumeContextError("恢复配置覆盖必须是常规文件")
        resolved = path.resolve(strict=True)
        with resolved.open("rb") as stream:
            current = os.fstat(stream.fileno())
            if (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino):
                raise CodegraphResumeContextError("恢复配置覆盖已变化")
            stream.read(1)
    except CodegraphResumeContextError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise CodegraphResumeContextError("恢复配置覆盖不可读") from None
    return _OverlaySnapshot(
        path=resolved,
        device=initial.st_dev,
        inode=initial.st_ino,
    )


def _require_overlay_unchanged(snapshot: _OverlaySnapshot) -> None:
    try:
        current = snapshot.path.lstat()
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
            snapshot.device,
            snapshot.inode,
        ):
            raise CodegraphResumeContextError("恢复配置覆盖已变化")
    except CodegraphResumeContextError:
        raise
    except OSError:
        raise CodegraphResumeContextError("恢复配置覆盖已变化") from None


def _require_configured_data_root(cfg: dict) -> Path:
    value = config_get(cfg, "data.platform_data_dir")
    if type(value) is not str or not value:
        raise CodegraphResumeContextError("恢复配置未提供绝对数据根")
    try:
        root = Path(value)
        if not root.is_absolute():
            raise CodegraphResumeContextError("恢复数据根必须为绝对路径")
        return root.resolve()
    except CodegraphResumeContextError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise CodegraphResumeContextError("恢复数据根不可用") from None


def _require_environment_data_root_matches(expected: Path) -> None:
    raw = os.environ.get(_DATA_ROOT_ENV)
    if type(raw) is not str or not raw:
        raise CodegraphResumeContextError("恢复必须显式指定环境数据根")
    try:
        actual = Path(raw)
        if not actual.is_absolute() or actual.resolve() != expected:
            raise CodegraphResumeContextError("环境数据根与恢复配置不一致")
    except CodegraphResumeContextError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise CodegraphResumeContextError("环境数据根无法验证") from None


def _resolve_repositories(project_id: str, cfg: dict) -> tuple[Path, ...]:
    try:
        repositories = tuple(spec.root for spec in project_repo_specs(project_id, cfg=cfg))
    except Exception as error:
        raise CodegraphResumeContextError("恢复仓集合无法解析") from error
    if not repositories:
        raise CodegraphResumeContextError("恢复仓集合不能为空")
    return repositories


def _resolve_codegraph_health_url(cfg: dict) -> str:
    """从已加载的同一配置快照导出唯一 CodeGraph 的公开健康地址。"""
    try:
        from codev_platform.mcp_serve import iter_endpoints

        endpoints = [endpoint for endpoint in iter_endpoints(cfg) if endpoint.kind == "codegraph"]
        if len(endpoints) != 1 or type(endpoints[0].health_url) is not str:
            raise CodegraphResumeContextError("恢复 CodeGraph 健康地址无法证明")
        return endpoints[0].health_url
    except CodegraphResumeContextError:
        raise
    except Exception:
        raise CodegraphResumeContextError("恢复 CodeGraph 健康地址无法证明") from None


def _resolve_service_environment_path(cfg: dict) -> Path | None:
    """按目标 systemd 的 POSIX 语义解析共享环境文件路径。"""
    value = config_get(cfg, "systemd.env_file")
    if value in (None, ""):
        return None
    try:
        return Path(require_systemd_environment_file_path(value))
    except SystemdEnvironmentFilePathError:
        raise CodegraphResumeContextError("服务环境文件路径无效") from None


__all__ = [
    "CodegraphResumeContext",
    "CodegraphResumeContextError",
    "resolve_codegraph_resume_context",
]
