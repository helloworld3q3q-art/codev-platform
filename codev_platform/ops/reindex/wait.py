"""等待后台重建索引完成的命令与辅助逻辑。"""
from __future__ import annotations

import argparse
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from codev_platform.core.wsl_data_owner import wsl_data_owner
from codev_platform.ops import _common as C
from codev_platform.reindex.index_status_client import (
    CoverageState,
    assess_index_coverage,
    read_platform_index_status,
)

from .dispatch import expected_reindex_jobs
from .logs import _git_out, _reindex_log, commit_changed_paths, integration_changed_paths


_FINISHED_RE = re.compile(r"reindex finished at .* \[(ok|warn exit=\d+|failed exit=\d+)\]")
_ENQUEUED_RE = re.compile(r"enqueued -> codev-reindex worker: ([^ ]+) -> (.+)")
_BLOCK_START_RE = re.compile(r"^===== reindex started at ")
_BLOCK_TIME_RE = re.compile(r"^===== reindex started at (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_BLOCK_EPOCH_RE = re.compile(r"^trigger epoch: (\d+(?:\.\d+)?)$")
_TRIGGER_LINE_RE = re.compile(r"^trigger (commit|merge/pull|checkout): ")


def _queued_expected_jobs(lines: list[str], start: int, end: int) -> list[tuple[str, str]]:
    jobs: list[tuple[str, str]] = []
    for ln in lines[start:end]:
        m = _ENQUEUED_RE.search(ln)
        if not m:
            continue
        pid = m.group(1).strip()
        for kind in (p.strip() for p in m.group(2).split(",")):
            if pid and kind:
                jobs.append((pid, kind))
    return jobs


def _reindex_block_end(lines: list[str], trigger_idx: int) -> int:
    for j in range(trigger_idx + 1, len(lines)):
        if _BLOCK_START_RE.search(lines[j]) or _TRIGGER_LINE_RE.search(lines[j]):
            return j
    return len(lines)


def _latest_trigger_index(lines: list[str], target_trigger: str) -> int:
    for i in range(len(lines) - 1, -1, -1):
        if target_trigger in lines[i]:
            return i
    return -1


def _trigger_started_at(lines: list[str], target_trigger: str) -> float | None:
    """Return the latest matching hook block's timezone-independent timestamp."""
    trigger_idx = _latest_trigger_index(lines, target_trigger)
    if trigger_idx < 0:
        return None
    for index in range(trigger_idx, -1, -1):
        epoch_match = _BLOCK_EPOCH_RE.match(lines[index])
        if epoch_match:
            return float(epoch_match.group(1))
        match = _BLOCK_TIME_RE.match(lines[index])
        if not match:
            continue
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return None
    return None


def _commit_covers(repo: Path, target: str, indexed: str | None) -> bool:
    if not indexed:
        return False
    indexed = indexed.strip()
    if indexed == target:
        return True
    rc, _out = _git_out(repo, "merge-base", "--is-ancestor", target, indexed)
    return rc == 0


def _queued_jobs_completed(repo: Path, expected: list[tuple[str, str]],
                           commit: str) -> tuple[bool, str]:
    if not expected:
        return False, ""
    try:
        from codev_platform.reindex import open_default_queue
        pending = {(j.project_id, j.kind) for j in open_default_queue().peek()}
    except Exception:  # noqa: BLE001 - 跨运行时不可读队列时仍以 manifest 证明为准
        pending = None
    if pending is not None and any(job in pending for job in expected):
        return False, ""

    from codev_platform.index_manifest import read_manifest, record_dependency_ok

    manifests = {}
    statuses: list[str] = []
    for pid, kind in expected:
        try:
            by_kind = manifests.get(pid)
            if by_kind is None:
                by_kind = {r.kind: r for r in read_manifest(pid)}
                manifests[pid] = by_kind
            rec = by_kind.get(kind)
        except Exception:  # noqa: BLE001 - 继续等待，日志轮询仍可能完成
            return False, ""
        if rec is None or not _commit_covers(repo, commit, rec.git_commit):
            return False, ""
        if rec.status == "failed":
            return True, "failed"
        if rec.status != "ok":
            return False, ""
        dep_ok, dep_reason = record_dependency_ok(rec, by_kind, repo=repo, target_commit=commit)
        if not dep_ok:
            if dep_reason.endswith(":stale"):
                return False, ""
            if dep_reason.endswith(":failed") or dep_reason.endswith(":missing") or dep_reason.endswith(":metadata-missing"):
                return True, f"failed:{kind}:{dep_reason}"
            return False, ""
        statuses.append(rec.status)
    if any(s == "failed" for s in statuses):
        return True, "failed"
    if any(s not in {"ok", "failed"} for s in statuses):
        return False, ""
    return True, "ok"


def _http_jobs_completed(
    repo: Path,
    expected: list[tuple[str, str]],
    commit: str,
    *,
    cfg: dict,
    retry_started_at: float | None = None,
) -> tuple[bool, str]:
    """Read WSL-owned manifests through HTTP without touching queue or SQLite paths."""
    if not expected:
        return False, ""
    by_project: dict[str, set[str]] = {}
    for project_id, kind in expected:
        by_project.setdefault(project_id, set()).add(kind)

    pending: list[str] = []
    failures: list[str] = []
    for project_id, kinds in sorted(by_project.items()):
        snapshot = read_platform_index_status(project_id, cfg)
        coverage = assess_index_coverage(
            snapshot,
            kinds,
            commit,
            commit_covers=lambda target, indexed: _commit_covers(
                repo,
                target,
                indexed,
            ),
            failure_not_before=retry_started_at,
        )
        if coverage.state is CoverageState.FAILED:
            failures.append(coverage.detail)
        elif coverage.state is CoverageState.PENDING:
            pending.append(coverage.detail)
    if failures:
        return True, "failed:" + "; ".join(failures)
    if pending:
        return False, "; ".join(pending)
    return True, "ok"


CompletionProbe = Callable[
    [Path, list[tuple[str, str]], str],
    tuple[bool, str],
]


def _timeout_guidance(*, remote_owner: bool) -> tuple[str, ...]:
    if remote_owner:
        return (
            "  This wait reads the platform Web control plane (default :18088), not the 19xxx MCP ports.",
            "  Verify `codev-web.service` in the WSL owner, then retry this HTTP wait later;",
            "  do not enqueue a duplicate while work is active.",
        )
    return (
        "  Check `codev-platform reindex-queue status` for pending/running jobs,",
        "  then inspect tools/chroma/reindex.log or worker logs if the queue is empty.",
        "  To retry through an installed relay after the queue is idle, run "
        "`git hook run post-commit`.",
    )


def _completed_status(
    repo: Path,
    lines: list[str],
    target_trigger: str,
    commit: str,
    *,
    expected_jobs: list[tuple[str, str]] | None = None,
    completion_probe: CompletionProbe | None = None,
    allow_legacy_fallback: bool = True,
) -> tuple[str, str] | None:
    trigger_idx = _latest_trigger_index(lines, target_trigger)
    jobs = list(expected_jobs or [])
    legacy_status: str | None = None
    if trigger_idx >= 0:
        block_end = _reindex_block_end(lines, trigger_idx)
        for k in range(trigger_idx, block_end):
            match = _FINISHED_RE.search(lines[k])
            if match:
                legacy_status = match.group(1)
        logged_jobs = _queued_expected_jobs(lines, trigger_idx, block_end)
        if logged_jobs:
            jobs = list(dict.fromkeys([*jobs, *logged_jobs]))
    probe = completion_probe or _queued_jobs_completed
    done, status = probe(
        repo,
        jobs,
        commit,
    )
    if done:
        return "reindex manifest covers", status
    if allow_legacy_fallback and not jobs and legacy_status is not None:
        if legacy_status != "ok":
            return "reindex finished for", f"failed:legacy:{legacy_status}"
        return "reindex finished for", legacy_status
    return None


def cmd_wait_for_reindex(args: argparse.Namespace) -> int:
    """轮询 manifest/兼容日志，直至目标提交对应的重建报告完成。

    返回码：找到结果或提交未触及索引文件时为 0，超时为 1，仓/提交不可解析为 2。
    """
    try:
        repo = C.resolve_repo(getattr(args, "repo", None))
    except RuntimeError:
        rc, top = _git_out(None, "rev-parse", "--show-toplevel")
        if rc != 0 or not top:
            C.out("[FAIL] not a git repo")
            return 2
        repo = Path(top).resolve()
    commit = args.commit
    if not commit:
        _rc, commit = _git_out(repo, "rev-parse", "HEAD")
    else:
        _rc, full_commit = _git_out(repo, "rev-parse", commit)
        if _rc == 0 and full_commit:
            commit = full_commit.strip()
    if not commit:
        C.out("[FAIL] unable to resolve target commit")
        return 2
    short = commit[: min(7, len(commit))]

    # 与 dispatch 共用同一份多项目 scope 计划。即使 Windows 读不到 WSL queue 或
    # 本地 reindex.log，webhook/WSL worker 写下的 manifest 仍可独立完成证明。
    expected_jobs: list[tuple[str, str]] | None = None
    _rc, changed = commit_changed_paths(repo, commit, git_out=_git_out)
    integration = integration_changed_paths(repo, commit, git_out=_git_out)
    if integration is not None:
        _rc, changed = integration
    if _rc == 0:
        try:
            expected_jobs = expected_reindex_jobs(repo, changed)
        except Exception:  # noqa: BLE001 - 兼容旧日志仍可提供精确 job 列表
            expected_jobs = None
        if expected_jobs == []:
            C.out(f"[OK] {short} touches no indexable file, skip wait")
            return 0
    log_file = _reindex_log(repo)
    timeout = args.timeout_sec
    cfg = C.config()
    owner = wsl_data_owner(cfg)
    completion_probe: CompletionProbe = _queued_jobs_completed
    if owner is not None:
        def completion_probe(
            selected_repo: Path,
            selected_jobs: list[tuple[str, str]],
            selected_commit: str,
        ) -> tuple[bool, str]:
            try:
                current_lines = log_file.read_text(
                    encoding="utf-8",
                    errors="replace",
                ).splitlines()
            except OSError:
                current_lines = []
            return _http_jobs_completed(
                selected_repo,
                selected_jobs,
                selected_commit,
                cfg=cfg,
                retry_started_at=_trigger_started_at(current_lines, target_trigger),
            )
    C.out(f"[INFO] waiting for reindex of {short} (timeout {timeout}s)")
    deadline = time.monotonic() + timeout
    target_trigger = f"trigger commit: {commit}"
    poll = 3
    last_probe_error: str | None = None
    while time.monotonic() < deadline:
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        try:
            completed = _completed_status(
                repo,
                lines,
                target_trigger,
                commit,
                expected_jobs=expected_jobs,
                completion_probe=completion_probe,
                allow_legacy_fallback=owner is None,
            )
        except Exception as exc:  # noqa: BLE001 - retry bounded HTTP probe, never local fallback
            last_probe_error = type(exc).__name__
            completed = None
        if completed:
            message_prefix, status = completed
            elapsed = int(timeout - (deadline - time.monotonic()))
            if status.startswith("failed"):
                C.out(f"[FAIL] {message_prefix} {short} status={status} (took ~{elapsed}s)")
                return 1
            C.out(f"[OK] {message_prefix} {short} status={status} (took ~{elapsed}s)")
            return 0
        time.sleep(poll)
    C.out(f"[TIMEOUT] reindex for {short} did not finish within {timeout}s")
    if last_probe_error is not None:
        C.out(f"  Platform index status probe unavailable: {last_probe_error}.")
    for line in _timeout_guidance(remote_owner=owner is not None):
        C.out(line)
    return 1
