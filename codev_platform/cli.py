"""codev-platform CLI - 多项目 AI 工具栈管理入口。

子命令:
    init           交互式创建 <cwd>/.claude/project.json
    current        打印当前解析到的 project_id (debug)
    list-projects  列出 platform_meta/projects/ 已注册项目
    validate       校验给定的 project_id 格式

用法:
    codev-platform <subcommand> [args]    # pip install -e . 后
    python -m codev_platform.cli <subcommand> [args]

> 重命令实现 (setup / config / sync / daemon / serve-mcp / mcp-source) 拆到
  codev_platform.cli_cmds.* (file-discipline §1, cli.py 瘦身); 本文件 re-export
  redact_config 保持 `codev_platform.cli.redact_config` 向后兼容 (测试依赖)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from codev_platform.core.project_id import (
    CONFIG_RELPATH,
    ENV_VAR,
    ProjectIdError,
    resolve_local,
    validate,
)

# 子命令实现 (按域拆到 cli_cmds 包)。redact_config re-export: tests/test_config_redact
# 与 config doctor 经 `codev_platform.cli.redact_config` 取用, 拆分后命名空间不变。
from codev_platform.cli_cmds._shared import _eprint, _print
from codev_platform.cli_cmds.config_cmd import cmd_config, redact_config
from codev_platform.cli_cmds.mcp import cmd_daemon, cmd_mcp_source, cmd_serve_mcp
from codev_platform.cli_cmds.setup_cmd import cmd_setup
# _RULES_SRC / _SKILLS_SRC re-export: tests/test_resources_packaging 经 `cli._RULES_SRC` 取用。
from codev_platform.cli_cmds.sync import (
    _HOOKS_SRC, _RULES_SRC, _SKILLS_SRC, cmd_sync_hooks, cmd_sync_rules, cmd_sync_skills,
)

__all__ = ["build_parser", "main", "redact_config", "PLATFORM_META_PROJECTS",
           "_RULES_SRC", "_SKILLS_SRC", "_HOOKS_SRC"]


# platform_meta 注册表路径: 默认仓内, 可由 CODEV_PLATFORM_META 覆盖
_DEFAULT_META = Path(__file__).resolve().parents[1] / "platform_meta" / "projects"
PLATFORM_META_PROJECTS = Path(os.environ.get("CODEV_PLATFORM_META", str(_DEFAULT_META)))


def cmd_init(args: argparse.Namespace) -> int:
    """交互或带参创建 <cwd>/.claude/project.json"""
    cwd = Path.cwd()
    target = cwd / CONFIG_RELPATH
    if target.exists() and not args.force:
        _eprint(f"已存在: {target}。加 --force 覆盖。")
        return 1

    if args.project_id:
        pid = args.project_id
    else:
        _print(f"目标: {target}")
        pid = input("project_id (小写字母/数字/连字符): ").strip()
    try:
        pid = validate(pid)
    except ProjectIdError as exc:
        _eprint(f"FATAL: {exc!s}")
        return 1

    display_name = args.display_name or input("display_name (可空): ").strip() or pid

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"project_id": pid, "display_name": display_name},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _print(f"OK: 写入 {target}")
    _print(f"     project_id={pid}")
    _print(f"     display_name={display_name}")
    _print()
    _print("下一步: 在 platform-meta/projects/ 注册元数据 (后续 server 化时上传)")
    return 0


def cmd_current(args: argparse.Namespace) -> int:
    """打印当前解析到的 project_id 及来源。"""
    try:
        pid = resolve_local()
    except ProjectIdError as exc:
        _eprint(f"FATAL: {exc!s}")
        return 1
    _print(f"project_id: {pid}")
    # 解析来源探测
    if os.environ.get(ENV_VAR):
        _print(f"source:     env {ENV_VAR}")
    else:
        cwd = Path.cwd().resolve()
        for parent in [cwd, *cwd.parents]:
            cand = parent / CONFIG_RELPATH
            if cand.is_file():
                _print(f"source:     {cand}")
                break
    return 0


def cmd_list_projects(args: argparse.Namespace) -> int:
    """读 platform-meta/projects/<id>/meta.json 列出所有已注册项目。"""
    if not PLATFORM_META_PROJECTS.is_dir():
        _eprint(f"未找到 {PLATFORM_META_PROJECTS}, platform-meta/ 未初始化")
        return 1
    rows = []
    for entry in sorted(PLATFORM_META_PROJECTS.iterdir()):
        if not entry.is_dir():
            continue
        meta_file = entry / "meta.json"
        if not meta_file.is_file():
            rows.append((entry.name, "(no meta.json)", ""))
            continue
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            rows.append((
                data.get("project_id", entry.name),
                data.get("display_name", ""),
                data.get("repo_path", ""),
            ))
        except json.JSONDecodeError as exc:
            rows.append((entry.name, f"(meta.json invalid: {exc!s})", ""))
    if not rows:
        _print("(无已注册项目)")
        _print("提示: 在项目仓根跑 `codev-platform register` 注册当前项目。")
        return 0
    width_id = max(len(r[0]) for r in rows)
    width_name = max(len(r[1]) for r in rows)
    _print(f"{'project_id'.ljust(width_id)}  {'display_name'.ljust(width_name)}  repo_path")
    _print("-" * (width_id + width_name + 20))
    for pid, name, path in rows:
        _print(f"{pid.ljust(width_id)}  {name.ljust(width_name)}  {path}")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    """把当前项目注册到 platform_meta/projects/<pid>/meta.json (list-projects 可见)。

    project_id 来源: 显式参数 > 当前 cwd 的 .claude/project.json。
    """
    cwd = Path.cwd()
    pid = args.project_id
    display_name = args.display_name
    # 无显式 pid 时从 cwd 的 project.json 读
    proj_json = cwd / CONFIG_RELPATH
    if (pid is None or display_name is None) and proj_json.is_file():
        try:
            data = json.loads(proj_json.read_text(encoding="utf-8"))
            pid = pid or data.get("project_id")
            display_name = display_name or data.get("display_name")
        except json.JSONDecodeError as exc:
            _eprint(f"FATAL: {proj_json} 解析失败: {exc!s}")
            return 1
    if not pid:
        _eprint(f"FATAL: 未指定 project_id 且 {proj_json} 不存在。先 `codev-platform init` 或显式传 project_id。")
        return 1
    try:
        pid = validate(pid)
    except ProjectIdError as exc:
        _eprint(f"FATAL: {exc!s}")
        return 1
    display_name = display_name or pid

    target = PLATFORM_META_PROJECTS / pid / "meta.json"
    if target.is_file() and not args.force:
        _eprint(f"已注册: {target}。加 --force 覆盖。")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "project_id": pid,
        "display_name": display_name,
        "repo_path": args.repo_path,
        "rules_path": ".claude/rules",
        "memory_path": "memory",
    }
    target.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print(f"OK: 注册 {target}")
    _print(f"     project_id={pid}  display_name={display_name}  repo_path={args.repo_path}")
    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    """plugins list: 列出已注册的 analyzer 插件 (name / version)。

    Phase 2 第一批只内置插件 (registry._discover_builtins 显式注册);列空属正常,
    内置插件 (builtin.sql / backend_spring / frontend_react / 分析器等) 注册后这里有行。
    """
    from codev_platform.plugins import list_plugins
    if args.action == "list":
        plugins = list_plugins()
        if not plugins:
            _print("(无已注册插件)")
            _print("提示: 内置插件 (如 builtin.sql / backend_spring) 经 registry._discover_builtins 注册。")
            return 0
        width = max(len(p.name) for p in plugins)
        _print(f"{'name'.ljust(width)}  version")
        _print("-" * (width + 12))
        for p in plugins:
            _print(f"{p.name.ljust(width)}  {p.version}")
        return 0
    _eprint(f"unknown action: {args.action}")
    return 1


def cmd_graph(args: argparse.Namespace) -> int:
    """graph ingest: 跑适用插件把统一图谱产出灌进 per-project store。
       graph stats:  打印某 project 统一图谱 store 的聚合统计。
    """
    pid = args.project or resolve_local()
    if args.action == "ingest":
        from codev_platform.graph.ingest import ingest_project
        repo = Path(args.repo).resolve() if args.repo else Path.cwd()
        report = ingest_project(repo, pid)
        if not report.ingested:
            _print(f"(无插件入库 project={pid}; 无适用且成功的 analyzer)")
            return 0
        _print(f"已 ingest project={pid} repo={repo}")
        for name in report.ingested:
            s = report.summaries.get(name, {})
            _print(
                f"  {name}: nodes={s.get('nodes', 0)} edges={s.get('edges', 0)} "
                f"evidences={s.get('evidences', 0)} findings={s.get('findings', 0)}"
            )
        return 0
    if args.action == "stats":
        from codev_platform.graph.store import open_store, stats
        conn = open_store(pid)
        try:
            data = stats(conn)
        finally:
            conn.close()
        _print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if args.action == "audit":
        from codev_platform.graph.audit import audit_graph, render_markdown
        from codev_platform.graph.store import open_store
        if getattr(args, "all", False):
            # 门禁模式: 审计所有有本地 store 的 project, 任一结构 error → 非零退出。
            # 无 store(机器没建图谱)→ 优雅跳过(返回 0, 不阻断 push)。
            from codev_platform.core.paths import data_root
            from codev_platform.graph.audit import audit_all_stores
            agg = audit_all_stores(data_root() / "graph_store")
            if not agg["projects"]:
                _print("(无 graph store, 跳过 graph audit 门禁)")
                return 0
            for ap in agg["projects"]:
                report = agg["reports"][ap]
                mark = "OK clean" if report["clean"] else f"{report['error_count']} ERROR"
                _print(f"[{ap}] {mark} — nodes {report['totals']['nodes']} / "
                       f"edges {report['totals']['edges']}")
                if not report["clean"]:
                    _print(render_markdown(report))
            if agg["total_errors"]:
                _print(f"\n✗ graph audit 门禁失败: {agg['total_errors']} 个结构 error, 修复后再 push。")
            return 0 if agg["total_errors"] == 0 else 1
        conn = open_store(pid)
        try:
            report = audit_graph(conn, pid)
        finally:
            conn.close()
        if getattr(args, "json", False):
            _print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            _print(render_markdown(report))
        return 0 if report["clean"] else 1   # 有结构 error → 非零(可作 CI/pre-push gate)
    _eprint(f"unknown action: {args.action}")
    return 1


def cmd_version(args: argparse.Namespace) -> int:
    from codev_platform import __version__
    _print(f"codev-platform {__version__}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        out = validate(args.project_id)
    except ProjectIdError as exc:
        _eprint(f"INVALID: {exc!s}")
        return 1
    _print(f"OK: {out}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """起 web backend (FastAPI via uvicorn)。systemd 之外的本机/调试入口 (与 codev-web.service 同 app)。"""
    try:
        import uvicorn
    except ImportError:
        _eprint("缺 uvicorn: uv pip install --python <venv> uvicorn")
        return 1
    _print(f"启动 web backend: codev_platform.web.app:app @ {args.host}:{args.port}")
    uvicorn.run("codev_platform.web.app:app", host=args.host, port=args.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    from codev_platform import __version__
    p = argparse.ArgumentParser(prog="codev-platform", description="多项目 AI 工具栈 CLI")
    p.add_argument("--version", "-V", action="version", version=f"codev-platform {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_init = sub.add_parser("init", help="创建 <cwd>/.claude/project.json")
    sp_init.add_argument("project_id", nargs="?", help="不给则交互输入")
    sp_init.add_argument("--display-name", default=None)
    sp_init.add_argument("--force", action="store_true", help="覆盖已有文件")
    sp_init.set_defaults(func=cmd_init)

    sp_cur = sub.add_parser("current", help="打印当前 project_id + 来源")
    sp_cur.set_defaults(func=cmd_current)

    sp_ls = sub.add_parser("list-projects", aliases=["ls"], help="列出 platform-meta/projects/ 已注册项目")
    sp_ls.set_defaults(func=cmd_list_projects)

    sp_reg = sub.add_parser("register", aliases=["reg"], help="注册当前项目到 platform_meta (list-projects 可见)")
    sp_reg.add_argument("project_id", nargs="?", default=None, help="不给则读 cwd 的 .claude/project.json")
    sp_reg.add_argument("--display-name", default=None)
    sp_reg.add_argument("--repo-path", default=".", help="meta.json repo_path 字段 (默认 '.')")
    sp_reg.add_argument("--force", action="store_true", help="覆盖已有 meta.json")
    sp_reg.set_defaults(func=cmd_register)

    sp_val = sub.add_parser("validate", help="校验 project_id 格式")
    sp_val.add_argument("project_id")
    sp_val.set_defaults(func=cmd_validate)

    sp_sr = sub.add_parser("sync-rules", help="把 codev-platform/rules/ 拷到 <cwd>/.claude/rules/")
    sp_sr.add_argument("--dry-run", action="store_true", help="只列不写")
    sp_sr.set_defaults(func=cmd_sync_rules)

    sp_ss = sub.add_parser("sync-skills", help="把 codev-platform/skills/ 拷到 <cwd>/.claude/skills/")
    sp_ss.add_argument("--dry-run", action="store_true", help="只列不写")
    sp_ss.set_defaults(func=cmd_sync_skills)

    sp_sh = sub.add_parser("sync-hooks",
                           help="把 codev-platform/hooks/ 拷到 <cwd>/.claude/hooks/ + merge MCP-first 护栏到 settings.json")
    sp_sh.add_argument("--dry-run", action="store_true", help="只列不写")
    sp_sh.set_defaults(func=cmd_sync_hooks)

    sp_setup = sub.add_parser("setup", help="新机器一键接入 (跨平台): venv / 模型探测 + 写 config + MCP 接入指引")
    sp_setup.add_argument("--dry-run", action="store_true", help="只探测不写 config")
    sp_setup.add_argument("--auto", action="store_true", help="缺 venv 自动建 (uv 优先, 回退 python -m venv) + 装 .[runtime]")
    sp_setup.set_defaults(func=cmd_setup)

    sp_cfg = sub.add_parser("config", help="~/.codev-platform/config.json 管理")
    sp_cfg.add_argument("action", choices=["show", "init", "path", "doctor"],
                        help="show=打印当前 / init=写默认 / path=只打印文件位置 / doctor=打印+secret体检")
    sp_cfg.add_argument("--force", action="store_true", help="init 时覆盖已有文件")
    sp_cfg.add_argument("--redact", action="store_true", help="show/doctor 时掩码敏感字段值 (防截图/日志泄漏)")
    sp_cfg.set_defaults(func=cmd_config)

    sp_ver = sub.add_parser("version", help="打印版本号")
    sp_ver.set_defaults(func=cmd_version)

    sp_plg = sub.add_parser("plugins", help="analyzer 插件管理 (list 列出已注册插件)")
    sp_plg.add_argument("action", choices=["list"], help="list=列出已注册插件 name/version")
    sp_plg.set_defaults(func=cmd_plugins)

    sp_graph = sub.add_parser("graph", help="统一图谱 store (ingest 入库 / stats 统计 / audit 结构审计)")
    sp_graph.add_argument("action", choices=["ingest", "stats", "audit"],
                          help="ingest=跑适用插件灌入 / stats=打印统计 / audit=结构完整性审计(断链/串台/重复/低置信)")
    sp_graph.add_argument("--project", default=None, help="project_id (默认从 cwd 解析)")
    sp_graph.add_argument("--repo", default=None, help="ingest: 被分析的仓库根 (默认 cwd)")
    sp_graph.add_argument("--json", action="store_true", help="audit: 机器可读 JSON 输出")
    sp_graph.add_argument("--all", action="store_true",
                          help="audit: 审计所有有本地 store 的 project(门禁模式, 任一结构 error 非零退出)")
    sp_graph.set_defaults(func=cmd_graph)

    sp_dae = sub.add_parser("daemon", help="chroma daemon 生命周期 (status / stop)")
    sp_dae.add_argument("action", choices=["status", "stop"], help="status=查 /health / stop=按 pid 结束")
    sp_dae.set_defaults(func=cmd_daemon)

    sp_mcp = sub.add_parser("serve-mcp", help="平台 MCP 端点编排 (status 探测 / start 拉起 / install-systemd 装常驻服务)")
    sp_mcp.add_argument("action", choices=["status", "start", "install-systemd"],
                        help="status=探测 / start=幂等拉起 / install-systemd=按 config 生成 systemd unit(开机自起)")
    sp_mcp.add_argument("--user", default=None, help="install-systemd: 服务运行用户 (默认 SUDO_USER / 当前用户)")
    sp_mcp.add_argument("--wait", action="store_true",
                        help="start: 拉起后有界轮询直到全 OK 或超时 (退出码 0=全 OK, 非 0=有超时)")
    sp_mcp.add_argument("--timeout", type=int, default=60, help="start --wait: 轮询总超时秒数 (默认 60)")
    sp_mcp.set_defaults(func=cmd_serve_mcp)

    sp_web = sub.add_parser("web", help="web backend 服务 (serve 起 FastAPI codev_platform.web.app)")
    web_sub = sp_web.add_subparsers(dest="web_cmd", required=True)
    sp_web_serve = web_sub.add_parser("serve", help="起 web backend (uvicorn, 默认 127.0.0.1:18088)")
    sp_web_serve.add_argument("--host", default="127.0.0.1", help="绑定 host (0.0.0.0 对外须 token 模式)")
    sp_web_serve.add_argument("--port", type=int, default=18088, help="端口 (默认 18088)")
    sp_web_serve.set_defaults(func=cmd_web)

    sp_msrc = sub.add_parser("mcp-source", help="切换业务仓 .mcp.json 各 MCP 的源 (local 本机 / platform 服务器)")
    sp_msrc.add_argument("target", choices=["local", "platform"],
                         help="local=本机本地实例(working tree) / platform=平台基线服务器(HEAD/共享)")
    sp_msrc.add_argument("tools", nargs="*",
                         help="要切的 tool: docs / codegraph / graph / agent-memory (可多个); 不给=全部统一切")
    sp_msrc.add_argument("--repo", default=None, help="业务仓路径 (默认 cwd)")
    sp_msrc.set_defaults(func=cmd_mcp_source)

    # Cross-platform ops subcommands (health / reindex / post-commit / dirty-check /
    # install-hooks / wait-for-reindex). Each ops submodule self-registers; missing
    # ones are skipped so partial parallel work doesn't break the CLI.
    from codev_platform import ops
    ops.register_all(sub)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
