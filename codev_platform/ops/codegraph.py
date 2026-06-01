"""codev-platform codegraph —— 把业务项目的 codegraph 索引集中到平台 data/(目录联接)。

让业务仓的 codegraph **数据 + 更新都走平台**(与 cross-link 对称):
- 数据: `<repo>/.codegraph` 移进 `data/codegraph_ext/<pid>/codegraph/`(与 cross_layer.sqlite 并排),
  业务仓 `.codegraph` 改成 junction/symlink 指向平台 —— 物理数据在平台, 第三方工具透明无感。
- 服务: `serve-mcp` 的 codegraph 端点(mcp-proxy 包 `codegraph serve`,cwd=repo)经 junction 读平台数据。
- 更新: `codev-platform reindex --codegraph` 跑的 `codegraph sync`(cwd=repo)写穿 junction 落平台 —— 更新天然走平台。

actions:
  status  各项目 codegraph 数据在哪(in-repo / linked→platform / platform-only / missing)
  link    把 .codegraph 移进平台 + 建 junction(幂等;--project X | --all)
  unlink  反向(删 junction + 数据移回仓)

junction 是本机状态(不进 git, 同平台所有 data/)。换机器/重 clone 后重跑 `link` 即恢复。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

from codev_platform.ops import _common as C
from codev_platform.core.paths import codegraph_index_dir


# ---- 跨平台 link 原语(Windows junction / POSIX symlink, 目录都不需管理员)----

def _is_link(p: Path) -> bool:
    """p 是否 junction(Windows)或 symlink(POSIX)。"""
    try:
        if p.is_symlink():
            return True
        if os.name == "nt":
            attrs = os.lstat(p).st_file_attributes  # type: ignore[attr-defined]
            return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except (OSError, AttributeError):
        return False
    return False


def _link_target(p: Path) -> Path | None:
    try:
        t = os.readlink(p)
    except OSError:
        return None
    t = t.replace("\\\\?\\", "").replace("\\??\\", "")  # Windows junction 前缀
    return Path(t)


def _make_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        cp = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True)
        if cp.returncode != 0:
            raise OSError(f"mklink /J 失败: {(cp.stderr or cp.stdout).strip()}")
    else:
        os.symlink(target, link, target_is_directory=True)


def _remove_link(link: Path) -> None:
    # Windows junction 是目录 reparse point: rmdir 删联接本身, 不动 target 内容
    if os.name == "nt":
        os.rmdir(link)
    else:
        os.unlink(link)


def _same_path(a: Path | None, b: Path) -> bool:
    if a is None:
        return False
    try:
        return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))
    except OSError:
        return False


# ---- 状态判定(纯函数, 可单测)----

def link_state(repo_cg: Path, plat_cg: Path) -> str:
    """返回: linked / link-broken / in-repo / platform-only / both / missing。"""
    if _is_link(repo_cg):
        tgt = _link_target(repo_cg)
        if plat_cg.exists() and (_same_path(tgt, plat_cg) or tgt is None):
            return "linked"
        return "link-broken"
    if repo_cg.is_dir() and plat_cg.exists():
        return "both"
    if repo_cg.is_dir():
        return "in-repo"
    if plat_cg.exists():
        return "platform-only"
    return "missing"


# ---- project 枚举 ----

def _iter_projects(cfg: dict):
    """yield (pid, repo_path) —— config.projects 里配了 repo_path 且仓存在的项目。
    codev-platform 自身不算(它就是平台, 索引留在本仓)。"""
    from codev_platform.core.config import get as _get
    projects = _get(cfg, "projects") or {}
    for pid in sorted(projects):
        pc = projects.get(pid)
        if not isinstance(pc, dict):
            continue
        repo = pc.get("repo_path")
        if not repo:
            continue
        rp = Path(repo).expanduser()
        if rp.exists():
            yield pid, rp


def _stop_endpoint(cfg: dict, pid: str) -> bool:
    """搬数据前停掉该 project 的 codegraph SSE 端点(否则 db 被占无法移)。best-effort。"""
    try:
        import psutil  # type: ignore
        from codev_platform import mcp_serve
    except ImportError:
        return False
    port = next((ep.port for ep in mcp_serve.iter_endpoints(cfg)
                 if ep.kind == "codegraph" and ep.project_id == pid), None)
    if port is None:
        return False
    killed = False
    for c in psutil.net_connections(kind="inet"):
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
            try:
                proc = psutil.Process(c.pid)
                for ch in proc.children(recursive=True):
                    ch.kill()
                proc.kill()
                killed = True
            except psutil.Error:
                pass
    return killed


# ---- 核心操作 ----

def link_project(cfg: dict, pid: str, repo: Path, *, dry_run: bool = False) -> dict:
    repo_cg = repo / ".codegraph"
    plat_cg = codegraph_index_dir(pid)
    state = link_state(repo_cg, plat_cg)
    if state == "linked":
        return {"pid": pid, "action": "already-linked", "platform": str(plat_cg)}
    if state == "missing":
        return {"pid": pid, "action": "skip", "note": "无 .codegraph 索引(先在仓内 codegraph index)"}
    if state == "both":
        return {"pid": pid, "action": "conflict",
                "note": f"仓内和平台都有 .codegraph, 手动取舍(平台: {plat_cg})"}
    if dry_run:
        return {"pid": pid, "action": f"would-link ({state})", "platform": str(plat_cg)}

    _stop_endpoint(cfg, pid)  # 停端点释放 db 锁
    plat_cg.parent.mkdir(parents=True, exist_ok=True)
    if state == "in-repo":
        shutil.move(str(repo_cg), str(plat_cg))   # 仓内 → 平台
        _make_link(repo_cg, plat_cg)
        return {"pid": pid, "action": "moved+linked", "platform": str(plat_cg)}
    if state == "platform-only":
        _make_link(repo_cg, plat_cg)               # 数据已在平台, 仅补 junction
        return {"pid": pid, "action": "relinked", "platform": str(plat_cg)}
    if state == "link-broken":
        _remove_link(repo_cg)                       # 悬空联接
        return {"pid": pid, "action": "skip", "note": "junction 悬空且平台无数据, 已清联接"}
    return {"pid": pid, "action": "skip", "note": f"未知状态 {state}"}


def unlink_project(cfg: dict, pid: str, repo: Path) -> dict:
    repo_cg = repo / ".codegraph"
    plat_cg = codegraph_index_dir(pid)
    if not _is_link(repo_cg):
        return {"pid": pid, "action": "not-linked", "note": "仓内 .codegraph 非联接, 不处理"}
    _stop_endpoint(cfg, pid)
    _remove_link(repo_cg)
    if plat_cg.exists():
        shutil.move(str(plat_cg), str(repo_cg))    # 平台 → 移回仓
        return {"pid": pid, "action": "unlinked+moved-back"}
    return {"pid": pid, "action": "unlinked", "note": "平台无数据, 仅删联接"}


def ensure_codegraph_linked(project_id: str, repo: Path, cfg: dict) -> dict:
    """reindex --codegraph 跑 `codegraph sync` 前的幂等 ensure-link(best-effort, fail-soft)。

    复用 link_project / link_state 原语, 不重造 junction 逻辑:
      - 已 linked → no-op(action="already-linked");
      - in-repo / platform-only / link-broken → 调 link_project 建/修联接(幂等);
      - missing(还没索引数据) → skip, 让 sync 在仓内首次生成。
    任何异常都吞掉(返回 {"action": "error"}), 绝不中断 reindex —— link 是本机便利, 失败不该挡 sync。
    """
    repo = Path(repo)
    try:
        repo_cg = repo / ".codegraph"
        plat_cg = codegraph_index_dir(project_id)
        if link_state(repo_cg, plat_cg) == "linked":
            return {"pid": project_id, "action": "already-linked"}
        return link_project(cfg, project_id, repo)
    except Exception as exc:  # noqa: BLE001 - fail-soft: link 失败不挡 reindex
        return {"pid": project_id, "action": "error", "note": str(exc)}


def cmd_codegraph(args: argparse.Namespace) -> int:
    from codev_platform.ops._common import config as load_cfg
    cfg = load_cfg()
    projects = list(_iter_projects(cfg))
    if args.project:
        projects = [(p, r) for p, r in projects if p == args.project]
        if not projects:
            C.err(f"项目 {args.project} 未在 config.projects 配 repo_path 或仓不存在")
            return 1
    elif not args.all and args.action in ("link", "unlink"):
        C.err("link/unlink 需指定 --project <id> 或 --all")
        return 1

    if args.action == "status":
        C.out(f"{'project'.ljust(24)} {'状态'.ljust(14)} 平台数据位置")
        C.out("-" * 80)
        for pid, repo in projects:
            plat = codegraph_index_dir(pid)
            st = link_state(repo / ".codegraph", plat)
            tag = {"linked": "✅ 走平台", "in-repo": "⚠️ 还在仓内", "platform-only": "○ 平台有/缺联接",
                   "both": "✗ 冲突", "link-broken": "✗ 联接悬空", "missing": "- 未索引"}.get(st, st)
            C.out(f"{pid.ljust(24)} {tag.ljust(14)} {plat if plat.exists() else '-'}")
        return 0

    fn = link_project if args.action == "link" else unlink_project
    rc = 0
    for pid, repo in projects:
        if args.action == "link":
            r = link_project(cfg, pid, repo, dry_run=args.dry_run)
        else:
            r = unlink_project(cfg, pid, repo)
        note = ("  " + r["note"]) if r.get("note") else ("  → " + r["platform"] if r.get("platform") else "")
        C.out(f"  {pid.ljust(24)} {r['action']}{note}")
        if r["action"] == "conflict":
            rc = 1
    if args.action == "link" and not args.dry_run:
        C.out("")
        C.out("提示: 重起 codegraph 端点让其经 junction 读平台数据 → `codev-platform serve-mcp start`")
    return rc


def register(subparsers) -> None:
    sp = subparsers.add_parser(
        "codegraph",
        help="codegraph 索引集中到平台 data/(junction): status / link / unlink",
    )
    sp.add_argument("action", choices=["status", "link", "unlink"],
                    help="status=看在哪 / link=移进平台+联接 / unlink=移回仓")
    sp.add_argument("--project", default=None, help="只处理某 project_id")
    sp.add_argument("--all", action="store_true", help="处理 config.projects 全部(repo 存在的)")
    sp.add_argument("--dry-run", action="store_true", help="link 只演练不动文件")
    sp.set_defaults(func=cmd_codegraph)
