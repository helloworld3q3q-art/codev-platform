"""reindex.commands -- the 5 CLI subcommand handlers + argparse registration.

Pure-move split out of the original ops/reindex.py (no logic change).
"""
from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

from codev_platform.ops import _common as C

from .dispatch import _dispatch_reindex
from .logs import _git_out, _reindex_log


# ======================================================================
# 1. reindex  (port of update-local-ai.ps1)
# ======================================================================
def cmd_reindex(args: argparse.Namespace) -> int:
    """Refresh local AI indexes. Default = run all stages.

    --chroma / --codegraph select a subset (if ANY is given, only
    the named stages run). --force passes --force to the chroma indexer.

    Exit code: 0 success / non-zero on first stage failure (mirrors the .ps1).
    """
    try:
        repo = C.resolve_repo(getattr(args, "repo", None))
    except RuntimeError as exc:
        C.err(str(exc))
        return 1

    do_ingest_flag = getattr(args, "ingest", False)
    selected = bool(args.chroma or args.codegraph or do_ingest_flag)
    do_codegraph = args.codegraph if selected else True
    do_chroma = args.chroma if selected else True
    do_ingest = do_ingest_flag if selected else True

    if args.force or (do_codegraph and do_chroma and not selected):
        C.out("[reindex] full rebuild: run in foreground to watch progress "
              "(post-commit handles incremental in background)")

    started = time.monotonic()

    # --- stage 1/3: codegraph sync ---
    if do_codegraph:
        C.out("")
        C.out("=== step 1/3: codegraph sync ===")
        try:
            cp = C.run(["codegraph", "sync"], cwd=str(repo))
            rc = cp.returncode
        except FileNotFoundError:
            C.out("SKIP: 'codegraph' CLI not found on PATH; continuing other indexes")
            rc = 0
        if rc == 2:
            # 2 = MCP holds the db lock; non-fatal (mirrors update-local-ai.ps1)
            C.out("WARN: codegraph sync skipped (MCP holds DB); continuing other indexes")
        elif rc != 0:
            C.err(f"FAIL: codegraph sync exit={rc}")
            return rc
    else:
        C.out("step 1/3: codegraph sync   -- skipped")

    # --- stage 2/3: chroma reindex ---
    if do_chroma:
        C.out("")
        C.out("=== step 2/3: chroma reindex ===")
        chroma_py = C.chroma_python()
        if not chroma_py or not Path(chroma_py).exists():
            C.err(f"FAIL: chroma python not found ({chroma_py}); set runtime.chroma_venv in config")
            return 1
        env = dict(os.environ)
        env["PLATFORM_ROOT"] = str(repo)
        cmd = [str(chroma_py), "-m", "codev_platform.chroma.indexer"]
        if args.force:
            cmd.append("--force")
        rc = C.run(cmd, env=env).returncode
        if rc != 0:
            C.err(f"FAIL: chroma reindex exit={rc}")
            return rc
    else:
        C.out("step 2/3: chroma reindex   -- skipped")

    # --- stage 3/3: unified graph ingest (plugins -> graph store) ---
    # 跑所有适用 analyzer 插件, 把产出灌进 per-project 统一图谱 store。与上面三个
    # stage 并列, 但 FAILURE-ISOLATED: 这是 Phase 3 聚合层, 任何异常只 warn 不
    # 改 reindex 退出码 —— 绝不让插件层拖垮已稳定的 codegraph/chroma 基线。
    if do_ingest:
        C.out("")
        C.out("=== step 3/3: unified graph ingest ===")
        pid = C.project_id_of(repo)
        if not pid:
            C.out("step 3/3: graph ingest     -- skipped (repo 无 .claude/project.json project_id)")
        else:
            try:
                from codev_platform.graph.ingest import ingest_project
                rep = ingest_project(repo, pid)
                if rep.ingested:
                    C.out(f"graph ingest ok: {len(rep.ingested)} plugin(s) -> store "
                          f"[{', '.join(rep.ingested)}]")
                else:
                    C.out("graph ingest ok: no applicable plugin produced output")
            except Exception as exc:  # noqa: BLE001 — 聚合层失败隔离, 不污染基线退出码
                C.err(f"WARN: graph ingest failed (non-fatal, baseline indexes unaffected): {exc}")
    else:
        C.out("step 3/3: graph ingest     -- skipped")

    dur = int(time.monotonic() - started)
    C.out("")
    C.out(f"total: {dur}s, reindex ok")
    return 0


# ======================================================================
# 2. post-commit  (port of post-commit.ps1)
# ======================================================================
def cmd_post_commit(args: argparse.Namespace) -> int:
    """Git post-commit hook: diff 刚做的 commit → 按 scope reindex。NEVER fail commit。"""
    try:
        rc, top = _git_out(None, "rev-parse", "--show-toplevel")
        if rc != 0 or not top:
            return 0
        repo = Path(top).resolve()
        rc, changed_raw = _git_out(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
        if rc != 0 or not changed_raw:
            return 0
        changed = [ln.strip() for ln in changed_raw.splitlines() if ln.strip()]
        _rc, commit_sha = _git_out(repo, "rev-parse", "HEAD")
        # "trigger commit: <sha>" 格式被 wait-for-reindex grep, 不要改
        return _dispatch_reindex(repo, changed, foreground=args.foreground,
                                 trigger_line=f"trigger commit: {commit_sha}", banner="post-commit")
    except Exception as exc:  # never fail the commit
        C.err(f"[post-commit] hook error (commit succeeded): {exc}")
        return 0


def cmd_post_merge(args: argparse.Namespace) -> int:
    """Git post-merge hook(pull / merge 后): diff ORIG_HEAD..HEAD → reindex 拉进来的改动。

    本地索引在"获取别人代码"后也更新, 不只自己 commit 时。NEVER fail。
    """
    try:
        rc, top = _git_out(None, "rev-parse", "--show-toplevel")
        if rc != 0 or not top:
            return 0
        repo = Path(top).resolve()
        # ORIG_HEAD = merge/pull 前的 HEAD(git 自动设); 无则首次/无可比, 静默退出
        rc, _ = _git_out(repo, "rev-parse", "--verify", "ORIG_HEAD")
        if rc != 0:
            return 0
        rc, changed_raw = _git_out(repo, "diff", "--name-only", "ORIG_HEAD", "HEAD")
        if rc != 0 or not changed_raw:
            return 0
        changed = [ln.strip() for ln in changed_raw.splitlines() if ln.strip()]
        return _dispatch_reindex(repo, changed, foreground=args.foreground,
                                 trigger_line="trigger merge/pull: ORIG_HEAD..HEAD", banner="post-merge")
    except Exception as exc:
        C.err(f"[post-merge] hook error: {exc}")
        return 0


def cmd_post_checkout(args: argparse.Namespace) -> int:
    """Git post-checkout hook(切分支后): diff prev..new → reindex。

    git 传 3 个位置参: prev_head new_head branch_flag(1=切分支 / 0=单文件 checkout)。
    只在切分支(flag=1)时 reindex; 单文件 checkout 跳过。NEVER fail。
    """
    try:
        prev, new, flag = args.prev, args.new, args.flag
        if flag != "1":
            return 0  # 单文件 checkout, 非切分支, 跳过
        if not prev or not new or prev == new:
            return 0
        rc, top = _git_out(None, "rev-parse", "--show-toplevel")
        if rc != 0 or not top:
            return 0
        repo = Path(top).resolve()
        rc, changed_raw = _git_out(repo, "diff", "--name-only", prev, new)
        if rc != 0 or not changed_raw:
            return 0
        changed = [ln.strip() for ln in changed_raw.splitlines() if ln.strip()]
        return _dispatch_reindex(repo, changed, foreground=args.foreground,
                                 trigger_line=f"trigger checkout: {prev[:7]}..{new[:7]}", banner="post-checkout")
    except Exception as exc:
        C.err(f"[post-checkout] hook error: {exc}")
        return 0


# ======================================================================
# 3. dirty-check  (port of dirty-index-check.ps1)
# ======================================================================
def cmd_dirty_check(args: argparse.Namespace) -> int:
    """Report whether the working tree has dirty files in the AI index scope.

    Exit code: 0 clean / 1 dirty (files in scope) / 2 not a git repo
    (matches dirty-index-check.ps1).
    """
    import json

    rc, top = _git_out(None, "rev-parse", "--show-toplevel")
    if rc != 0 or not top:
        if not args.json and not args.quiet:
            C.err("not a git repo")
        return 2
    repo = Path(top).resolve()

    rc, status = _git_out(repo, "status", "--porcelain")
    if not status:
        if args.json:
            C.out('{"dirty": false, "affected": {}}')
            return 0
        if not args.quiet:
            C.out("working tree clean")
        return 0

    dirty_paths: list[str] = []
    for line in status.splitlines():
        if len(line) < 3:
            continue
        path = line[3:].strip()
        if " -> " in path:  # rename: take the new name
            path = path.split(" -> ")[-1]
        path = path.strip('"').replace("\\", "/")
        if path:
            dirty_paths.append(path)

    pid = C.project_id_of(repo)
    health = C.meta_health(pid)
    pats = C.reindex_patterns(health)

    affected_cg = [p for p in dirty_paths if C.matches_any(p, pats["codegraph"])]
    affected_ch = [p for p in dirty_paths if C.matches_any(p, pats["doc"])]

    total = sorted(set(affected_cg + affected_ch))
    dirty = len(total) > 0

    if args.json:
        payload = {
            "dirty": dirty,
            "affected": {
                "codegraph": affected_cg,
                "chroma": affected_ch,
            },
            "total_dirty_files": len(dirty_paths),
            "recommendation": (
                "allow grep/read fallback; run reindex before deciding"
                if dirty
                else "MCP indexes are fresh; default to MCP tools"
            ),
        }
        C.out(json.dumps(payload, ensure_ascii=False))
        return 1 if dirty else 0

    if args.quiet:
        return 1 if dirty else 0

    # Human-readable
    C.out("")
    if not dirty:
        C.out("no dirty files in AI index scope")
        C.out(f"working tree has {len(dirty_paths)} dirty file(s) but none in "
              "CodeGraph/Chroma scope")
        return 0

    C.out("WARN: dirty files in AI index scope - MCP results may be STALE")
    C.out("")
    if affected_cg:
        C.out(f"[CodeGraph  ] {len(affected_cg)} file(s):")
        for p in affected_cg:
            C.out(f"  {p}")
    if affected_ch:
        C.out(f"[Chroma     ] {len(affected_ch)} file(s):")
        for p in affected_ch:
            C.out(f"  {p}")
    C.out("")
    C.out("Next steps:")
    C.out("  1. allow grep/read fallback for affected files")
    C.out("  2. or commit + let post-commit hook reindex")
    C.out("  3. or run `codev-platform reindex` manually")
    return 1


# ======================================================================
# 4. wait-for-reindex  (port of wait-for-reindex.ps1)
# ======================================================================
_FINISHED_RE = re.compile(r"reindex finished at .* \[(ok|warn exit=\d+|failed exit=\d+)\]")


def cmd_wait_for_reindex(args: argparse.Namespace) -> int:
    """Poll reindex.log until the target commit's reindex block reports finished.

    Exit code: 0 found (or commit touches no indexable file) / 1 timeout /
    2 log missing (matches wait-for-reindex.ps1).
    """
    try:
        repo = C.resolve_repo(getattr(args, "repo", None))
    except RuntimeError:
        rc, top = _git_out(None, "rev-parse", "--show-toplevel")
        if rc != 0 or not top:
            C.out("[FAIL] not a git repo")
            return 2
        repo = Path(top).resolve()

    log_file = _reindex_log(repo)
    if not log_file.exists():
        C.out(f"[FAIL] reindex.log not found at {log_file}")
        return 2

    commit = args.commit
    if not commit:
        _rc, commit = _git_out(repo, "rev-parse", "HEAD")
    short = commit[: min(7, len(commit))]

    # Pre-check: if this commit touches NO indexable file, post-commit skips
    # reindex (no trigger line will ever appear) -> exit 0 early.
    _rc, changed_raw = _git_out(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
    if changed_raw:
        changed = [ln.strip() for ln in changed_raw.splitlines() if ln.strip()]
        pid = C.project_id_of(repo)
        pats = C.reindex_patterns(C.meta_health(pid))
        all_pats = pats["doc"] + pats["codegraph"]
        has_indexable = any(C.matches_any(p, all_pats) for p in changed)
        if not has_indexable:
            C.out(f"[OK] {short} touches no indexable file, skip wait")
            return 0

    timeout = args.timeout_sec
    C.out(f"[INFO] waiting for reindex of {short} (timeout {timeout}s)")
    deadline = time.monotonic() + timeout
    target_trigger = f"trigger commit: {commit}"
    poll = 3

    while time.monotonic() < deadline:
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        if lines:
            trigger_idx = -1
            for i, ln in enumerate(lines):
                if target_trigger in ln:
                    trigger_idx = i
                    break
            if trigger_idx >= 0:
                block_end = len(lines)
                for j in range(trigger_idx + 1, len(lines)):
                    if "trigger commit: " in lines[j]:
                        block_end = j
                        break
                for k in range(trigger_idx, block_end):
                    m = _FINISHED_RE.search(lines[k])
                    if m:
                        elapsed = int(timeout - (deadline - time.monotonic()))
                        C.out(f"[OK] reindex finished for {short} status={m.group(1)} "
                              f"(took ~{elapsed}s)")
                        return 0
        time.sleep(poll)

    C.out(f"[TIMEOUT] reindex for {short} did not finish within {timeout}s")
    C.out("  Check tools/chroma/reindex.log tail for errors,")
    C.out("  or run `codev-platform post-commit` manually to retry.")
    return 1


# ======================================================================
# register
# ======================================================================
def register(subparsers) -> None:
    sp = subparsers.add_parser(
        "reindex",
        help="刷新本地 AI 索引 (chroma / codegraph; 默认全跑)",
    )
    sp.add_argument("--repo", default=None, help="目标仓 (默认 git rev-parse 当前仓)")
    sp.add_argument("--chroma", action="store_true", help="只跑 chroma (与其它 flag 组合则只跑选中的)")
    sp.add_argument("--codegraph", action="store_true", help="只跑 codegraph sync")
    sp.add_argument("--ingest", action="store_true", help="只跑统一图谱 ingest (plugins -> graph store)")
    sp.add_argument("--force", action="store_true", help="chroma indexer 传 --force (drop + rebuild)")
    sp.set_defaults(func=cmd_reindex)

    pc = subparsers.add_parser(
        "post-commit",
        help="git post-commit hook: 按改动 scope 触发 reindex (永不 fail commit)",
    )
    pc.add_argument("--foreground", action="store_true", help="前台跑 (默认后台 detached)")
    pc.set_defaults(func=cmd_post_commit)

    pm = subparsers.add_parser(
        "post-merge",
        help="git post-merge hook: pull/merge 后 diff ORIG_HEAD..HEAD 触发 reindex",
    )
    pm.add_argument("--foreground", action="store_true", help="前台跑 (默认后台 detached)")
    pm.add_argument("ignored", nargs="*", help="git 传的 is-squash 参数, 忽略")
    pm.set_defaults(func=cmd_post_merge)

    po = subparsers.add_parser(
        "post-checkout",
        help="git post-checkout hook: 切分支后 diff prev..new 触发 reindex",
    )
    po.add_argument("prev", nargs="?", default="", help="git 传: 切换前 HEAD")
    po.add_argument("new", nargs="?", default="", help="git 传: 切换后 HEAD")
    po.add_argument("flag", nargs="?", default="", help="git 传: 1=切分支 / 0=单文件 checkout")
    po.add_argument("--foreground", action="store_true", help="前台跑 (默认后台 detached)")
    po.set_defaults(func=cmd_post_checkout)

    dc = subparsers.add_parser(
        "dirty-check",
        help="检查工作树是否有 dirty 文件命中 AI 索引范围 (0 clean / 1 dirty / 2 非 git)",
    )
    dc.add_argument("--json", action="store_true", help="输出 JSON 给 AI / 工具")
    dc.add_argument("--quiet", action="store_true", help="只返回 exit code")
    dc.set_defaults(func=cmd_dirty_check)

    wr = subparsers.add_parser(
        "wait-for-reindex",
        help="轮询 reindex.log 直到目标 commit reindex 完成 (0 done / 1 timeout / 2 无 log)",
    )
    wr.add_argument("--repo", default=None, help="目标仓 (默认 git rev-parse 当前仓)")
    wr.add_argument("--commit", default=None, help="目标 commit (默认 HEAD)")
    wr.add_argument("--timeout-sec", type=int, default=120, dest="timeout_sec", help="超时秒数 (默认 120)")
    wr.set_defaults(func=cmd_wait_for_reindex)
