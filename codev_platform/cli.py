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


# codev-platform 仓内的 rules / skills 源
_CODEV_PKG_ROOT = Path(__file__).resolve().parent.parent  # codev-platform/
_RULES_SRC = _CODEV_PKG_ROOT / "rules"
_SKILLS_SRC = _CODEV_PKG_ROOT / "skills"


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
    """一键新机器接入: preflight + 探测三仓 + 装 venv + 模型 detect + 写 config + sync.

    流程:
      git clone <three repos> 到同一父目录
      cd codev-platform && pip install -e .
      codev-platform setup [--auto]   # auto 自动 uv sync venv + pip install -e + sync-rules/skills
      Claude Code 在任一仓打开即可

    模型: 自备放 ~/models/Qwen3-* (或仓内 MiniLM fallback), 本命令不下载。

    跨平台: 路径全走 pathlib + config.json, 零 Windows 盘符字面量。venv 在本仓 .venv
    (平台所有权翻正后 venv / data 归 codev-platform 仓自有)。
    """
    from codev_platform.core.config import load_config, save_config, get
    codev_root = Path(__file__).resolve().parents[1]
    parent = codev_root.parent
    missing: list[str] = []
    _print(f"codev-platform repo: {codev_root}")
    _print(f"parent dir:          {parent}")
    _print()

    # step 0: preflight — 必备外部命令 (无 huggingface, 模型自备)
    _print("=== step 0/5: preflight 外部命令 ===")
    need = {"uv": "https://astral.sh/uv/install.ps1 (irm ... | iex)",
            "claude": "Anthropic Claude Code CLI"}
    for cmd, hint in need.items():
        w = _which(cmd)
        if w:
            _print(f"  {cmd}: OK ({w})")
        else:
            _print(f"  {cmd}: MISSING — 装: {hint}")
            missing.append(cmd)
    _print()

    # step 1: 兄弟仓 + 同父目录校验
    platform_repo = parent / "platform"
    widget_repo = parent / "codev-platform-widget"
    _print("=== step 1/5: 兄弟仓 (必须同父目录) ===")
    _print(f"  期望布局:\n    {parent}/\n      platform/\n      codev-platform/   (本仓)\n      codev-platform-widget/  (可选)")
    _print(f"  platform:                {'OK' if platform_repo.is_dir() else 'MISSING'}")
    _print(f"  codev-platform-widget:   {'OK' if widget_repo.is_dir() else 'MISSING (可选, 仅 Tray UI)'}")
    if not platform_repo.is_dir():
        _eprint(f"FATAL: platform 仓不在 {parent}。三仓必须 clone 到同一父目录 (.mcp.json 用 ..\\platform 相对路径)。")
        _eprint(f"  cd {parent} && git clone <platform>.git")
        return 1
    _print()

    # step 2: codev-platform 本仓 .venv (平台所有权翻正后 venv 归本仓自有)
    venv_dir = codev_root / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    _print("=== step 2/5: codev-platform .venv ===")
    chroma_venv = None
    if venv_py.is_file():
        _print(f"  found: {venv_dir}")
        chroma_venv = str(venv_dir)
    elif args.auto and _which("uv"):
        _print("  MISSING, auto-installing (uv) ...")
        _run_subprocess(["uv", "venv"], cwd=codev_root, label="uv venv")
        req = codev_root / "requirements-runtime.txt"
        if req.is_file():
            _run_subprocess(["uv", "pip", "install", "-r", str(req), "--python", str(venv_py)], cwd=codev_root, label="uv pip install -r requirements-runtime.txt")
        chroma_venv = str(venv_dir) if venv_py.is_file() else None
    else:
        _print(f"  MISSING — setup --auto 自动装, 或手动: cd {codev_root} && uv venv && uv pip install -r requirements-runtime.txt")
        _print("    (torch CUDA 版需先单独装, 见 requirements-runtime.txt 顶部注释)")
    if not chroma_venv:
        missing.append("codev-venv")
    _print()

    # step 2.5: pip install -e codev-platform 进本仓 .venv (editable, 改源码即生效)
    if chroma_venv and venv_py.is_file():
        _print("=== step 2.5/5: codev-platform editable 装进 .venv ===")
        check = _run_subprocess([str(venv_py), "-c", "import codev_platform"], label="check codev_platform in venv")
        if check != 0:
            if args.auto and _which("uv"):
                _run_subprocess(["uv", "pip", "install", "-e", str(codev_root), "--python", str(venv_py)], label="uv pip install -e codev-platform")
            else:
                _print(f"  codev_platform 未装进 .venv — 跑: uv pip install -e {codev_root} --python {venv_py}")
                missing.append("codev_platform-in-venv")
        else:
            _print("  OK: codev_platform 已可在 .venv import")
        _print()

    # step 3: 模型 detect (自备, 不下载) — 零字面量, 优先 config, 再 ~/models, 再仓内 fallback
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
    _print("=== step 3/5: 模型 (自备, 本命令不下载) ===")
    if embed_path:
        _print(f"  embed:    {embed_path}")
    else:
        _print(f"  MISSING embed — 放模型到 {model_root / 'Qwen3-Embedding-0.6B'} (或仓内 MiniLM fallback 自动用)")
        missing.append("embed-model")
    _print(f"  reranker: {reranker_path or 'MISSING (可选, daemon 降级纯向量)'}")
    _print()

    # step 4: 写 config
    _print("=== step 4/5: 写 config ===")
    cfg = load_config()
    changed = False
    def _set(section, key, val):
        nonlocal changed
        if val and cfg.get(section, {}).get(key) != val:
            cfg.setdefault(section, {})[key] = val
            changed = True
    _set("runtime", "chroma_venv", chroma_venv)
    _set("data", "platform_data_dir", str(codev_root / "data"))
    _set("models", "embed_path", embed_path)
    _set("models", "reranker_path", reranker_path)
    if changed and not args.dry_run:
        p = save_config(cfg)
        _print(f"  写入 {p}")
    elif changed:
        _print("  [dry-run] 会更新: " + json.dumps({k: cfg.get(k) for k in ("runtime", "data", "models")}, ensure_ascii=False))
    else:
        _print("  config 已 match, 无需更新")
    _print()

    # step 5: sync rules + skills 到兄弟仓 (新人 P12)
    _print("=== step 5/5: sync rules + skills ===")
    if args.auto and not args.dry_run:
        # 含 codev_root 自身 (.claude/rules + skills gitignored, 需本地生成)
        for repo in [codev_root, platform_repo, widget_repo]:
            if not repo.is_dir():
                continue
            for kind, src in [("rules", _RULES_SRC), ("skills", _SKILLS_SRC)]:
                dst = repo / ".claude" / kind
                n = _sync_dir(src, dst, f"{repo.name}/{kind}", dry_run=False)
                if n >= 0:
                    _print(f"  {repo.name}/.claude/{kind}: synced")
    else:
        _print("  (--auto 时自动 sync; 手动跑 codev-platform sync-rules / sync-skills)")
    _print()

    # 收尾
    if not missing:
        _print(">>> READY <<< 三仓 + venv + codev_platform + 模型齐备, 任一仓开 Claude Code 即可")
        return 0
    _print(f">>> INCOMPLETE <<< 缺: {', '.join(missing)}")
    _print("  按上面对应 MISSING 行的命令补齐后, 再跑一次 codev-platform setup --auto")
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
        _print(f"# config file: {p}  (exists={p.is_file()})")
        _print(json.dumps(cfg, ensure_ascii=False, indent=2))
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

    sp_setup = sub.add_parser("setup", help="新机器一键接入: 探测三仓 / venv / 模型 + 写 config")
    sp_setup.add_argument("--dry-run", action="store_true", help="只探测不写 config")
    sp_setup.add_argument("--auto", action="store_true", help="缺 venv 自动 uv sync (模型不自动下载, 需自备或走 MiniLM fallback)")
    sp_setup.set_defaults(func=cmd_setup)

    sp_cfg = sub.add_parser("config", help="~/.codev-platform/config.json 管理")
    sp_cfg.add_argument("action", choices=["show", "init", "path"], help="show=打印当前 / init=写默认 / path=只打印文件位置")
    sp_cfg.add_argument("--force", action="store_true", help="init 时覆盖已有文件")
    sp_cfg.set_defaults(func=cmd_config)

    sp_ver = sub.add_parser("version", help="打印版本号")
    sp_ver.set_defaults(func=cmd_version)

    sp_dae = sub.add_parser("daemon", help="chroma daemon 生命周期 (status / stop)")
    sp_dae.add_argument("action", choices=["status", "stop"], help="status=查 /health / stop=按 pid 结束")
    sp_dae.set_defaults(func=cmd_daemon)

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
