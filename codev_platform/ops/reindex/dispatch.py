"""reindex.dispatch -- scope classification + shared hook dispatch.

Pure-move split out of the original ops/reindex.py (no logic change).
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.core.repos import impacted_project_ids_for_repo
from codev_platform.ops import _common as C
from codev_platform.reindex.maintenance_gate import (
    maintenance_reindex_operation_permit,
)
from codev_platform.reindex.queue import JobMeta

from .logs import _append_log, _now, _now_epoch, _reindex_log


def classify_scopes(changed: list[str], pats: dict) -> dict[str, list[str]]:
    """把改动文件按 doc/codegraph 两 scope 归类(纯函数, 可单测)。

    返回 {scope: [matched paths]}, 只含命中的 scope。空 dict = 无需 reindex。
    """
    buckets = {
        "chroma": [p for p in changed if C.matches_any(p, pats["doc"])],
        "codegraph": [p for p in changed if C.matches_any(p, pats["codegraph"])],
    }
    return {k: v for k, v in buckets.items() if v}


def _expand_scopes(scoped: dict[str, list[str]]) -> list[str]:
    scopes = list(scoped)
    # 代码改动 (codegraph scope) → 顺带刷统一图谱 ingest + 代码向量 lane (插件/嵌入重跑落库)。
    # 二者失败隔离在 reindex --ingest / --code-vec 内, 不影响 codegraph 自身索引。
    # append 在 codegraph 之后 → 串行 worker 保证读到新鲜 codegraph.db(code_vec 依赖它)。
    if "codegraph" in scoped:
        if "ingest" not in scopes:
            scopes.append("ingest")
        if "code_vec" not in scopes:
            scopes.append("code_vec")
    return scopes


def expand_reindex_scopes(scoped: dict[str, list[str]]) -> list[str]:
    """Return runner kinds for a classified reindex scope map."""
    return _expand_scopes(scoped)


def expected_reindex_kinds(changed: list[str], pats: dict) -> list[str]:
    """Return runner kinds required for the changed files.

    Shared by hook dispatch and health checks so their scope semantics do not drift.
    """
    return expand_reindex_scopes(classify_scopes(changed, pats))


def build_reindex_plan(
    repo: Path,
    changed: list[str],
    *,
    primary_project_id: str | None = None,
    cfg: dict | None = None,
) -> list[tuple[str, dict[str, list[str]], list[str]]]:
    """Build the shared per-project dispatch/wait plan for a source commit."""
    pid = C.project_id_of(repo) if primary_project_id is None else primary_project_id
    resolved_cfg = C.config() if cfg is None else cfg
    target_pids = impacted_project_ids_for_repo(
        repo,
        primary_project_id=pid,
        cfg=resolved_cfg,
    )
    plan: list[tuple[str, dict[str, list[str]], list[str]]] = []
    for target_pid in target_pids:
        pats = C.reindex_patterns(C.meta_health(target_pid))
        scoped = classify_scopes(changed, pats)
        if scoped:
            plan.append((target_pid, scoped, expand_reindex_scopes(scoped)))
    return plan


def expected_reindex_jobs(repo: Path, changed: list[str]) -> list[tuple[str, str]]:
    """Return manifest jobs expected for the changed source commit."""
    return [
        (project_id, kind)
        for project_id, _scoped, kinds in build_reindex_plan(repo, changed)
        for kind in kinds
    ]


def _safe_stage_error(stage: str, exc: BaseException) -> str:
    """Return actionable diagnostics without exception messages or paths."""
    parts = [f"stage={stage}", type(exc).__name__]
    for field in ("errno", "winerror"):
        value = getattr(exc, field, None)
        if isinstance(value, int):
            parts.append(f"{field}={value}")
    return " ".join(parts)


def _drain_foreground_queue(queue, *, banner: str) -> None:
    """在已取得维护许可后，运行一次前台队列消费。"""
    from codev_platform.core.config import load_config
    from codev_platform.reindex import supervisor
    from codev_platform.reindex.isolated_worker_composer import (
        build_isolated_worker,
        execution_mode,
    )

    owner_token = supervisor.new_owner_token()
    with supervisor.acquire_run_lock(owner_token) as acquired:
        if not acquired:
            C.out(f"[{banner}] reindex worker 已在运行, 跳过 foreground drain")
            return
        runtime_cfg = load_config()
        runtime_execution_mode = execution_mode(runtime_cfg)
        supervisor.record_worker_start(
            owner_token,
            mode="foreground",
            execution_mode=runtime_execution_mode,
        )
        try:
            if runtime_execution_mode == "isolated":
                build_isolated_worker(runtime_cfg, owner_token).loop.drain_once()
            else:
                from codev_platform.reindex import ReindexWorker

                ReindexWorker(queue, runtime_cfg).drain_once()
            supervisor.record_worker_exit(owner_token, "foreground")
        except Exception as exc:
            supervisor.record_worker_exit(owner_token, "error", str(exc)[:200])
            raise


def _dispatch_reindex(repo: Path, changed: list[str], *, foreground: bool,
                      trigger_line: str, banner: str) -> int:
    """共享: 按改动文件分 scope → 写 log header → spawn 对应 reindex + 健康快照刷新。

    post-commit / post-merge / post-checkout 三个 hook 复用本体, 只是 changed 算法 +
    trigger_line + banner 不同。无命中则静默 no-op。NEVER raise(hook 永不 fail)。
    """
    pid = C.project_id_of(repo)
    cfg = C.config()
    plan = build_reindex_plan(
        repo,
        changed,
        primary_project_id=pid,
        cfg=cfg,
    )
    if not plan:
        return 0  # silent no-op
    log_file = _reindex_log(repo)
    all_scopes = sorted({scope for _, _, scopes in plan for scope in scopes})
    all_matched = sorted({p for _, scoped, _ in plan for paths in scoped.values() for p in paths})
    header = "\n".join(
        ["", f"===== reindex started at {_now()} =====", f"trigger epoch: {_now_epoch()}", trigger_line,
         f"projects:       {', '.join(pid for pid, _, _ in plan)}",
         f"scopes:         {', '.join(all_scopes)}", "matched paths:"] + all_matched
    ) + "\n"
    _append_log(log_file, header)

    # 写侧走队列: hook 只 enqueue 即返回, 常驻 codev-reindex worker 串行消费 (合并/不丢尾/
    # 不并发写)。worker 跑完会刷 ai-health 快照, 故此处不再 spawn reindex / health。
    # 目标提交只能由发生变更的源仓解析一次，不能以 HEAD 别名延后到 worker 再解释。
    stage = "resolve-target"
    try:
        from codev_platform.reindex import open_default_queue
        from codev_platform.reindex.target_commit import resolve_repo_head

        target_commit = resolve_repo_head(repo)
        stage = "open-queue"
        q = open_default_queue(fail_soft=False)
        for target_pid, _, scopes in plan:
            meta = JobMeta(source="local_hook", pull_policy="never", target_commit=target_commit)
            for kind in scopes:
                stage = "enqueue"
                q.enqueue(target_pid, kind, meta=meta)
            _append_log(log_file, f"enqueued -> codev-reindex worker: {target_pid} -> {', '.join(scopes)}\n")
    except Exception as exc:  # noqa: BLE001 - hook 必须非阻断，但失败必须可观测
        detail = _safe_stage_error(stage, exc)
        _append_log(log_file, f"reindex enqueue failed (ignored): {detail}\n")
        C.err(f"[{banner}] reindex 入队失败（已忽略）: {detail}")
        return 0
    summary = "; ".join(f"{target_pid}:{'+'.join(scopes)}" for target_pid, _, scopes in plan)
    C.out(f"[{banner}] {summary} changed → 入队 (codev-reindex worker 串行消费)")

    if foreground:
        # 前台调用仅在整个 drain 期间持有维护许可；维护写侧会用独占锁等待该段退出。
        with maintenance_reindex_operation_permit() as permitted:
            if not permitted:
                C.err(f"[{banner}] reindex 维护窗口已启用，跳过 foreground drain")
                return 0
            _drain_foreground_queue(q, banner=banner)
    else:
        try:
            from codev_platform.reindex import supervisor
            if supervisor.should_auto_start(q, cfg):
                r = supervisor.ensure_worker_running(cfg, cwd=repo, queue=q)
                extra = f" pid={r.get('pid')}" if r.get("pid") else ""
                note = r.get("error") or ""
                _append_log(log_file, f"worker auto-start: {r.get('action')}{extra} {note}\n")
                if r.get("action") == "fail":
                    C.err(f"[{banner}] worker auto-start failed: {note}")
        except Exception as exc:  # noqa: BLE001 - hooks must never fail commit
            _append_log(log_file, f"worker auto-start failed (ignored): {exc!s}\n")
            C.err(f"[{banner}] worker auto-start failed (ignored): {exc!s}")
    return 0
