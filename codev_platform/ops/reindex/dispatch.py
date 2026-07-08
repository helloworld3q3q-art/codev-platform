"""reindex.dispatch -- scope classification + shared hook dispatch.

Pure-move split out of the original ops/reindex.py (no logic change).
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.core.repos import impacted_project_ids_for_repo
from codev_platform.ops import _common as C

from .logs import _append_log, _now, _reindex_log


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


def _dispatch_reindex(repo: Path, changed: list[str], *, foreground: bool,
                      trigger_line: str, banner: str) -> int:
    """共享: 按改动文件分 scope → 写 log header → spawn 对应 reindex + 健康快照刷新。

    post-commit / post-merge / post-checkout 三个 hook 复用本体, 只是 changed 算法 +
    trigger_line + banner 不同。无命中则静默 no-op。NEVER raise(hook 永不 fail)。
    """
    pid = C.project_id_of(repo)
    cfg = C.config()
    target_pids = impacted_project_ids_for_repo(repo, primary_project_id=pid, cfg=cfg)
    plan: list[tuple[str, dict[str, list[str]], list[str]]] = []
    for target_pid in target_pids:
        pats = C.reindex_patterns(C.meta_health(target_pid))
        scoped = classify_scopes(changed, pats)
        if scoped:
            plan.append((target_pid, scoped, expand_reindex_scopes(scoped)))
    if not plan:
        return 0  # silent no-op
    log_file = _reindex_log(repo)
    all_scopes = sorted({scope for _, _, scopes in plan for scope in scopes})
    all_matched = sorted({p for _, scoped, _ in plan for paths in scoped.values() for p in paths})
    header = "\n".join(
        ["", f"===== reindex started at {_now()} =====", trigger_line,
         f"projects:       {', '.join(pid for pid, _, _ in plan)}",
         f"scopes:         {', '.join(all_scopes)}", "matched paths:"] + all_matched
    ) + "\n"
    _append_log(log_file, header)

    # 写侧走队列: hook 只 enqueue 即返回, 常驻 codev-reindex worker 串行消费 (合并/不丢尾/
    # 不并发写)。worker 跑完会刷 ai-health 快照, 故此处不再 spawn reindex / health。
    # scoped 的 key (chroma/graph/codegraph) 即 runner kind, 直接入队。
    from codev_platform.reindex import open_default_queue
    q = open_default_queue()
    for target_pid, _, scopes in plan:
        for kind in scopes:
            q.enqueue(target_pid, kind)
        _append_log(log_file, f"enqueued -> codev-reindex worker: {target_pid} -> {', '.join(scopes)}\n")
    summary = "; ".join(f"{target_pid}:{'+'.join(scopes)}" for target_pid, _, scopes in plan)
    C.out(f"[{banner}] {summary} changed → 入队 (codev-reindex worker 串行消费)")

    if foreground:
        # 前台调用 (手动 / 调试): 当场串行 drain, 不依赖常驻 worker
        from codev_platform.core.config import load_config
        from codev_platform.reindex import ReindexWorker
        from codev_platform.reindex import supervisor
        owner_token = supervisor.new_owner_token()
        with supervisor.acquire_run_lock(owner_token) as acquired:
            if not acquired:
                C.out(f"[{banner}] reindex worker 已在运行, 跳过 foreground drain")
            else:
                supervisor.record_worker_start(owner_token, mode="foreground")
                try:
                    ReindexWorker(q, load_config()).drain_once()
                    supervisor.record_worker_exit(owner_token, "foreground")
                except Exception as exc:
                    supervisor.record_worker_exit(owner_token, "error", str(exc)[:200])
                    raise
    else:
        try:
            from codev_platform.reindex import supervisor
            if supervisor.should_auto_start(q, cfg):
                r = supervisor.ensure_worker_running(cfg, cwd=repo)
                extra = f" pid={r.get('pid')}" if r.get("pid") else ""
                note = r.get("error") or ""
                _append_log(log_file, f"worker auto-start: {r.get('action')}{extra} {note}\n")
                if r.get("action") == "fail":
                    C.err(f"[{banner}] worker auto-start failed: {note}")
        except Exception as exc:  # noqa: BLE001 - hooks must never fail commit
            _append_log(log_file, f"worker auto-start failed (ignored): {exc!s}\n")
            C.err(f"[{banner}] worker auto-start failed (ignored): {exc!s}")
    return 0
