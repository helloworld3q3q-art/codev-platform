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


# codegraph .codegraph/config.json 默认: 只索引代码语言 + 排噪声(min.js/vendored/dist/target/
# public/static 等)。**真实大仓必备**: 缺 config 时 codegraph 默认扫 min.js/vendored → 符号索引
# 病态膨胀(实测 31773 文件仓 → 828MB 卡死)。生成进仓 → 随 git 走, 不靠手动塞。
_CODEGRAPH_CONFIG = {
    "version": 1,
    "include": ["**/*.java", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.vue",
                "**/*.py", "**/*.go", "**/*.rs", "**/*.kt", "**/*.scala", "**/*.cs"],
    "exclude": [
        "**/.git/**", "**/node_modules/**", "**/vendor/**", "**/dist/**", "**/build/**",
        "**/out/**", "**/bin/**", "**/target/**", "**/generated-sources/**",
        "**/*.min.js", "**/*.min.css", "**/*.bundle.*", "**/*.umd.js",
        "**/public/**", "**/static/**", "**/assets/**",
        "**/.gradle/**", "**/.m2/**", "**/__pycache__/**", "**/.venv/**",
        "**/.idea/**", "**/logs/**", "**/tmp/**", "**/temp/**", "**/coverage/**",
    ],
    "languages": [], "frameworks": [],
    "maxFileSize": 1048576, "extractDocstrings": True, "trackCallSites": True,
}

_CG_GITIGNORE_LINES = [
    "# codegraph 索引产物(平台代码智能): 只提交 .codegraph/config.json, 不提交 db/lock",
    ".codegraph/codegraph.db",
    ".codegraph/codegraph.db-*",
    ".codegraph/*.lock",
]


def _write_codegraph_config(repo: Path) -> bool:
    """写 <repo>/.codegraph/config.json(已存在不覆盖, 保用户自定义)。返回是否新写。"""
    cfg_path = repo / ".codegraph" / "config.json"
    if cfg_path.exists():
        return False
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(_CODEGRAPH_CONFIG, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return True


def _ensure_gitignore(repo: Path) -> None:
    """幂等追加 codegraph db 忽略规则(只提交 config.json, 不提交大 db)。"""
    gi = repo / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    if ".codegraph/codegraph.db" in existing:
        return
    sep = "" if existing.endswith("\n") or not existing else "\n"
    gi.write_text(existing + sep + "\n" + "\n".join(_CG_GITIGNORE_LINES) + "\n", encoding="utf-8")


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
            _out(f"[4/8] RBAC: project 挂 org={org} + 授 {owner} admin")
        else:
            _out("[4/8] RBAC: 跳过 (无 PG store; 单机 passthrough 无需)")
    except Exception as exc:  # noqa: BLE001 — RBAC 不可用不阻断基础接入
        _out(f"[4/8] RBAC: 跳过 (不可用: {type(exc).__name__})")

    # --- 5. sync rules/skills/hooks 进业务仓 .claude/(软步: 资源缺失只 warn 不阻断)---
    try:
        from codev_platform.cli_cmds.sync import sync_resources_to
        _out("[5/8] sync rules/skills/hooks → 业务仓 .claude/:")
        sync_resources_to(repo, dry_run=False)
    except Exception as exc:  # noqa: BLE001 — 同步失败不阻断接入
        _out(f"[5/8] sync: 跳过 (失败: {type(exc).__name__})")

    # --- 6. codegraph: 写 config.json(排噪声, 可提交进仓) + gitignore db + init ---
    wrote_cfg = _write_codegraph_config(repo)
    _ensure_gitignore(repo)
    cfg_note = "config.json 已生成" if wrote_cfg else "config.json 已存在(保留)"
    if (repo / ".codegraph" / "codegraph.db").exists():
        _out(f"[6/8] codegraph: {cfg_note}; 已初始化 (跳过 init)")
    else:
        try:
            r = subprocess.run(["codegraph", "init"], cwd=str(repo),
                               capture_output=True, text=True, timeout=60)
            ok = "init OK" if r.returncode == 0 else f"init 失败({r.stderr.strip()[:80]})"
            _out(f"[6/8] codegraph: {cfg_note}; {ok}")
        except FileNotFoundError:
            _out(f"[6/8] codegraph: {cfg_note}; 命令未装跳过 init (不影响 graph/chroma)")
        except Exception as exc:  # noqa: BLE001
            _out(f"[6/8] codegraph: {cfg_note}; init 跳过 ({type(exc).__name__})")

    # --- 7. 生成业务仓 .mcp.json(不存在才写, 保用户自定义; 软步)---
    mcp_json = repo / ".mcp.json"
    if mcp_json.exists():
        _out("[7/8] .mcp.json: 已存在(保留); 切源用 `codev-platform mcp-source`")
    else:
        try:
            from codev_platform.mcp_serve import build_mcp_servers
            servers = build_mcp_servers(cfg, args.mcp_source, code)
            mcp_json.write_text(
                json.dumps({"mcpServers": servers}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            _out(f"[7/8] .mcp.json 已生成 ({args.mcp_source} 源, {len(servers)} 套端点)")
        except Exception as exc:  # noqa: BLE001 — 生成失败不阻断接入
            _out(f"[7/8] .mcp.json: 跳过 (失败: {type(exc).__name__})")

    # --- 8. reindex 入队(graph ingest + codegraph + code_vec + chroma)---
    if args.no_index:
        _out(f"[8/8] reindex: 跳过 (--no-index); 手动 `reindex-queue enqueue {code} --kind all`")
    else:
        try:
            from codev_platform.reindex import open_default_queue
            q = open_default_queue()
            kinds = ["ingest", "codegraph", "code_vec", "chroma"]
            for k in kinds:
                q.enqueue(code, k)
            _out(f"[8/8] reindex 入队: {', '.join(kinds)} (worker 后台串行消费)")
        except Exception as exc:  # noqa: BLE001 — 队列不可用是真错(接入不完整)
            _err(f"[8/8] reindex 入队失败: {exc}")
            return 1

    _out("")
    _out(f"OK: {code} 接入完成。")
    _out("  下一步:")
    _out("  - **把 .claude/{project.json,rules,skills,hooks,settings.json} + .codegraph/config.json"
         " + .gitignore + .mcp.json 提交进仓**(配置随 git 走, 别人/重 clone 也带得上)")
    _out("  - `reindex-queue status` 看索引进度")
    _out("  - codegraph 索引完后让端点认新项目: `serve-mcp start`(或重启 codegraph 端点)")
    _out("  - token 模式: 给 .mcp.json 各 server 补 headers.Authorization(Bearer <token>);"
         " 切 local/platform 源用 `mcp-source`")
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
    p.add_argument("--mcp-source", choices=["local", "platform"], default="platform",
                   help=".mcp.json 端点源 (platform=平台服务器 / local=本机本地实例; 默认 platform)")
    p.add_argument("--no-index", action="store_true", help="只登记不入队索引")
    p.set_defaults(func=cmd_onboard)
