"""attempt 输入策略、固定映射与 legacy 仓库准备兼容层。"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from codev_platform.core import repos as repo_catalog
from codev_platform.core.config import get as _cfg_get
from codev_platform.core.project_id import ProjectIdError, validate as validate_project_id
from codev_platform.reindex import git_sync
from codev_platform.reindex.attempts import (
    AttemptOutcome,
    AttemptSpec,
    CanonicalJsonObject,
)

if TYPE_CHECKING:
    from codev_platform.reindex.queue_ports import Job

_OID_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_REPO_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _valid_oid(value: object) -> bool:
    return type(value) is str and _OID_RE.fullmatch(value) is not None and set(value) != {"0"}


@dataclass(frozen=True, slots=True)
class MaterializedInput:
    """runner 实际消费的根目录与 Git 版本向量。"""

    root: str
    input_commits: tuple[tuple[str, str], ...]
    input_trees: tuple[tuple[str, str], ...]


class AttemptInputStrategy(Protocol):
    """在 executor 内物化并复核一种固定输入。"""

    def materialize(self, spec: AttemptSpec) -> MaterializedInput:
        raise NotImplementedError("input strategy method")

    def verify(
        self,
        spec: AttemptSpec,
        materialized: MaterializedInput,
    ) -> CanonicalJsonObject:
        raise NotImplementedError("input strategy method")


class AttemptInputError(RuntimeError):
    """输入适配器可显式交给 executor 的结构化失败。"""

    def __init__(self, outcome: AttemptOutcome, note: str, retryable: bool) -> None:
        super().__init__(note)
        self.outcome = outcome
        self.note = note
        self.retryable = retryable


def freeze_attempt_input_strategies(
    strategies: Mapping[str, AttemptInputStrategy],
) -> Mapping[str, AttemptInputStrategy]:
    """复制并冻结固定策略映射，拒绝动态类型。"""
    frozen = dict(strategies)
    allowed = {"configured", "exact_workspace"}
    if "configured" not in frozen or not set(frozen) <= allowed:
        raise ValueError("invalid fixed attempt input strategy mapping")
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class _ResolvedRepo:
    key: str
    root: Path


def _input_error(
    note: str,
    *,
    retryable: bool = False,
) -> AttemptInputError:
    outcome = AttemptOutcome.RETRYABLE if retryable else AttemptOutcome.FAILED
    return AttemptInputError(outcome, note, retryable)


def _validated_payload(spec: AttemptSpec) -> dict[str, str]:
    if type(spec) is not AttemptSpec or spec.input_kind != "configured":
        raise _input_error("configured 输入 spec 类型无效")
    try:
        project_id = validate_project_id(spec.project_id)
    except ProjectIdError:
        raise _input_error("configured project_id 无效") from None
    if project_id != spec.project_id:
        raise _input_error("configured project_id 必须是规范小写值")

    payload = spec.input_payload.to_value()
    if set(payload) - {"project_id", "repo_targets"} or payload.get("project_id") != project_id:
        raise _input_error("configured payload 字段或身份无效")
    targets = payload.get("repo_targets", {})
    if type(targets) is not dict:
        raise _input_error("configured repo_targets 必须是对象")
    validated: dict[str, str] = {}
    for key, target in targets.items():
        if type(key) is not str or _REPO_KEY_RE.fullmatch(key) is None:
            raise _input_error("configured repo target key 不安全")
        if not _valid_oid(target):
            raise _input_error("configured repo target OID 无效")
        validated[key] = target
    return validated


def _resolved_repos(spec: AttemptSpec, cfg: dict) -> tuple[_ResolvedRepo, ...]:
    try:
        raw_specs = repo_catalog.project_repo_specs(spec.project_id, cfg=cfg)
    except Exception:
        raise _input_error("configured 仓库配置解析失败") from None
    resolved: list[_ResolvedRepo] = []
    seen: set[str] = set()
    main_count = 0
    for repo_spec in raw_specs:
        key = "main" if repo_spec.is_main else str(repo_spec.tag or "")
        if not key or key in seen:
            raise _input_error("configured 仓库 key 缺失或重复")
        try:
            root = Path(repo_spec.root).resolve(strict=True)
        except OSError:
            raise _input_error(f"configured 仓库不可用: {key}") from None
        if not root.is_dir():
            raise _input_error(f"configured 仓库不是目录: {key}")
        seen.add(key)
        main_count += int(repo_spec.is_main)
        resolved.append(_ResolvedRepo(key, root))
    if not resolved or main_count != 1 or resolved[0].key != "main":
        raise _input_error("configured 主仓配置缺失或不唯一")
    try:
        repo_catalog.install_runtime_repo_override(cfg, spec.project_id, list(raw_specs))
    except ValueError:
        raise _input_error("configured 仓向量无法冻结") from None
    return tuple(resolved)


def _targets_by_key(
    requested: dict[str, str],
    repos: tuple[_ResolvedRepo, ...],
) -> dict[str, str]:
    known = {repo.key for repo in repos}
    targets: dict[str, str] = {}
    for raw_key, target in requested.items():
        key = "main" if raw_key == "primary" else raw_key
        if key not in known or key in targets:
            raise _input_error(f"configured repo target 未登记或重复: {raw_key}")
        targets[key] = target
    return targets


def _covers_targets(
    repos: tuple[_ResolvedRepo, ...],
    target_commit: str,
    repo_targets: dict[str, str],
) -> bool:
    global_covered = any(
        git_sync.repo_covers_commit(repo.root, target_commit)
        for repo in repos
    )
    if not global_covered:
        return False
    roots = {repo.key: repo.root for repo in repos}
    return all(
        git_sync.repo_covers_commit(roots[key], target)
        for key, target in repo_targets.items()
    )


def _sync_repos(repos: tuple[_ResolvedRepo, ...]) -> None:
    try:
        for repo in repos:
            git_sync.sync_repo_to_remote(repo.root)
    except Exception:
        raise _input_error("configured 仓库同步异常", retryable=True) from None


def _snapshot(repos: tuple[_ResolvedRepo, ...]) -> MaterializedInput:
    commits: list[tuple[str, str]] = []
    trees: list[tuple[str, str]] = []
    for repo in repos:
        if not git_sync.repo_is_clean(repo.root):
            raise _input_error(f"configured 仓库工作树不干净或不可验证: {repo.key}")
        head = git_sync.repo_head(repo.root)
        tree = git_sync.repo_tree(repo.root)
        if not _valid_oid(head) or not _valid_oid(tree):
            raise _input_error(f"configured 仓库 Git 身份不可验证: {repo.key}")
        commits.append((repo.key, head))
        trees.append((repo.key, tree))
    return MaterializedInput(str(repos[0].root), tuple(commits), tuple(trees))


class ConfiguredAttemptInputStrategy:
    """仅根据服务端配置物化 live clone，并输出可复核版本向量。"""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg

    def materialize(self, spec: AttemptSpec) -> MaterializedInput:
        requested = _validated_payload(spec)
        repos = _resolved_repos(spec, self._cfg)
        repo_targets = _targets_by_key(requested, repos)
        if not _covers_targets(repos, spec.target_commit, repo_targets):
            _sync_repos(repos)
            if not _covers_targets(repos, spec.target_commit, repo_targets):
                raise _input_error("configured 目标提交同步后仍不可用", retryable=True)
        return _snapshot(repos)

    def verify(
        self,
        spec: AttemptSpec,
        materialized: MaterializedInput,
    ) -> CanonicalJsonObject:
        if type(materialized) is not MaterializedInput:
            raise _input_error("configured materialized input 类型无效")
        requested = _validated_payload(spec)
        repos = _resolved_repos(spec, self._cfg)
        targets = _targets_by_key(requested, repos)
        if not _covers_targets(repos, spec.target_commit, targets):
            raise _input_error("configured 目标提交在执行后不可用", retryable=True)
        actual = _snapshot(repos)
        if actual != materialized:
            raise _input_error("configured 输入在 runner 执行期间发生漂移", retryable=True)
        return CanonicalJsonObject.from_value({
            "success": True,
            "kind": "configured",
            "project_id": spec.project_id,
            "root": actual.root,
            "commits": dict(actual.input_commits),
            "trees": dict(actual.input_trees),
        })


@dataclass(frozen=True)
class PreparedRepo:
    """legacy worker 已准备仓库的兼容视图。"""

    root: Path
    head: str | None
    pulled: bool | None
    note: str


@dataclass(frozen=True)
class PrepareResult:
    """legacy worker 仓库准备结果。"""

    can_run: bool
    retryable: bool
    note: str
    repos: tuple[PreparedRepo, ...] = ()


def _repo_for(cfg: dict, project_id: str) -> Path | None:
    projects = _cfg_get(cfg, "projects") or {}
    pc = projects.get(project_id)
    repo = pc.get("repo_path") if isinstance(pc, dict) else None
    if repo:
        path = Path(repo).expanduser()
        if path.exists():
            return path.resolve()

    try:
        for repo_spec in repo_catalog.project_repo_specs(project_id, cfg=cfg):
            if repo_spec.is_main and repo_spec.root.exists():
                return repo_spec.root.resolve()
    except Exception:
        return None
    return None


def prepare_project_repos(job: Job, repo: Path, cfg: dict) -> PrepareResult:
    """保留 legacy worker 的 pull policy 语义，隔离路径不调用本函数。"""
    policy = str(job.meta.pull_policy or "never").strip() or "never"
    specs = repo_catalog.project_repo_specs(job.project_id, main_repo=repo, cfg=cfg)
    if not specs:
        specs = [repo_catalog.RepoSpec(
            root=repo.resolve(),
            tag="",
            is_main=True,
            source_project_id=job.project_id,
        )]

    prepared: list[PreparedRepo] = []
    for repo_spec in specs:
        pulled = None
        note = "local only"
        if policy == "ff_only":
            sync = git_sync.sync_repo_to_remote(repo_spec.root)
            pulled = bool(sync.get("pulled"))
            note = str(sync.get("note") or "")
        elif policy != "never":
            return PrepareResult(False, False, f"unsupported pull_policy={policy}", tuple(prepared))
        prepared.append(PreparedRepo(
            root=repo_spec.root,
            head=git_sync.repo_head(repo_spec.root),
            pulled=pulled,
            note=note,
        ))

    target_commit = str(job.meta.target_commit or "").strip()
    covered = not target_commit or any(
        git_sync.repo_covers_commit(item.root, target_commit)
        for item in specs
    )
    if target_commit and not covered:
        return PrepareResult(
            False,
            policy == "ff_only",
            f"target commit not covered after prepare: {target_commit[:12]}",
            tuple(prepared),
        )
    detail = f"{policy} prepare ok"
    if target_commit:
        detail += f" target={target_commit[:12]}"
    return PrepareResult(True, False, detail, tuple(prepared))


__all__ = [
    "AttemptInputError",
    "AttemptInputStrategy",
    "ConfiguredAttemptInputStrategy",
    "MaterializedInput",
    "PrepareResult",
    "PreparedRepo",
    "freeze_attempt_input_strategies",
    "prepare_project_repos",
]
