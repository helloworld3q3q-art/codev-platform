"""reindex 的 CodeGraph 同步阶段。

本叶子模块只编排 CodeGraph 仓链接、多仓协调上下文和子进程返回码，不承担 CLI 参数
解析或其它索引 stage。这样 MCP 后端可先协作排空，再由重建命令一次取得全部仓的写窗口。
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path

from codev_platform.codegraph.operation_lease import (
    CodegraphOperationLeaseBusyError,
    CodegraphOperationLeaseError,
    codegraph_reindex_leases,
)
from codev_platform.ops import _common as C


ReindexLeases = Callable[[tuple[Path, ...]], AbstractContextManager[None]]
SyncRepos = Callable[[Path, str | None], tuple[bool, int]]


class _CodegraphCliUnavailableError(RuntimeError):
    """仅表示启动 ``codegraph sync`` 时找不到可执行文件。"""


def sync_codegraph_repos(
    repo: Path,
    project_id: str | None,
    *,
    reindex_leases: ReindexLeases = codegraph_reindex_leases,
) -> tuple[bool, int]:
    """在一个多仓协调上下文内同步 CodeGraph，避免产生部分成功证明。"""
    if project_id:
        from codev_platform.core.config import load_config
        from codev_platform.core.repos import project_repo_specs

        cfg = load_config()
        specs = project_repo_specs(project_id, main_repo=repo, cfg=cfg)
    else:
        cfg = {}
        specs = []

    if not specs:
        from codev_platform.core.repos import RepoSpec

        specs = [RepoSpec(root=repo, tag="", is_main=True)]

    repositories = tuple(spec.root for spec in specs)
    with ExitStack() as stack:
        try:
            stack.enter_context(reindex_leases(repositories))
        except CodegraphOperationLeaseBusyError:
            C.out("WARN: CodeGraph 重建协调超时；本任务将重试")
            return True, 2
        except CodegraphOperationLeaseError:
            C.err("FAIL: CodeGraph 重建协调租约不可用")
            return False, 1
        return _sync_codegraph_specs(specs, project_id, cfg)


def _sync_codegraph_specs(specs: list[object], project_id: str | None, cfg: dict) -> tuple[bool, int]:
    """协调租约已持有时逐仓执行同步，并保留原生数据库锁忙语义。"""
    for spec in specs:
        label = "main" if spec.is_main else f"extra:{spec.tag}"
        C.out(f"codegraph sync [{label}] {spec.root}")
        _ensure_codegraph_repo_link(project_id, spec, cfg, label)
        cwd = str(spec.root)
        try:
            cp = C.run(["codegraph", "sync"], cwd=cwd)
        except FileNotFoundError:
            raise _CodegraphCliUnavailableError from None
        rc = cp.returncode
        if rc == 2:
            C.out(f"WARN: codegraph sync 未完成（{label}，原生数据库锁忙）；本任务将重试")
            return True, 2
        if rc != 0:
            return False, rc
    return False, 0


def run_codegraph_stage(
    repo: Path,
    project_id: str | None,
    *,
    sync_repos: SyncRepos = sync_codegraph_repos,
) -> tuple[bool, int]:
    """执行多仓 CodeGraph；租约与原生数据库锁忙均要求后续重试。"""
    locked = False
    try:
        locked, rc = sync_repos(repo, project_id)
    except _CodegraphCliUnavailableError:
        C.err("FAIL: codegraph CLI 不可用")
        return False, 1
    if locked and rc in {0, 2}:
        return True, 2
    if rc != 0:
        C.err(f"FAIL: codegraph sync exit={rc}")
        return locked, rc
    if not locked:
        C.out("proof: codegraph ok")
    return locked, 0


def _ensure_codegraph_repo_link(project_id: str | None, spec: object, cfg: dict, label: str) -> None:
    """在持有仓级租约后确保该仓的 CodeGraph 链接存在。"""
    if not project_id:
        return
    from codev_platform.ops.codegraph import ensure_codegraph_linked

    link_pid = project_id if getattr(spec, "is_main", False) else getattr(
        spec,
        "source_project_id",
        None,
    )
    if not link_pid:
        return
    result = ensure_codegraph_linked(link_pid, spec.root, cfg)
    if result.get("action") == "error":
        C.err(f"WARN: ensure codegraph link failed ({label}, fail-soft): {result.get('note')}")


__all__ = ["run_codegraph_stage", "sync_codegraph_repos"]
