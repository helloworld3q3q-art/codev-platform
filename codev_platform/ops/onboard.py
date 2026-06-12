"""`codev-platform onboard <code> --repo <path>` —— 一条命令接入新项目。

**编排器**: 复用既有能力(config / project_id / RbacStore / reindex queue / codegraph), 不重造。
把原本散落的步骤 + 3 个隐藏坑收成一条幂等命令:
  - 缺 `.claude/project.json` → graph ingest 静默跳过(不报错)
  - 新仓没 `codegraph init` → reindex codegraph sync exit=1 失败丢弃
  - 只 config 注册不写 meta.json → web / list-projects 不显示

分层: 关键步(repo 校验 / config / project.json / meta)失败即停; 软步(RBAC / codegraph init)
失败只 warn 不阻断接入(单机无 PG / 未装 codegraph 仍能完成基础接入 + graph 索引)。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _git_remote_url(repo: Path) -> str | None:
    """repo 的 origin remote url(供 meta.json repo_url); 取不到返 None(best-effort)。"""
    try:
        r = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                           capture_output=True, text=True, timeout=10)
        return (r.stdout.strip() or None) if r.returncode == 0 else None
    except Exception:  # noqa: BLE001 — 取不到 url 不影响接入
        return None


def cmd_onboard(args: argparse.Namespace) -> int:
    from codev_platform.core.config import load_config, save_config
    from codev_platform.core.project_id import ProjectIdError, validate

    # --- 0. 校验(关键步, 失败即停)---
    try:
        code = validate(args.code)
    except ProjectIdError as exc:
        _err(f"FATAL: 非法 project_id: {exc}")
        return 1
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        _err(f"FATAL: repo 路径不存在: {repo}")
        return 1
    org, owner, name = args.org, args.owner, (args.name or code)
    _out(f"=== onboard {code} (repo={repo}, org={org}) ===")

    # --- 1. config projects.<code>(reindex worker 据此解析 repo_path)---
    cfg = load_config()
    cfg.setdefault("projects", {})[code] = {"repo_path": str(repo), "org_id": org}
    save_config(cfg)
    _out(f"[1/6] config projects.{code} 已写 (repo_path + org_id={org})")

    # --- 2. <repo>/.claude/project.json(graph ingest 据此解析 project_id, 缺则静默跳过)---
    pj = repo / ".claude" / "project.json"
    pj.parent.mkdir(parents=True, exist_ok=True)
    pj.write_text(json.dumps({"project_id": code, "display_name": name},
                             ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _out("[2/6] .claude/project.json 已写 (graph ingest project_id 真值源)")

    # --- 3. platform_meta/projects/<code>/meta.json(list-projects / web 可见)---
    from codev_platform.cli import PLATFORM_META_PROJECTS
    meta_dir = PLATFORM_META_PROJECTS / code
    meta_dir.mkdir(parents=True, exist_ok=True)
    meta = {"project_id": code, "display_name": name, "repo_path": str(repo)}
    url = _git_remote_url(repo)
    if url:
        meta["repo_url"] = url
    (meta_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _out("[3/6] meta.json 已写 (list-projects / web 列表可见)")

    # --- 4. RBAC(软步: 无 PG / 单机 passthrough 跳过, 不阻断)---
    try:
        from codev_platform.agent.deps import get_rbac_store
        store = get_rbac_store()
        if store is not None:
            store.upsert_project(code, org, name=name)
            store.grant_project(code, owner, "user", role="admin")
            _out(f"[4/6] RBAC: project 挂 org={org} + 授 {owner} admin")
        else:
            _out("[4/6] RBAC: 跳过 (无 PG store; 单机 passthrough 无需)")
    except Exception as exc:  # noqa: BLE001 — RBAC 不可用不阻断基础接入
        _out(f"[4/6] RBAC: 跳过 (不可用: {type(exc).__name__})")

    # --- 5. codegraph init(软步: 未初始化才 init; 未装 codegraph 跳过)---
    if (repo / ".codegraph" / "codegraph.db").exists():
        _out("[5/6] codegraph: 已初始化 (跳过 init)")
    else:
        try:
            r = subprocess.run(["codegraph", "init"], cwd=str(repo),
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                _out("[5/6] codegraph init OK (reindex codegraph 方可建符号索引)")
            else:
                _out(f"[5/6] codegraph init 失败 (手动 `cd {repo} && codegraph init`): "
                     f"{r.stderr.strip()[:120]}")
        except FileNotFoundError:
            _out("[5/6] codegraph: 命令未装, 跳过 (装 codegraph 后手动 init; 不影响 graph/chroma)")
        except Exception as exc:  # noqa: BLE001
            _out(f"[5/6] codegraph init 跳过: {type(exc).__name__}")

    # --- 6. reindex 入队(graph ingest + codegraph + code_vec + chroma)---
    if args.no_index:
        _out(f"[6/6] reindex: 跳过 (--no-index); 手动 `reindex-queue enqueue {code} --kind all`")
    else:
        try:
            from codev_platform.reindex import open_default_queue
            q = open_default_queue()
            kinds = ["ingest", "codegraph", "code_vec", "chroma"]
            for k in kinds:
                q.enqueue(code, k)
            _out(f"[6/6] reindex 入队: {', '.join(kinds)} (worker 后台串行消费)")
        except Exception as exc:  # noqa: BLE001 — 队列不可用是真错(接入不完整)
            _err(f"[6/6] reindex 入队失败: {exc}")
            return 1

    _out("")
    _out(f"OK: {code} 接入完成。")
    _out("  下一步:")
    _out("  - `reindex-queue status` 看索引进度")
    _out("  - codegraph 索引完后让端点认新项目: `serve-mcp start`(或重启 codegraph 端点)")
    _out("  - token 模式: 给该项目仓 .mcp.json 配 token(header 形式)")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "onboard",
        help="一条命令接入新项目 (config + project.json + meta + RBAC + codegraph init + reindex)",
    )
    p.add_argument("code", help="project_id (小写字母 / 数字 / 连字符)")
    p.add_argument("--repo", required=True, help="项目仓本机路径")
    p.add_argument("--org", default="default", help="归属 org (默认 default)")
    p.add_argument("--owner", default="root", help="授 admin 的 owner user_id (默认 root)")
    p.add_argument("--name", default=None, help="display_name (默认 = code)")
    p.add_argument("--no-index", action="store_true", help="只登记不入队索引")
    p.set_defaults(func=cmd_onboard)
