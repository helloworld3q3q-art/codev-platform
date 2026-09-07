"""生产部署只允许读取并抓取固定 `fuwuqi/dev` 目标。"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.runtime_deployment_contract import (
    PRODUCTION_GIT_BRANCH,
    PRODUCTION_GIT_REMOTE,
    DeploymentPlan,
    RuntimeDeploymentError,
)
from codev_platform.runtime_process import isolated_process_environment


_OID = re.compile(r"[0-9a-f]{40}\Z")
_MAX_OUTPUT_CHARS = 64 * 1024


@dataclass(frozen=True, slots=True)
class ProductionGitProof:
    target_revision: str
    evidence_sha256: str


GitRunner = Callable[[tuple[str, ...], bool], object]


def refresh_production_git_target(
    repo: Path,
    *,
    service_user: str,
    target_revision: str,
    runner: GitRunner | None = None,
) -> ProductionGitProof:
    """受控刷新唯一生产跟踪引用，并证明其 tip 精确等于目标提交。"""
    if type(service_user) is not str or _OID.fullmatch(target_revision) is None:
        raise RuntimeDeploymentError("生产 Git 目标无效")
    source = _require_service_repository(repo, service_user)
    run = _service_runner(service_user, source) if runner is None else runner
    try:
        remote = _run(run, ("git", "remote", "get-url", PRODUCTION_GIT_REMOTE), False)
        _require_non_github_remote(remote)
        _run(
            run,
            (
                "git",
                "fetch",
                "--quiet",
                "--no-tags",
                PRODUCTION_GIT_REMOTE,
                f"+refs/heads/{PRODUCTION_GIT_BRANCH}:"
                f"refs/remotes/{PRODUCTION_GIT_REMOTE}/{PRODUCTION_GIT_BRANCH}",
            ),
            True,
        )
        resolved = _run(
            run,
            (
                "git",
                "rev-parse",
                "--verify",
                f"refs/remotes/{PRODUCTION_GIT_REMOTE}/{PRODUCTION_GIT_BRANCH}^{{commit}}",
            ),
            False,
        ).strip()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("生产 Git 目标验证失败") from None
    if resolved != target_revision:
        raise RuntimeDeploymentError("部署目标不等于 fuwuqi/dev 最新提交")
    evidence = hashlib.sha256(
        f"{PRODUCTION_GIT_REMOTE}\n{PRODUCTION_GIT_BRANCH}\n{resolved}\n".encode("ascii")
    ).hexdigest()
    return ProductionGitProof(resolved, evidence)


def verify_production_git_target(
    plan: DeploymentPlan,
    *,
    runner: GitRunner | None = None,
) -> ProductionGitProof:
    """抓取固定远端分支并要求其 tip 精确等于部署计划，不读取或输出其他远端。"""
    if type(plan) is not DeploymentPlan:
        raise RuntimeDeploymentError("部署 Git 计划无效")
    return refresh_production_git_target(
        Path(plan.source_repo),
        service_user=plan.service_user,
        target_revision=plan.target_revision,
        runner=runner,
    )


def verify_production_git_revision(
    plan: DeploymentPlan,
    *,
    runner: GitRunner | None = None,
) -> ProductionGitProof:
    """恢复期只证明目标仍属于受信跟踪历史，不要求分支 tip 永远停留。"""
    if type(plan) is not DeploymentPlan:
        raise RuntimeDeploymentError("部署 Git 计划无效")
    repo = _require_service_repository(Path(plan.source_repo), plan.service_user)
    run = _service_runner(plan.service_user, repo) if runner is None else runner
    reference = f"refs/remotes/{PRODUCTION_GIT_REMOTE}/{PRODUCTION_GIT_BRANCH}"
    try:
        remote = _run(run, ("git", "remote", "get-url", PRODUCTION_GIT_REMOTE), False)
        _require_non_github_remote(remote)
        _run(run, ("git", "cat-file", "-e", f"{plan.target_revision}^{{commit}}"), False)
        tip = _run(
            run,
            ("git", "rev-parse", "--verify", f"{reference}^{{commit}}"),
            False,
        ).strip()
        if _OID.fullmatch(tip) is None:
            raise RuntimeDeploymentError("fuwuqi/dev 跟踪提交无效")
        try:
            _run(
                run,
                ("git", "merge-base", "--is-ancestor", plan.target_revision, reference),
                False,
            )
        except RuntimeDeploymentError:
            raise RuntimeDeploymentError("部署目标已脱离 fuwuqi/dev 受信历史") from None
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("生产 Git 恢复证明失败") from None
    evidence = hashlib.sha256(
        (
            f"{PRODUCTION_GIT_REMOTE}\n{PRODUCTION_GIT_BRANCH}\n"
            f"{plan.target_revision}\n{tip}\n"
        ).encode("ascii")
    ).hexdigest()
    return ProductionGitProof(plan.target_revision, evidence)


def _run(runner: GitRunner, command: tuple[str, ...], network: bool) -> str:
    result = runner(command, network)
    returncode = getattr(result, "returncode", None)
    stdout = getattr(result, "stdout", None)
    if type(returncode) is not int or returncode != 0 or type(stdout) is not str:
        raise RuntimeDeploymentError("固定 Git 命令执行失败")
    if len(stdout) > _MAX_OUTPUT_CHARS or "\x00" in stdout:
        raise RuntimeDeploymentError("固定 Git 命令输出无效")
    return stdout


def _require_non_github_remote(value: str) -> None:
    lines = value.splitlines()
    if (
        len(lines) != 1
        or not lines[0].strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in lines[0])
        or "github.com" in lines[0].casefold()
    ):
        raise RuntimeDeploymentError("fuwuqi 远端地址不受信任")


def _require_service_repository(path: Path, user: str) -> Path:
    try:
        import pwd

        account = pwd.getpwnam(user)
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except (ImportError, KeyError, OSError, RuntimeError, ValueError):
        raise RuntimeDeploymentError("服务器源码仓不可用") from None
    if (
        not path.is_absolute()
        or path != resolved
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != account.pw_uid
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or path.as_posix().startswith("/mnt/")
    ):
        raise RuntimeDeploymentError("服务器源码仓所有权或路径不受信任")
    return resolved


def _service_runner(user: str, repo: Path) -> GitRunner:
    try:
        import pwd

        account = pwd.getpwnam(user)
    except (ImportError, KeyError):
        raise RuntimeDeploymentError("服务账号不存在") from None
    current = os.geteuid()
    if current not in {0, account.pw_uid}:
        raise RuntimeDeploymentError("部署 Git 命令用户身份无效")

    def run(command: tuple[str, ...], network: bool) -> object:
        argv = (*command[:1], "-C", str(repo), *command[1:])
        if current == 0:
            argv = ("/usr/sbin/runuser", "--user", user, "--", *argv)
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            check=False,
            timeout=300.0 if network else 30.0,
            env=isolated_process_environment(
                network=network,
                overrides={"HOME": account.pw_dir},
            ),
        )

    return run


__all__ = [
    "ProductionGitProof",
    "refresh_production_git_target",
    "verify_production_git_revision",
    "verify_production_git_target",
]
