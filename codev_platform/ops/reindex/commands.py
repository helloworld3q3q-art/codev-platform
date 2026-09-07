"""重建索引 CLI 子命令处理、兼容重导出与参数注册。

本模块从原始 ops/reindex.py 纯移动拆分而来，业务逻辑保持不变。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from codev_platform.codegraph.operation_lease import codegraph_reindex_leases
from codev_platform.ops import _common as C
from codev_platform.reindex.maintenance_gate import (
    maintenance_reindex_operation_permit,
)
from codev_platform.reindex.producer_route import (
    LocalHookRelayRequired,
    require_local_hook_queue_route,
)

from .codegraph_stage import run_codegraph_stage, sync_codegraph_repos
from .dispatch import _dispatch_reindex
from .logs import _git_out as _git_out
from .logs import _reindex_log as _reindex_log
from .logs import changed_paths_between, commit_changed_paths
from .wait import (
    _BLOCK_START_RE as _BLOCK_START_RE,
    _ENQUEUED_RE as _ENQUEUED_RE,
    _FINISHED_RE as _FINISHED_RE,
    _TRIGGER_LINE_RE as _TRIGGER_LINE_RE,
    _commit_covers as _commit_covers,
    _latest_trigger_index as _latest_trigger_index,
    _queued_expected_jobs as _queued_expected_jobs,
    _queued_jobs_completed as _queued_jobs_completed,
    _reindex_block_end as _reindex_block_end,
    cmd_wait_for_reindex as cmd_wait_for_reindex,
)


# ======================================================================
# 1. reindex  (port of update-local-ai.ps1)
# ======================================================================
_CODEGRAPH_COORDINATION_TIMEOUT_SEC = 15.0


def _bound_reindex_project(args: argparse.Namespace, repo: Path) -> str | None:
    """隔离入口在任何写入前复核 spec 绑定的项目与运行版本。"""
    if not getattr(args, "proven_runtime", False):
        return C.project_id_of(repo)
    from codev_platform.core.project_id import ProjectIdError, validate
    from codev_platform.core.runtime_interpreter import verify_reindex_runtime_revision

    expected_project = getattr(args, "expected_project_id", None)
    expected_revision = getattr(args, "expected_runtime_revision", None)
    try:
        project_id = validate(expected_project)
        declared = C.project_id_of(repo)
        declared_id = validate(declared) if declared is not None else None
    except (ProjectIdError, TypeError):
        raise RuntimeError("隔离 reindex 项目身份无效") from None
    if (
        type(expected_project) is not str
        or project_id != expected_project
        or os.environ.get("PLATFORM_PROJECT_ID") != project_id
        or type(declared) is not str
        or declared != declared_id
        or declared_id != project_id
    ):
        raise RuntimeError("隔离 reindex 项目身份不一致")
    try:
        verify_reindex_runtime_revision(expected_revision)
    except RuntimeError:
        raise RuntimeError("隔离 reindex 运行版本证明无效") from None
    from codev_platform.core.repo_input_guard import proven_repo_input_guard
    try:
        guard = proven_repo_input_guard()
    except ValueError:
        raise RuntimeError("隔离 reindex 输入证明上下文无效") from None
    if guard is None:
        raise RuntimeError("隔离 reindex 输入证明上下文缺失")
    try:
        guard.validate_main_root(repo)
    except ValueError:
        raise RuntimeError("隔离 reindex --repo 与冻结主仓不一致") from None
    return project_id


def _reindex_result_label(rc: int, *, ingest_incomplete: bool) -> str:
    """区分 legacy fail-soft 与真实成功，避免 rc=0 文案伪装完整证明。"""
    if rc == 0:
        return "部分完成（legacy 兼容降级；无 ingest 证明）" if ingest_incomplete else "ok"
    if rc == 2:
        return "rc=2 (will retry)"
    return f"失败 (rc={rc})"


def _sync_codegraph_repos(repo: Path, project_id: str | None) -> tuple[bool, int]:
    """保持兼容私有入口，并注入固定预算的多仓协调策略。"""

    def coordinated(repositories: tuple[Path, ...]):
        return codegraph_reindex_leases(
            repositories,
            timeout_sec=_CODEGRAPH_COORDINATION_TIMEOUT_SEC,
        )

    return sync_codegraph_repos(
        repo,
        project_id,
        reindex_leases=coordinated,
    )


def _run_ingest_stage(
    args: argparse.Namespace,
    repo: Path,
    project_id: str | None,
) -> tuple[bool, int]:
    """运行可局部降级的图谱组件，并单独返回完整性与进程返回码。"""
    if not project_id:
        C.out("step 3/4: graph ingest     -- skipped (repo 无 project_id)")
        return False, 0
    try:
        from codev_platform.graph.ingest import ingest_project

        report = ingest_project(repo, project_id)
    except Exception as exc:  # noqa: BLE001 — 组件隔离后由证明门禁统一失败
        C.err(f"WARN: graph ingest failed (non-fatal, baseline indexes unaffected): {exc}")
        return True, 1 if getattr(args, "proven_runtime", False) else 0
    if report.failures:
        C.err(
            "WARN: graph ingest failed（未完成）："
            f"{len(report.failures)} 个组件失败；已拒绝成功证明"
        )
        return True, 1 if getattr(args, "proven_runtime", False) else 0
    if report.ingested:
        C.out(f"graph ingest ok: {len(report.ingested)} plugin(s) -> store "
              f"[{', '.join(report.ingested)}]")
    else:
        C.out("graph ingest ok: no applicable plugin produced output")
    C.out("proof: ingest ok")
    return False, 0


def _run_codegraph_stage(repo: Path, project_id: str | None) -> tuple[bool, int]:
    """保持命令层可替换 seam，不让 stage 叶子反向依赖 CLI。"""
    return run_codegraph_stage(repo, project_id, sync_repos=_sync_codegraph_repos)


def _run_chroma_stage(
    args: argparse.Namespace,
    repo: Path,
    project_id: str | None,
) -> int:
    """为 Chroma 选择已证明解释器并执行独立子进程。"""
    chroma_py = os.path.abspath(sys.executable) if getattr(
        args,
        "proven_runtime",
        False,
    ) else C.chroma_python()
    if not chroma_py or not Path(chroma_py).exists():
        C.err(f"FAIL: chroma python not found ({chroma_py}); set runtime.chroma_venv in config")
        return 1
    env = dict(os.environ)
    env["PLATFORM_ROOT"] = str(repo)
    if project_id is not None:
        env["PLATFORM_PROJECT_ID"] = project_id
    cmd = [str(chroma_py), "-I", "-m", "codev_platform.chroma.indexer"]
    if args.force:
        cmd.append("--force")
    rc = C.run(cmd, env=env).returncode
    if rc != 0:
        C.err(f"FAIL: chroma reindex exit={rc}")
    return rc


def _build_code_vector_stage(args: argparse.Namespace, project_id: str | None) -> int:
    """执行一次 code_vec；隔离任务失败闭合，前台 legacy 聚合保持 fail-soft。"""
    if not project_id:
        C.out("step 4/4: code vector      -- skipped (repo 无 project_id)")
        return 0
    from codev_platform.recall.code_vector_store import (
        CodeVecLockBusy,
        build_code_vector_index,
    )

    try:
        count = build_code_vector_index(project_id, incremental=not args.force)
    except CodeVecLockBusy as exc:
        C.out(f"step 4/4: code vector      -- 锁忙跳过, 本 job 将重试: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 — 隔离任务与 legacy 前台采用不同失败策略
        if getattr(args, "proven_runtime", False):
            C.err(f"FAIL: code vector failed: {exc}")
            return 1
        C.err(f"WARN: code vector failed (non-fatal, baseline indexes unaffected): {exc}")
        return 0
    C.out(f"code vector ok: {count} 节点 (re)embedded")
    C.out("proof: code_vec ok")
    return 0


def _run_code_vector_stage(
    args: argparse.Namespace,
    project_id: str | None,
    *,
    enabled: bool,
) -> int:
    """按本次选择路由 code_vec stage；前置 CodeGraph 非零时不会到达这里。"""
    if not enabled:
        C.out("step 4/4: code vector      -- skipped")
        return 0
    C.out("")
    C.out("=== step 4/4: code vector index ===")
    return _build_code_vector_stage(args, project_id)


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
    # 维护准备以独占锁等待整个实际写阶段退出；仓路径解析是只读，可不占用许可。
    with maintenance_reindex_operation_permit() as permitted:
        if not permitted:
            C.err("FATAL: reindex 维护窗口已启用；所有 reindex 写阶段均不可运行，受控 service 仅可待命")
            return 1
        return _run_reindex_stages(args, repo)


def _run_reindex_stages(args: argparse.Namespace, repo: Path) -> int:
    """在维护许可已持有时执行完整或分段 reindex 写阶段。"""
    try:
        project_id = _bound_reindex_project(args, repo)
    except RuntimeError as exc:
        C.err(str(exc))
        return 1

    do_ingest_flag = getattr(args, "ingest", False)
    do_codevec_flag = getattr(args, "code_vec", False)
    selected = bool(args.chroma or args.codegraph or do_ingest_flag or do_codevec_flag)
    do_codegraph = args.codegraph if selected else True
    do_chroma = args.chroma if selected else True
    do_ingest = do_ingest_flag if selected else True
    do_codevec = do_codevec_flag if selected else True
    if args.force or (do_codegraph and do_chroma and not selected):
        C.out("[reindex] full rebuild: run in foreground to watch progress "
              "(post-commit handles incremental in background)")
    started = time.monotonic()
    ingest_incomplete = False
    ingest_rc = 0

    # --- stage 1/4: codegraph sync ---
    if do_codegraph:
        C.out("")
        C.out("=== step 1/4: codegraph sync ===")
        _, rc = _run_codegraph_stage(repo, project_id)
        if rc != 0:
            return rc
    else:
        C.out("step 1/4: codegraph sync   -- skipped")
    # --- stage 2/4: chroma reindex ---
    if do_chroma:
        C.out("")
        C.out("=== step 2/4: chroma reindex ===")
        rc = _run_chroma_stage(args, repo, project_id)
        if rc != 0:
            return rc
    else:
        C.out("step 2/4: chroma reindex   -- skipped")

    # --- stage 3/4: unified graph ingest (plugins -> graph store) ---
    # 组件之间保持失败隔离；最终 marker/rc 由结构化完整性报告统一门禁。
    if do_ingest:
        C.out("")
        C.out("=== step 3/4: unified graph ingest ===")
        ingest_incomplete, ingest_rc = _run_ingest_stage(args, repo, project_id)
    else:
        C.out("step 3/4: graph ingest     -- skipped")
    # --- stage 4/4: code vector index (vector lane) ---
    codevec_rc = _run_code_vector_stage(
        args,
        project_id,
        enabled=do_codevec,
    )

    final_rc = codevec_rc or ingest_rc
    dur = int(time.monotonic() - started)
    C.out("")
    C.out(f"total: {dur}s, reindex {_reindex_result_label(final_rc, ingest_incomplete=ingest_incomplete)}")
    return final_rc

# ======================================================================
# 2. post-commit  (port of post-commit.ps1)
# ======================================================================
def _local_hook_queue_route_ready(banner: str) -> bool:
    """Keep hook commands fail-soft while refusing an unsafe queue writer."""
    try:
        require_local_hook_queue_route(C.config())
    except LocalHookRelayRequired as exc:
        C.err(f"[{banner}] 已拒绝本地入队: {exc}")
        return False
    return True


def cmd_post_commit(args: argparse.Namespace) -> int:
    """Git post-commit hook: diff 刚做的 commit → 按 scope reindex。NEVER fail commit。"""
    try:
        rc, top = _git_out(None, "rev-parse", "--show-toplevel")
        if rc != 0 or not top:
            return 0
        repo = Path(top).resolve()
        rc, changed = commit_changed_paths(repo, "HEAD", git_out=_git_out)
        if rc != 0 or not changed:
            return 0
        if not _local_hook_queue_route_ready("post-commit"):
            return 0
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
        rc, changed = changed_paths_between(repo, "ORIG_HEAD", "HEAD", git_out=_git_out)
        if rc != 0 or not changed:
            return 0
        if not _local_hook_queue_route_ready("post-merge"):
            return 0
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
        rc, changed = changed_paths_between(repo, prev, new, git_out=_git_out)
        if rc != 0 or not changed:
            return 0
        if not _local_hook_queue_route_ready("post-checkout"):
            return 0
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
                "allow grep/read fallback; commit and let the installed hook refresh indexes"
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
    C.out("  3. dirty state alone never authorizes a manual rebuild")
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
    sp.add_argument("--code-vec", dest="code_vec", action="store_true",
                    help="只跑代码向量索引刷新 (vector lane; 增量重嵌变更节点)")
    sp.add_argument("--force", action="store_true", help="chroma indexer 传 --force (drop + rebuild)")
    sp.add_argument("--proven-runtime", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--expected-project-id", help=argparse.SUPPRESS)
    sp.add_argument("--expected-runtime-revision", help=argparse.SUPPRESS)
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
