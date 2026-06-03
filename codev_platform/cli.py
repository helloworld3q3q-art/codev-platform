"""codev-platform CLI - 多项目 AI 工具栈管理入口。

子命令:
    init           交互式创建 <cwd>/.claude/project.json
    current        打印当前解析到的 project_id (debug)
    list-projects  列出 platform_meta/projects/ 已注册项目
    validate       校验给定的 project_id 格式

用法:
    codev-platform <subcommand> [args]    # pip install -e . 后
    python -m codev_platform.cli <subcommand> [args]

后续 server 化时新增子命令: login / logout / switch / push-rules
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


# platform_meta 注册表路径: 默认仓内, 可由 CODEV_PLATFORM_META 覆盖
_DEFAULT_META = Path(__file__).resolve().parents[1] / "platform_meta" / "projects"
PLATFORM_META_PROJECTS = Path(os.environ.get("CODEV_PLATFORM_META", str(_DEFAULT_META)))


def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# key 名命中任一子串即视为敏感, 其值掩码 (大小写不敏感)。
_SECRET_KEY_HINTS = ("api_key", "apikey", "password", "passwd", "secret", "token", "dsn")


def _mask_value(key: str, value):
    """单个敏感值掩码。dsn 类只掩密码段 (复用 ops.backup._redact_dsn), 其余整体掩。

    保留前 4 位便于核对是哪把 key, 太短的整体掩。非字符串原样掩成 "***"。
    """
    if isinstance(value, str) and "dsn" in key.lower():
        from codev_platform.ops.backup import _redact_dsn
        return _redact_dsn(value)
    if not value:
        return value
    if isinstance(value, str) and len(value) > 8:
        return value[:4] + "***"
    return "***"


def redact_config(cfg):
    """纯函数: 深度遍历 dict/list, 把敏感 key 的值掩码后返回新结构, 不改原 cfg。

    敏感判定: key 名 (小写) 含 _SECRET_KEY_HINTS 任一子串。dsn 类只掩密码段。
    用于 config show/doctor --redact, 防截图 / 日志 / 备份泄漏明文 secret。
    """
    if isinstance(cfg, dict):
        out = {}
        for k, v in cfg.items():
            ks = str(k).lower()
            if any(h in ks for h in _SECRET_KEY_HINTS) and not isinstance(v, (dict, list)):
                out[k] = _mask_value(str(k), v)
            else:
                out[k] = redact_config(v)
        return out
    if isinstance(cfg, list):
        return [redact_config(it) for it in cfg]
    return cfg


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
    import os
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


def cmd_daemon(args: argparse.Namespace) -> int:
    """chroma daemon 生命周期: status (查 /health) / stop (按 pid 杀)。

    spawn 由业务项目首次 Claude session 经 launcher 自动完成, 不在此处 start —
    避免脱离 project_id 上下文起一个无主 daemon。stop 后下次 session 会重新拉起。
    """
    from codev_platform.chroma import launcher  # 复用 health/port 逻辑
    health = launcher._fetch_daemon_health()
    if args.action == "status":
        if health is None:
            _print(f"daemon: DOWN ({launcher.DAEMON_URL})")
            _print("提示: 打开业务项目任一 Claude Code 会话会自动拉起 daemon。")
            return 1
        proc = health.get("process") or {}
        _print(f"daemon: {health.get('status', '?').upper()} pid={proc.get('pid')} "
               f"uptime={proc.get('uptime_sec')}s rss={proc.get('rss_mb')}MiB")
        _print(f"  model={health.get('model')} reranker={health.get('reranker')} "
               f"sse_sessions={health.get('sse_sessions')}")
        for p in health.get("loaded_projects", []):
            _print(f"  [{p.get('project_id')}] chunks={p.get('chunks')} "
                   f"bm25={p.get('bm25')} last_indexed={p.get('last_indexed_at')}")
        return 0
    if args.action == "stop":
        if health is None:
            _print("daemon 未运行, 无需 stop。")
            return 0
        pid = (health.get("process") or {}).get("pid")
        if not pid:
            _eprint("daemon /health 未返回 pid (旧版 daemon?), 无法自动 stop。请手动结束进程。")
            return 1
        import subprocess
        if sys.platform == "win32":
            rc = subprocess.call(["taskkill", "/PID", str(pid), "/F"])
        else:
            rc = subprocess.call(["kill", str(pid)])
        _print(f"stop pid={pid} rc={rc}。下次 Claude session 会重新拉起。")
        return rc
    return 1


def cmd_serve_mcp(args: argparse.Namespace) -> int:
    """平台 MCP 端点编排: status (探测) / start (幂等拉起 cross-link + 各项目 codegraph)。

    chroma daemon 由业务仓 Claude 会话经 launcher 自 spawn, 本命令不拉起它, 只报状态。
    """
    from codev_platform.core.config import load_config
    from codev_platform import mcp_serve
    cfg = load_config()
    if args.action == "status":
        rows = mcp_serve.probe_all(cfg, diagnose=True)
        _print(f"{'endpoint'.ljust(22)} {'kind'.ljust(11)} {'port'.ljust(6)} status   reason / sse_url")
        _print("-" * 90)
        any_down = False
        for r in rows:
            mark = "OK  " if r["status"] == "ok" else "DOWN"
            if r["status"] != "ok" and not r["self_spawned"]:
                any_down = True
            tail = r["sse_url"] if r["status"] == "ok" else (r.get("reason") or r["sse_url"])
            _print(f"{r['name'].ljust(22)} {r['kind'].ljust(11)} {str(r['port']).ljust(6)} "
                   f"{mark}     {tail}")
        return 1 if any_down else 0
    if args.action == "start":
        results = mcp_serve.ensure_serving(cfg)
        for r in results:
            extra = r.get("error") or r.get("note") or ""
            pid = f" pid={r['pid']}" if r.get("pid") else ""
            _print(f"  {r['name'].ljust(22)} {r['action']}{pid}  {extra}")
        _print()
        if getattr(args, "wait", False):
            timeout = float(getattr(args, "timeout", 60) or 60)
            _print(f"等待端点就绪 (--wait, 超时 {int(timeout)}s) ...")
            waited = mcp_serve.wait_until_serving(cfg, timeout=timeout)
            any_timeout = False
            for w in waited:
                if w["status"] == "ok":
                    _print(f"  {w['name'].ljust(22)} OK")
                else:
                    any_timeout = True
                    _print(f"  {w['name'].ljust(22)} 超时  {w.get('reason') or ''}")
            return 1 if any_timeout else 0
        _print("提示: codegraph 端点需 ~2-5s 起来; 再跑 `codev-platform serve-mcp status` 确认。")
        return 0
    if args.action == "install-systemd":
        # 以普通用户跑: 按 config 生成 systemd unit(端口/路径都来自 iter_endpoints),
        # 写到 ~/codev-systemd/, 再打印唯一一条 sudo 命令装进 /etc/systemd/system 并 enable。
        # 不在此直接 sudo —— 普通用户跑能读对用户的 config(sudo 会切到 root 的 HOME/config)。
        import getpass
        user = getattr(args, "user", None) or os.environ.get("SUDO_USER") or getpass.getuser()
        r = mcp_serve.install_systemd(cfg, user)
        _print(f"生成 {len(r['units'])} 个 unit 到 {r['dir']} (User={user}):")
        for u in r["units"]:
            _print(f"  - {u}")
        _print()
        _print("装上 + 开机自起(enable),跑这一条(需 root):")
        _print(f"  {r['sudo_cmd']}")
        _print()
        _print("装完验证: systemctl is-active " + " ".join(s[:-8] for s in r["units"]))
        return 0
    _eprint(f"unknown action: {args.action}")
    return 1


def cmd_mcp_source(args: argparse.Namespace) -> int:
    """切换业务仓 .mcp.json 各 MCP 的源 (local 本机本地实例 / platform 平台基线服务器)。

    可统一切 (默认全部 3 套) 或只切指定 tool。host/port 来自 config.mcp_sources.<target>
    (缺省 local=18xxx / platform=19xxx)。project_id 取自 <repo>/.claude/project.json。
    """
    from codev_platform.core.config import load_config
    from codev_platform import mcp_serve
    repo = Path(args.repo).expanduser().resolve() if args.repo else Path.cwd()
    mcp_json = repo / ".mcp.json"
    if not mcp_json.is_file():
        _eprint(f"FATAL: 未找到 {mcp_json} (在业务仓根跑, 或 --repo 指定)")
        return 1
    pj = repo / CONFIG_RELPATH
    if not pj.is_file():
        _eprint(f"FATAL: 未找到 {pj} (先 codev-platform init)")
        return 1
    try:
        pid = validate(json.loads(pj.read_text(encoding="utf-8")).get("project_id"))
    except (json.JSONDecodeError, ProjectIdError) as exc:
        _eprint(f"FATAL: 解析 project_id 失败: {exc!s}")
        return 1

    alias = {
        "docs": "platform-docs", "chromadb": "platform-docs", "chroma": "platform-docs",
        "platform-docs": "platform-docs", "cross-link": "cross-link", "crosslink": "cross-link",
        "codegraph": "codegraph",
    }
    cfg = load_config()
    try:
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _eprint(f"FATAL: {mcp_json} 解析失败: {exc!s}")
        return 1
    servers = data.get("mcpServers", {})
    sel = [alias.get(t, t) for t in args.tools] if args.tools else list(mcp_serve.MCP_SOURCE_TOOLS)

    changed: list[tuple[str, str]] = []
    for tool in sel:
        if tool not in servers:
            _print(f"  跳过 {tool} (.mcp.json 无此项)")
            continue
        try:
            url = mcp_serve.mcp_source_url(cfg, args.target, tool, pid)
        except ValueError as exc:
            _eprint(f"FATAL: {exc!s}")
            return 1
        servers[tool]["type"] = "sse"
        servers[tool]["url"] = url
        changed.append((tool, url))
    if not changed:
        _print("无改动 (选中的 tool 都不在 .mcp.json)")
        return 0
    mcp_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print(f"OK: {mcp_json}  ->  源 = {args.target}  (project_id={pid})")
    for tool, url in changed:
        _print(f"  {tool.ljust(14)} {url}")
    _print()
    _print("提示: 重启 Claude Code 让新 .mcp.json 生效。")
    return 0


def cmd_plugins(args: argparse.Namespace) -> int:
    """plugins list: 列出已注册的 analyzer 插件 (name / version)。

    Phase 2 第一批只内置插件 (registry._discover_builtins 显式注册);列空属正常,
    下一步把 cross-link 适配器包成 builtin.cross_link 后这里就有行。
    """
    from codev_platform.plugins import list_plugins
    if args.action == "list":
        plugins = list_plugins()
        if not plugins:
            _print("(无已注册插件)")
            _print("提示: Phase 2 只落地 plugin runtime; 内置插件 (如 builtin.cross_link) 待后续接入。")
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


# codev-platform 的 rules / skills 真值源 —— 现位于包内 codev_platform/resources/
# (relocate: 进包后 wheel 也带得走, 普通 pip 安装的 sync-rules/sync-skills 才能工作)。
_CODEV_PKG_ROOT = Path(__file__).resolve().parent.parent  # codev-platform/ (legacy 仓根 fallback)


def _resource_src(name: str) -> Path:
    """定位真值源资源目录 (rules / skills)。

    优先包内 `codev_platform/resources/<name>`(importlib.resources —— editable 与 wheel 均在);
    找不到才回退仓根旧布局 `<repo>/<name>`(老 clone / 迁移期兜底)。
    """
    try:
        from importlib.resources import files as _res_files
        p = Path(str(_res_files("codev_platform") / "resources" / name))
        if p.is_dir():
            return p
    except Exception:  # noqa: BLE001 — 资源定位失败回退仓根, 不让 CLI 炸
        pass
    return _CODEV_PKG_ROOT / name


_RULES_SRC = _resource_src("rules")
_SKILLS_SRC = _resource_src("skills")


def _sync_dir(src: Path, dst: Path, kind: str, dry_run: bool) -> int:
    """复制 src 下所有文件到 dst (含 README.md), 返回处理文件数。"""
    if not src.is_dir():
        _eprint(f"FATAL: {kind} 源目录不存在: {src}")
        return -1
    dst.mkdir(parents=True, exist_ok=True)
    import shutil
    n_copied = 0
    n_skipped = 0
    for item in src.rglob("*"):
        if item.is_dir():
            continue
        # 排除 README.md (codev-platform 仓自身 doc, 不是业务仓需要的规则/skill)
        if item.name == "README.md":
            continue
        rel = item.relative_to(src)
        target = dst / rel
        if target.exists() and target.read_bytes() == item.read_bytes():
            n_skipped += 1
            continue
        if dry_run:
            _print(f"  [dry-run] {rel}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
        n_copied += 1
    _print(f"  {kind}: {n_copied} files synced, {n_skipped} unchanged")
    return n_copied


def cmd_sync_rules(args: argparse.Namespace) -> int:
    """复制 codev-platform/rules/ 到 <cwd>/.claude/rules/ (业务仓内)."""
    dst = Path.cwd() / ".claude" / "rules"
    _print(f"sync rules: {_RULES_SRC} -> {dst}")
    n = _sync_dir(_RULES_SRC, dst, "rules", args.dry_run)
    return 0 if n >= 0 else 1


def cmd_sync_skills(args: argparse.Namespace) -> int:
    """复制 codev-platform/skills/ 到 <cwd>/.claude/skills/ (业务仓内)."""
    dst = Path.cwd() / ".claude" / "skills"
    _print(f"sync skills: {_SKILLS_SRC} -> {dst}")
    n = _sync_dir(_SKILLS_SRC, dst, "skills", args.dry_run)
    return 0 if n >= 0 else 1


def _which(cmd: str) -> str | None:
    """跨平台查 PATH 里有没有该命令, 返回绝对路径或 None."""
    import shutil
    return shutil.which(cmd)


def _run_subprocess(cmd: list[str], cwd: Path | None = None, label: str = "") -> int:
    """跑子进程 + 实时打印, 返回 exit code."""
    import subprocess
    _print(f"  → {label or ' '.join(cmd)}")
    try:
        rc = subprocess.call(cmd, cwd=str(cwd) if cwd else None)
        return rc
    except FileNotFoundError as exc:
        _eprint(f"    FAIL: {exc!s}")
        return 127


def cmd_setup(args: argparse.Namespace) -> int:
    """一键新机器接入 (自包含布局, 跨平台): preflight + venv + 模型 detect + 写 config + MCP 接入指引 + sync.

    布局 (2026-05-28 所有权翻正后): venv / data / 模型 config 全围绕本仓
    (codev-platform/.venv, codev-platform/data), 无需兄弟仓即可起 chroma 检索。

    模型自备 (放 ~/models/Qwen3-*); 路径走 config + pathlib (零盘符字面量); 本命令探测 + 给下载命令, 不下重物。
    Mac/Linux: 打印 .mcp.json 接入指引 (CLI 不替你改 shell profile)。
    """
    from codev_platform.core.config import load_config, save_config, get
    import platform as _platform
    codev_root = Path(__file__).resolve().parents[1]
    is_win = sys.platform == "win32"
    is_mac = sys.platform == "darwin"
    missing: list[str] = []
    _print(f"codev-platform repo: {codev_root}")
    _print(f"platform:            {sys.platform} / {_platform.machine()}")
    _print()

    # step 0/5: preflight 外部命令
    _print("=== step 0/5: preflight 外部命令 ===")
    if _which("claude"):
        _print(f"  claude: OK ({_which('claude')})")
    else:
        _print("  claude: MISSING — 装 Anthropic Claude Code CLI")
        missing.append("claude")
    if args.auto and not _which("uv"):
        _print("  uv: MISSING (--auto 建 venv 时优先用; 缺则回退 python -m venv) — https://astral.sh/uv")
    _print()

    # step 1/5: 仓内 .venv (self-contained, 无需兄弟仓)
    venv_dir = codev_root / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if is_win else "bin/python")
    _print("=== step 1/5: 仓内 .venv ===")
    if venv_py.is_file():
        _print(f"  found: {venv_dir}")
    elif args.auto:
        _print("  MISSING, auto-installing ...")
        if _which("uv"):
            _run_subprocess(["uv", "venv"], cwd=codev_root, label="uv venv")
            _run_subprocess(["uv", "pip", "install", "-e", ".[runtime]", "--python", str(venv_py)], cwd=codev_root, label="uv pip install -e .[runtime]")
        else:
            _run_subprocess([sys.executable, "-m", "venv", str(venv_dir)], label="python -m venv .venv")
            _run_subprocess([str(venv_py), "-m", "pip", "install", "-e", ".[runtime]"], cwd=codev_root, label="pip install -e .[runtime]")
    else:
        _print("  MISSING — 跑: python3 -m venv .venv && .venv/bin/pip install -e '.[runtime]'  (或 setup --auto)")
    if not venv_py.is_file():
        missing.append("venv")
    _print()

    # step 2/5: codev_platform 可在 venv import
    if venv_py.is_file():
        _print("=== step 2/5: codev_platform import 检查 ===")
        if _run_subprocess([str(venv_py), "-c", "import codev_platform"], label="import codev_platform") != 0:
            if args.auto:
                _run_subprocess([str(venv_py), "-m", "pip", "install", "-e", str(codev_root)], label="pip install -e codev-platform")
            else:
                _print(f"  未装 — 跑: {venv_py} -m pip install -e {codev_root}")
                missing.append("codev_platform-in-venv")
        else:
            _print("  OK")
        _print()

    # step 3/5: 模型 detect (config 驱动, 跨平台, 不下载) — 优先 config, 再 ~/models, 再仓内 fallback
    cfg_pre = load_config()
    model_root = Path.home() / "models"
    cfg_embed = get(cfg_pre, "models.embed_path")
    cfg_reranker = get(cfg_pre, "models.reranker_path")
    embed_path = next((str(m) for m in [
        Path(cfg_embed).expanduser() if cfg_embed else None,
        model_root / "Qwen3-Embedding-0.6B",
        codev_root / "models" / "paraphrase-multilingual-MiniLM-L12-v2",
    ] if m and m.is_dir()), None)
    reranker_path = next((str(r) for r in [
        Path(cfg_reranker).expanduser() if cfg_reranker else None,
        model_root / "Qwen3-Reranker-0.6B",
    ] if r and r.is_dir()), "")
    _print("=== step 3/5: 模型 (自备, 不下载) ===")
    if embed_path:
        _print(f"  embed:    {embed_path}")
    else:
        _print(f"  MISSING embed — 下: hf download Qwen/Qwen3-Embedding-0.6B --local-dir {model_root}/Qwen3-Embedding-0.6B")
        missing.append("embed-model")
    _print(f"  reranker: {reranker_path or 'MISSING (可选, 关则纯向量+BM25)'}")
    _print()

    # step 4/5: 写 config (device 按平台探测; data 走仓内默认 null)
    if is_mac:
        device = "mps" if _platform.machine() == "arm64" else "cpu"
    elif is_win:
        device = "cuda"  # NVIDIA 默认; 无 N 卡手动改 cpu
    else:
        device = "cpu"  # linux 默认; 有 N 卡手动改 cuda
    _print(f"=== step 4/5: 写 config (embed_device={device}) ===")
    cfg = load_config()
    m = cfg.setdefault("models", {})
    rt = cfg.setdefault("runtime", {})
    before = json.dumps({"models": dict(m), "runtime": dict(rt)}, ensure_ascii=False, sort_keys=True)
    if embed_path:
        m["embed_path"] = embed_path
    m["embed_device"] = device
    m["reranker_path"] = reranker_path
    m["reranker_enabled"] = bool(reranker_path)
    if venv_py.is_file():
        rt["chroma_venv"] = str(venv_dir)
    changed = json.dumps({"models": dict(m), "runtime": dict(rt)}, ensure_ascii=False, sort_keys=True) != before
    if changed and not args.dry_run:
        p = save_config(cfg)
        _print(f"  写入 {p}")
    elif changed:
        _print("  [dry-run] 会更新 models/runtime: " + json.dumps({"models": dict(m), "runtime": dict(rt)}, ensure_ascii=False))
    else:
        _print("  config 已 match, 无需更新")
    _print()

    # step 5/5: sync rules/skills 到本仓 + MCP 接入指引
    _print("=== step 5/5: sync rules/skills + MCP 接入 ===")
    if args.auto and not args.dry_run:
        for kind, src in [("rules", _RULES_SRC), ("skills", _SKILLS_SRC)]:
            dst = codev_root / ".claude" / kind
            if _sync_dir(src, dst, f"codev-platform/{kind}", dry_run=False) >= 0:
                _print(f"  .claude/{kind}: synced")
    else:
        _print("  (--auto 时自动 sync; 或手动 codev-platform sync-rules / sync-skills)")
    if is_win:
        _print("  Windows: .mcp.json 默认即 cmd /c daemon launcher, 无需额外 env。")
    elif is_mac:
        # .mcp.json 的 ${VAR} 展开取自 VSCode 扩展宿主 process.env。Dock/Finder 启动的 VSCode
        # 不读 ~/.zshrc(只继承 launchd 环境) → 必须用 launchctl(GUI 可见)+ LaunchAgent 持久化。
        import subprocess as _sp
        try:
            cur = _sp.check_output(["launchctl", "getenv", "PLATFORM_MCP_CHROMA"], text=True).strip()
        except Exception:  # noqa: BLE001
            cur = ""
        if cur:
            _print("  Mac MCP env: OK (launchctl 已设 PLATFORM_MCP_CHROMA)")
        else:
            _print("  Mac 接 Claude Code: 用 launchctl 设 4 个 env (别用 ~/.zshrc — Dock 启动的 VSCode 读不到)。")
            _print("  跑 docs/onboarding-mac.md 『接入 Claude Code』那段 (写 LaunchAgent + launchctl setenv), 再 Cmd+Q 重启 VSCode。")
    else:
        _print("  Linux 接 Claude Code: 在登录环境 export PLATFORM_MCP_SH=sh / FLAG=-c / CHROMA / CROSSLINK (见 docs/onboarding-mac.md), 或从终端启动 IDE。")
    _print()

    # 收尾
    if not missing:
        _print(">>> READY <<< venv + codev_platform + 模型 + config 齐备")
        if not is_win:
            _print("  最后: 建索引 (python -m codev_platform.chroma.indexer --force) + 配 MCP env (launchctl, 见 docs/onboarding-mac.md) + 重启 VSCode")
        return 0
    _print(f">>> INCOMPLETE <<< 缺: {', '.join(missing)}")
    _print("  按上面 MISSING 行补齐后, 再跑 codev-platform setup --auto")
    return 1


def cmd_config(args: argparse.Namespace) -> int:
    """show / init / path: ~/.codev-platform/config.json 管理."""
    from codev_platform.core.config import (
        DEFAULTS, config_path, load_config, save_config,
    )
    p = config_path()
    if args.action == "path":
        _print(str(p))
        return 0
    if args.action == "show":
        cfg = load_config()
        if getattr(args, "redact", False):
            cfg = redact_config(cfg)
        _print(f"# config file: {p}  (exists={p.is_file()})")
        _print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0
    if args.action == "doctor":
        cfg = load_config()
        shown = redact_config(cfg) if getattr(args, "redact", False) else cfg
        _print(f"# config file: {p}  (exists={p.is_file()})")
        _print(json.dumps(shown, ensure_ascii=False, indent=2))
        # provider api_key 体检: 只报 有/无, 绝不打印值
        providers = {}
        agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
        if isinstance(agent.get("providers"), dict):
            providers = agent["providers"]
        _print()
        _print("=== provider api_key 体检 (只报有/无, 不打印值) ===")
        if not providers:
            _print("  (无 agent.providers 配置)")
        else:
            for name, spec in providers.items():
                has = bool(isinstance(spec, dict) and spec.get("api_key"))
                _print(f"  - {name}: api_key {'有' if has else '无'}")
            _print("  建议: 定期轮换 key, 并优先用 env 引用 (env > config.agent.providers.<name>.api_key), 真 key 不进 git。")
        return 0
    if args.action == "init":
        if p.is_file() and not args.force:
            _eprint(f"已存在: {p} (加 --force 覆盖)")
            return 1
        save_config(DEFAULTS, p)
        _print(f"OK: 写入默认 config 到 {p}")
        _print("编辑后修改本机路径 (D:/models/... / data_dir 等), 或 env 临时覆盖。")
        return 0
    _eprint(f"unknown action: {args.action}")
    return 1


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

    sp_graph = sub.add_parser("graph", help="统一图谱 store (ingest 跑插件入库 / stats 统计)")
    sp_graph.add_argument("action", choices=["ingest", "stats"],
                          help="ingest=跑适用插件灌入 per-project store / stats=打印统计")
    sp_graph.add_argument("--project", default=None, help="project_id (默认从 cwd 解析)")
    sp_graph.add_argument("--repo", default=None, help="ingest: 被分析的仓库根 (默认 cwd)")
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
                         help="要切的 tool: docs / cross-link / codegraph (可多个); 不给=全部 3 套统一切")
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
