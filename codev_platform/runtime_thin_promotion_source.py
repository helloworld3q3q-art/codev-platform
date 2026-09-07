"""日常薄发布的源码提交门禁、root 私有候选构建与薄 release 暂存。"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from codev_platform.core.runtime_models import (
    ReleaseCandidate,
    ReleaseMetadata,
    require_runtime_revision,
    require_sha256,
)
from codev_platform.runtime_build import stage_release
from codev_platform.runtime_candidate import compute_candidate_id
from codev_platform.runtime_deployment_contract import (
    PRODUCTION_GIT_BRANCH,
    PRODUCTION_GIT_REMOTE,
)
from codev_platform.runtime_deployment_git import (
    ProductionGitProof,
    refresh_production_git_target,
)
from codev_platform.runtime_git_snapshot import git_command_context
from codev_platform.runtime_process import isolated_process_environment
from codev_platform.runtime_release_binding import BoundRelease, snapshot_current_release
from codev_platform.runtime_service_process import (
    ServiceAccount,
    resolve_service_account,
)
from codev_platform.runtime_thin_promotion import ThinPromotionError


_DEPENDENCY_PATHS = (
    "pyproject.toml",
    "requirements/wsl-runtime.freeze",
    "requirements-runtime.txt",
)
_SYSTEMD_CONTRACT_PATHS = (
    "codev_platform/mcp_serve.py",
    "codev_platform/mcp_systemd.py",
    ":(glob)codev_platform/mcp_systemd_*.py",
    "codev_platform/runtime_preflight.py",
    ":(glob)codev_platform/runtime_preflight_*.py",
    "codev_platform/runtime_service_process.py",
    "codev_platform/core/config.py",
    "codev_platform/core/runtime_identity.py",
    ":(glob)codev_platform/core/systemd_*.py",
    "codev_platform/web/config.py",
    "codev_platform/webhook/server.py",
)
_BUILD_TIMEOUT_SEC = 900.0
_MAX_BUILD_RECEIPT_BYTES = 64 * 1024
_CANDIDATE_DIRECTORY_NAME = "candidates"


class _GitCommandContext(Protocol):
    """日常发布只需要的受限 Git 读取能力。"""

    cwd: Path
    environment: Mapping[str, str]

    def command(self, *arguments: str) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class ThinStagedRelease:
    """已从受推送提交构建并静态验证的薄 release 与原始锚点。"""

    baseline_release_id: str
    target_release_id: str
    target_revision: str

    def __post_init__(self) -> None:
        try:
            require_sha256(self.baseline_release_id, field="baseline_release_id")
            require_sha256(self.target_release_id, field="target_release_id")
            require_runtime_revision(self.target_revision, git_only=True)
        except ValueError:
            raise ThinPromotionError("日常薄发布暂存身份无效") from None
        if self.baseline_release_id == self.target_release_id:
            raise ThinPromotionError("日常薄发布暂存目标不能等于基线")


def stage_daily_target_release(
    runtime_root: Path,
    *,
    repo: Path,
    target_revision: str,
    service_user: str,
) -> ThinStagedRelease:
    """只为已推送、无依赖/运行时契约漂移的提交生成薄 release。"""
    _require_linux_root()
    root = _runtime_root(runtime_root)
    target = _revision(target_revision)
    account = _service_account(service_user)
    baseline = _baseline(root)
    if baseline.runtime_revision == target:
        raise ThinPromotionError("日常薄发布目标提交已是 current")
    source = _source_repository(repo)
    _refresh_production_tracking_target(source, account=account, target=target)
    context = _git_context(source, account)
    _verify_daily_target(context, baseline=baseline, target=target)
    output = _candidate_root(root)
    candidate = _build_candidate_as_root(
        baseline,
        source=source,
        target=target,
        output=output,
        account=account,
    )
    release = _stage_candidate(root, baseline=baseline, candidate=candidate, output=output)
    _require_unchanged_baseline(root, baseline)
    return ThinStagedRelease(
        baseline_release_id=baseline.release_id,
        target_release_id=release.release_id,
        target_revision=target,
    )


def _refresh_production_tracking_target(
    source: Path,
    *,
    account: ServiceAccount,
    target: str,
) -> None:
    """仅刷新固定生产分支；随后仍由本模块执行薄发布专属门禁。"""
    try:
        proof = refresh_production_git_target(
            source,
            service_user=account.name,
            target_revision=target,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布生产 Git 目标刷新失败") from None
    if type(proof) is not ProductionGitProof or proof.target_revision != target:
        raise ThinPromotionError("日常薄发布生产 Git 目标刷新无效")


def _verify_daily_target(
    context: _GitCommandContext,
    *,
    baseline: BoundRelease,
    target: str,
) -> None:
    _require_clean_worktree(context)
    remote_tip = _git_output(
        context,
        "rev-parse",
        "--verify",
        f"{PRODUCTION_GIT_REMOTE}/{PRODUCTION_GIT_BRANCH}^{{commit}}",
    )
    if remote_tip != target:
        raise ThinPromotionError("日常薄发布目标不是 fuwuqi/dev 当前提交")
    if not _git_succeeds(context, "merge-base", "--is-ancestor", baseline.runtime_revision, target):
        raise ThinPromotionError("日常薄发布目标不包含 current 版本")
    _require_no_diff(context, baseline.runtime_revision, target, _DEPENDENCY_PATHS, "依赖")
    _require_no_diff(context, baseline.runtime_revision, target, _SYSTEMD_CONTRACT_PATHS, "运行时契约")


def _require_clean_worktree(context: _GitCommandContext) -> None:
    completed = _git_run(
        context,
        "status",
        "--porcelain=v1",
        "--untracked-files=normal",
        capture_output=True,
    )
    if completed.returncode != 0 or completed.stdout:
        raise ThinPromotionError("日常薄发布源码仓必须保持干净")


def _require_no_diff(
    context: _GitCommandContext,
    baseline_revision: str,
    target_revision: str,
    paths: tuple[str, ...],
    label: str,
) -> None:
    completed = _git_run(
        context,
        "diff",
        "--quiet",
        baseline_revision,
        target_revision,
        "--",
        *paths,
        capture_output=False,
    )
    if completed.returncode == 0:
        return
    if completed.returncode == 1:
        raise ThinPromotionError(f"日常薄发布不接受{label}漂移")
    raise ThinPromotionError("日常薄发布 Git 身份无法证明")


def _build_candidate_as_root(
    baseline: BoundRelease,
    *,
    source: Path,
    target: str,
    output: Path,
    account: ServiceAccount,
) -> ReleaseCandidate:
    """root 写入私有候选根，Git 读取仅通过既有服务账号身份桥。"""
    command = (
        baseline.interpreter_path.as_posix(),
        "-B",
        "-I",
        "-m",
        "codev_platform.cli",
        "runtime",
        "build",
        "--repo",
        source.as_posix(),
        "--out-dir",
        output.as_posix(),
        "--revision",
        target,
        "--source-user",
        account.name,
    )
    try:
        completed = subprocess.run(
            command,
            cwd=Path("/"),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_BUILD_TIMEOUT_SEC,
            check=False,
            env=isolated_process_environment(),
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布候选构建无法执行") from None
    if completed.returncode != 0 or type(completed.stdout) is not bytes:
        raise ThinPromotionError("日常薄发布候选构建失败")
    candidate = _decode_candidate_receipt(completed.stdout)
    if candidate.runtime_revision != target:
        raise ThinPromotionError("日常薄发布候选提交身份不一致")
    return candidate


def _decode_candidate_receipt(raw: bytes) -> ReleaseCandidate:
    if not raw or len(raw) > _MAX_BUILD_RECEIPT_BYTES:
        raise ThinPromotionError("日常薄发布候选构建回执无效")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if (
            type(payload) is not dict
            or set(payload) != {"kind", "result", "status"}
            or payload["kind"] != "candidate"
            or payload["status"] != "ok"
            or type(payload["result"]) is not dict
        ):
            raise ValueError("候选回执形状无效")
        candidate = ReleaseCandidate(**payload["result"])
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ThinPromotionError("日常薄发布候选构建回执无效") from None
    return candidate


def _stage_candidate(
    runtime_root: Path,
    *,
    baseline: BoundRelease,
    candidate: ReleaseCandidate,
    output: Path,
) -> ReleaseMetadata:
    directory = output / compute_candidate_id(candidate.runtime_revision, candidate.wheel_sha256)
    wheel = directory / candidate.wheel_name
    candidate_file = directory / f"{candidate.wheel_name}.candidate.json"
    try:
        release = stage_release(runtime_root, wheel, candidate_file, baseline.base_id)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布薄版本暂存失败") from None
    if (
        type(release) is not ReleaseMetadata
        or release.runtime_revision != candidate.runtime_revision
        or release.base_id != baseline.base_id
    ):
        raise ThinPromotionError("日常薄发布薄版本暂存身份不一致")
    return release


def _require_unchanged_baseline(runtime_root: Path, baseline: BoundRelease) -> None:
    try:
        current = snapshot_current_release(runtime_root)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布 current 身份无法复核") from None
    if (
        current.release_id != baseline.release_id
        or current.runtime_revision != baseline.runtime_revision
        or current.base_id != baseline.base_id
    ):
        raise ThinPromotionError("日常薄发布期间 current 已被其他发布链改变")


def _baseline(runtime_root: Path) -> BoundRelease:
    try:
        baseline = snapshot_current_release(runtime_root)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布 current 身份无法证明") from None
    if type(baseline) is not BoundRelease:
        raise ThinPromotionError("日常薄发布 current 身份无法证明")
    return baseline


def _git_context(source: Path, account: ServiceAccount) -> _GitCommandContext:
    try:
        return git_command_context(source, source_user=account.name)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布源码仓身份不安全") from None


def _git_output(context: _GitCommandContext, *arguments: str) -> str:
    completed = _git_run(context, *arguments, capture_output=True)
    if completed.returncode != 0 or not completed.stdout or "\n" in completed.stdout.strip():
        raise ThinPromotionError("日常薄发布 Git 身份无法证明")
    return completed.stdout.strip()


def _git_succeeds(context: _GitCommandContext, *arguments: str) -> bool:
    return _git_run(context, *arguments, capture_output=False).returncode == 0


def _git_run(
    context: _GitCommandContext,
    *arguments: str,
    capture_output: bool,
) -> subprocess.CompletedProcess[str]:
    try:
        command = context.command(*arguments)
        cwd = context.cwd
        environment = context.environment
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=30.0,
            check=False,
            env=environment,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布 Git 身份无法证明") from None
    if capture_output and (type(completed.stdout) is not str or len(completed.stdout) > 64 * 1024):
        raise ThinPromotionError("日常薄发布 Git 输出无效")
    return completed


def _candidate_root(runtime_root: Path) -> Path:
    """只接受既有运行时根中 root 私有的候选输入目录。"""
    raw = runtime_root / _CANDIDATE_DIRECTORY_NAME
    candidate = Path(os.path.abspath(raw))
    if raw != candidate:
        raise ThinPromotionError("日常薄发布候选目录无效")
    metadata = _candidate_directory_metadata(candidate)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ThinPromotionError("日常薄发布候选目录不安全")
    return candidate


def _candidate_directory_metadata(candidate: Path) -> os.stat_result:
    try:
        return candidate.lstat()
    except OSError:
        raise ThinPromotionError("日常薄发布候选目录不可用") from None


def _service_account(value: object) -> ServiceAccount:
    if type(value) is not str:
        raise ThinPromotionError("日常薄发布服务账号无效")
    try:
        account = resolve_service_account(value)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ThinPromotionError("日常薄发布服务账号无效") from None
    if type(account) is not ServiceAccount:
        raise ThinPromotionError("日常薄发布服务账号无效")
    return account


def _source_repository(value: object) -> Path:
    try:
        raw = Path(value).expanduser()
        metadata = raw.lstat()
        source = raw.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ThinPromotionError("日常薄发布源码仓不可用") from None
    if (
        not raw.is_absolute()
        or raw.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or not source.is_dir()
    ):
        raise ThinPromotionError("日常薄发布源码仓不可用")
    return source


def _runtime_root(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ThinPromotionError("日常薄发布运行时根无效")
    return value


def _require_linux_root() -> None:
    if (
        os.name != "posix"
        or not sys.platform.startswith("linux")
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
    ):
        raise ThinPromotionError("日常薄发布只允许 Linux root 执行")


def _revision(value: object) -> str:
    try:
        return require_runtime_revision(value, git_only=True)
    except ValueError:
        raise ThinPromotionError("日常薄发布目标提交无效") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 包含重复字段")
        result[key] = value
    return result


__all__ = ["ThinStagedRelease", "stage_daily_target_release"]
