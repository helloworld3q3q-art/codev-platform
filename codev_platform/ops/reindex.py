"""codev_platform.ops.reindex -- cross-platform port of the 4 reindex .ps1 scripts.

Ports (1:1 behavior + exit codes) the Windows-only PowerShell layer into pure
Python CLI subcommands so the same logic runs on Windows / Linux / macOS:

  scripts/update-local-ai.ps1   -> ``reindex``           full / scoped index refresh
  scripts/post-commit.ps1       -> ``post-commit``       git hook: scope-diff + spawn
  scripts/dirty-index-check.ps1 -> ``dirty-check``       dirty work-tree vs index scope
  scripts/wait-for-reindex.ps1  -> ``wait-for-reindex``  block until bg reindex done

All machine paths come from codev_platform.ops._common (config-driven, no
hardcoded paths). Subprocess launching tolerates Windows .cmd/.ps1 launchers and
runs real executables directly elsewhere (see _common.run / resolve_argv).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from codev_platform.ops import _common as C


# ----------------------------------------------------------------------
# shared: per-repo reindex.log location (mirrors post-commit.ps1)
# ----------------------------------------------------------------------
def _reindex_log(repo: Path) -> Path:
    return repo / "tools" / "chroma" / "reindex.log"


def _append_log(path: Path, text: str) -> None:
    """Append UTF-8 (no BOM) -- mirrors the .NET AppendAllText in post-commit.ps1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ======================================================================
# 1. reindex  (port of update-local-ai.ps1)
# ======================================================================
def cmd_reindex(args: argparse.Namespace) -> int:
    """Refresh local AI indexes. Default = run all three stages.

    --chroma / --codegraph / --cross-link select a subset (if ANY is given, only
    the named stages run). --force passes --force to the chroma indexer.

    Exit code: 0 success / non-zero on first stage failure (mirrors the .ps1).
    """
    try:
        repo = C.resolve_repo(getattr(args, "repo", None))
    except RuntimeError as exc:
        C.err(str(exc))
        return 1

    do_ingest_flag = getattr(args, "ingest", False)
    selected = bool(args.chroma or args.codegraph or args.cross_link or do_ingest_flag)
    do_codegraph = args.codegraph if selected else True
    do_chroma = args.chroma if selected else True
    do_cross_link = args.cross_link if selected else True
    do_ingest = do_ingest_flag if selected else True

    if args.force or (do_codegraph and do_chroma and do_cross_link and not selected):
        C.out("[reindex] full rebuild: run in foreground to watch progress "
              "(post-commit handles incremental in background)")

    started = time.monotonic()

    # --- stage 1/3: codegraph sync ---
    if do_codegraph:
        C.out("")
        C.out("=== step 1/4: codegraph sync ===")
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
        C.out("step 1/4: codegraph sync   -- skipped")

    # --- stage 2/3: chroma reindex ---
    if do_chroma:
        C.out("")
        C.out("=== step 2/4: chroma reindex ===")
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
        C.out("step 2/4: chroma reindex   -- skipped")

    # --- stage 3/3: cross-layer KG rebuild ---
    if do_cross_link:
        C.out("")
        C.out("=== step 3/4: cross-layer KG rebuild ===")
        # The cross_link build scanners (build_index + scan_*) are a BUSINESS-repo
        # asset, not part of the codev_platform package. Projects without them
        # (e.g. codev-platform itself) must skip — NOT scan some other repo and
        # pollute their own DB. Gate on the target repo actually shipping them.
        builder = repo / "tools" / "cross_link" / "build_index.py"
        if not builder.is_file():
            C.out(f"step 3/4: cross-layer KG   -- skipped "
                  f"(no {builder.relative_to(repo)} in this repo)")
        else:
            cross_py = C.cross_link_python()
            env = dict(os.environ)
            # cross_link package lives under <repo>/tools (mirrors update-local-ai.ps1)
            env["PYTHONPATH"] = str(repo / "tools")
            env["PYTHONIOENCODING"] = "utf-8"
            # Pin scan-root AND write-path to the SAME project so we never
            # "scan repo A, write DB B" (cross-tenant pollution). The builder's
            # DB_PATH is resolved from PLATFORM_PROJECT_ID; its scan-root
            # (schema.REPO_ROOT) now honors CROSS_LINK_REPO_ROOT. Pinning both to
            # this repo keeps them in lockstep regardless of cwd / __file__.
            pid = C.project_id_of(repo)
            if pid:
                env["PLATFORM_PROJECT_ID"] = pid
            env["CROSS_LINK_REPO_ROOT"] = str(repo)
            try:
                rc = C.run([cross_py, "-m", "cross_link.build_index"],
                           env=env, cwd=str(repo)).returncode
            except FileNotFoundError:
                C.err(f"FAIL: cross_link python not found ({cross_py}); set runtime.cross_link_python")
                return 1
            if rc != 0:
                C.err(f"FAIL: cross_link build exit={rc}")
                return rc
    else:
        C.out("step 3/4: cross-layer KG   -- skipped")

    # --- stage 4/4: unified graph ingest (plugins -> graph store) ---
    # 跑所有适用 analyzer 插件, 把产出灌进 per-project 统一图谱 store。与上面三个
    # stage 并列, 但 FAILURE-ISOLATED: 这是 Phase 3 聚合层, 任何异常只 warn 不
    # 改 reindex 退出码 —— 绝不让插件层拖垮已稳定的 codegraph/chroma/cross-link 基线。
    if do_ingest:
        C.out("")
        C.out("=== step 4/4: unified graph ingest ===")
        pid = C.project_id_of(repo)
        if not pid:
            C.out("step 4/4: graph ingest     -- skipped (repo 无 .claude/project.json project_id)")
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
        C.out("step 4/4: graph ingest     -- skipped")

    dur = int(time.monotonic() - started)
    C.out("")
    C.out(f"total: {dur}s, reindex ok")
    return 0


# ======================================================================
# 2. post-commit  (port of post-commit.ps1)
# ======================================================================
def _git_out(repo: Path | None, *git_args: str) -> tuple[int, str]:
    cmd = ["git"]
    if repo is not None:
        cmd += ["-C", str(repo)]
    cmd += list(git_args)
    try:
        # Windows 下 text=True 不指定 encoding 会按 GBK 解码 git 输出,撞中文 commit/diff
        # 内容 → UnicodeDecodeError 崩 reader 线程 → 静默跳过 reindex(2026-06-02 修)
        cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 127, ""
    return cp.returncode, cp.stdout.strip()


def classify_scopes(changed: list[str], pats: dict) -> dict[str, list[str]]:
    """把改动文件按 doc/cross_link/codegraph 三 scope 归类(纯函数, 可单测)。

    返回 {scope: [matched paths]}, 只含命中的 scope。空 dict = 无需 reindex。
    """
    buckets = {
        "chroma": [p for p in changed if C.matches_any(p, pats["doc"])],
        "cross_link": [p for p in changed if C.matches_any(p, pats["cross_link"])],
        "codegraph": [p for p in changed if C.matches_any(p, pats["codegraph"])],
    }
    return {k: v for k, v in buckets.items() if v}


# A3 (parity 达标后退役): cross_link/cross_layer 已被统一图谱 store 完全取代
# (store 每项覆盖 ≥ cross_layer, 见 tools/audit_graph_parity.py), 不再**自动**重建。
# classify_scopes 仍诚实分类 (.sql/.xml 确与 cross-link 相关, 供 dirty 显示), 但自动入队
# 层滤掉它; 手动 `reindex --cross-link` (do_cross_link 路径) + runner 保留, 可按需重建/回退。
_AUTO_REINDEX_RETIRED = frozenset({"cross_link"})


def auto_reindex_kinds(scoped: dict) -> list[str]:
    """从 classify_scopes 结果取真正要自动入队的 runner kind, 滤掉已退役 scope (A3)。"""
    return [k for k in scoped if k not in _AUTO_REINDEX_RETIRED]


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
    scopes = auto_reindex_kinds(scoped)  # A3: 滤掉已退役 cross_link, 不自动重建 cross_layer
    if not scopes:
        return 0  # 仅命中退役 scope (cross_link) → 静默 no-op
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
    # scoped 的 key (chroma/cross_link/codegraph) 即 runner kind, 直接入队。
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


def _status_label(rc: int) -> str:
    if rc == 0:
        return "ok"
    if rc == 2:
        return "warn exit=2"
    return f"failed exit={rc}"


def _finish_log(log_file: Path, rc: int) -> None:
    _append_log(log_file, f"===== reindex finished at {_now()} [{_status_label(rc)}] =====\n")


def _run_logged_foreground(cmd: list[str], log_file: Path) -> int:
    """Run reindex inline, appending stdout+stderr to the log (UTF-8)."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as fh:
        cp = subprocess.run(C.resolve_argv(cmd), stdout=fh, stderr=subprocess.STDOUT)
    return cp.returncode


def _run_health_refresh(cmd: list[str], log_file: Path) -> None:
    """Refresh the widget health snapshot, best-effort (never raises)."""
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as fh:
            subprocess.run(C.resolve_argv(cmd), stdout=fh, stderr=subprocess.STDOUT)
    except Exception as exc:  # noqa: BLE001 - snapshot refresh must never fail the hook
        _append_log(log_file, f"health snapshot refresh exception: {exc}\n")


def _spawn_background(cmd: list[str], log_file: Path, post_cmd: list[str] | None = None) -> None:
    """Spawn reindex detached, redirecting output to the log + writing the
    finish marker afterwards. Uses a tiny Python wrapper so the finish status
    is recorded cross-platform (replaces the .ps1 temp-wrapper trick)."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    py = sys.executable or "python"
    inner = repr(C.resolve_argv(cmd))
    post = repr(C.resolve_argv(post_cmd) if post_cmd else None)
    logf = repr(str(log_file))
    wrapper = (
        "import subprocess,sys\n"
        f"_cmd={inner}\n"
        f"_post={post}\n"
        f"_log={logf}\n"
        "import datetime\n"
        "def _stamp(): return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')\n"
        "rc=-1\n"
        "try:\n"
        "    with open(_log,'a',encoding='utf-8') as fh:\n"
        "        rc=subprocess.run(_cmd,stdout=fh,stderr=subprocess.STDOUT).returncode\n"
        "except Exception as e:\n"
        "    with open(_log,'a',encoding='utf-8') as fh:\n"
        "        fh.write('reindex wrapper exception: '+str(e)+chr(10))\n"
        "lbl='ok' if rc==0 else ('warn exit=2' if rc==2 else 'failed exit='+str(rc))\n"
        "with open(_log,'a',encoding='utf-8') as fh:\n"
        "    fh.write('===== reindex finished at '+_stamp()+' ['+lbl+'] ====='+chr(10))\n"
        "if _post:\n"
        "    try:\n"
        "        with open(_log,'a',encoding='utf-8') as fh:\n"
        "            subprocess.run(_post,stdout=fh,stderr=subprocess.STDOUT)\n"
        "    except Exception as e:\n"
        "        with open(_log,'a',encoding='utf-8') as fh:\n"
        "            fh.write('health snapshot refresh exception: '+str(e)+chr(10))\n"
    )
    kwargs: dict = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NO_WINDOW
        kwargs["creationflags"] = 0x00000008 | 0x08000000
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [py, "-c", wrapper],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


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
    affected_cl = [p for p in dirty_paths if C.matches_any(p, pats["cross_link"])]
    affected_ch = [p for p in dirty_paths if C.matches_any(p, pats["doc"])]

    total = sorted(set(affected_cg + affected_cl + affected_ch))
    dirty = len(total) > 0

    if args.json:
        payload = {
            "dirty": dirty,
            "affected": {
                "codegraph": affected_cg,
                "cross_link": affected_cl,
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
              "CodeGraph/cross-link/Chroma scope")
        return 0

    C.out("WARN: dirty files in AI index scope - MCP results may be STALE")
    C.out("")
    if affected_cg:
        C.out(f"[CodeGraph  ] {len(affected_cg)} file(s):")
        for p in affected_cg:
            C.out(f"  {p}")
    if affected_cl:
        C.out(f"[cross-link ] {len(affected_cl)} file(s):")
        for p in affected_cl:
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
        all_pats = pats["doc"] + pats["cross_link"] + pats["codegraph"]
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
        help="刷新本地 AI 索引 (chroma / codegraph / cross-link; 默认全跑)",
    )
    sp.add_argument("--repo", default=None, help="目标仓 (默认 git rev-parse 当前仓)")
    sp.add_argument("--chroma", action="store_true", help="只跑 chroma (与其它 flag 组合则只跑选中的)")
    sp.add_argument("--codegraph", action="store_true", help="只跑 codegraph sync")
    sp.add_argument("--cross-link", action="store_true", dest="cross_link", help="只跑 cross-link 重建")
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
