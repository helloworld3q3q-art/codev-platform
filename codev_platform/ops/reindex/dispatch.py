"""reindex.dispatch -- scope classification + shared hook dispatch.

Pure-move split out of the original ops/reindex.py (no logic change).
"""
from __future__ import annotations

from pathlib import Path

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


def _dispatch_reindex(repo: Path, changed: list[str], *, foreground: bool,
                      trigger_line: str, banner: str) -> int:
    """共享: 按改动文件分 scope → 写 log header → spawn 对应 reindex + 健康快照刷新。

    post-commit / post-merge / post-checkout 三个 hook 复用本体, 只是 changed 算法 +
    trigger_line + banner 不同。无命中则静默 no-op。NEVER raise(hook 永不 fail)。
    """
    pid = C.project_id_of(repo)
    pats = C.reindex_patterns(C.meta_health(pid))
    scoped = classify_scopes(changed, pats)
    if not scoped:
        return 0  # silent no-op
    scopes = list(scoped)
    # 代码改动 (codegraph scope) → 顺带刷统一图谱 ingest (插件重跑落 store)。
    # ingest 失败隔离在 reindex --ingest 内, 不影响 codegraph 自身索引。
    if "codegraph" in scoped and "ingest" not in scopes:
        scopes.append("ingest")
    log_file = _reindex_log(repo)
    all_matched = sorted({p for paths in scoped.values() for p in paths})
    header = "\n".join(
        ["", f"===== reindex started at {_now()} =====", trigger_line,
         f"scopes:         {', '.join(scopes)}", "matched paths:"] + all_matched
    ) + "\n"
    _append_log(log_file, header)

    # 写侧走队列: hook 只 enqueue 即返回, 常驻 codev-reindex worker 串行消费 (合并/不丢尾/
    # 不并发写)。worker 跑完会刷 ai-health 快照, 故此处不再 spawn reindex / health。
    # scoped 的 key (chroma/graph/codegraph) 即 runner kind, 直接入队。
    from codev_platform.reindex import open_default_queue
    q = open_default_queue()
    for kind in scopes:
        q.enqueue(pid, kind)
    _append_log(log_file, f"enqueued -> codev-reindex worker: {pid} -> {', '.join(scopes)}\n")
    C.out(f"[{banner}] {'+'.join(scopes)} changed → 入队 (codev-reindex worker 串行消费)")

    if foreground:
        # 前台调用 (手动 / 调试): 当场串行 drain, 不依赖常驻 worker
        from codev_platform.core.config import load_config
        from codev_platform.reindex import ReindexWorker
        ReindexWorker(q, load_config()).drain_once()
    return 0
