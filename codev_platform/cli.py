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
        return 0
    width_id = max(len(r[0]) for r in rows)
    width_name = max(len(r[1]) for r in rows)
    _print(f"{'project_id'.ljust(width_id)}  {'display_name'.ljust(width_name)}  repo_path")
    _print("-" * (width_id + width_name + 20))
    for pid, name, path in rows:
        _print(f"{pid.ljust(width_id)}  {name.ljust(width_name)}  {path}")
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
    """一键新机器接入: 探测三仓 + 自动装 venv + 自动下模型 + 写 config.

    流程:
      git clone <three repos> 到同一父目录
      cd codev-platform && pip install -e .
      codev-platform setup [--auto]   # auto 自动装 venv + 下载模型
      Claude Code 在任一仓打开即可
    """
    from codev_platform.core.config import (
        DEFAULTS, config_path, load_config, save_config,
    )
    codev_root = Path(__file__).resolve().parents[1]
    parent = codev_root.parent
    _print(f"codev-platform repo: {codev_root}")
    _print(f"parent dir:          {parent}")
    _print()

    # 1. 探测兄弟仓
    platform_repo = parent / "platform"
    widget_repo = parent / "codev-platform-widget"
    _print("=== step 1/4: 兄弟仓探测 ===")
    _print(f"  platform:                {'OK' if platform_repo.is_dir() else 'MISSING -- git clone <platform>.git'}")
    _print(f"  codev-platform-widget:   {'OK' if widget_repo.is_dir() else 'MISSING (可选)'}")
    if not platform_repo.is_dir():
        _eprint(f"FATAL: 必须先 git clone platform 仓到 {parent}")
        return 1
    _print()

    # 2. 探测 / 装 chroma venv
    venv_dir = platform_repo / "tools" / "chroma" / ".venv"
    venv_py = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    _print("=== step 2/4: chroma .venv ===")
    if venv_py.is_file():
        _print(f"  found: {venv_dir}")
        chroma_venv = str(venv_dir)
    elif args.auto:
        _print(f"  MISSING, auto-installing in {venv_dir.parent} ...")
        uv = _which("uv")
        if uv:
            rc = _run_subprocess([uv, "venv"], cwd=venv_dir.parent, label="uv venv")
            if rc == 0:
                req = venv_dir.parent / "requirements.txt"
                pyproject = venv_dir.parent / "pyproject.toml"
                if pyproject.is_file():
                    rc = _run_subprocess([uv, "sync"], cwd=venv_dir.parent, label="uv sync")
                elif req.is_file():
                    rc = _run_subprocess([uv, "pip", "install", "-r", str(req), "--python", str(venv_py)], label="uv pip install -r requirements.txt")
            chroma_venv = str(venv_dir) if venv_py.is_file() else None
        else:
            _eprint("  uv not found; 装 uv 后重试: https://github.com/astral-sh/uv")
            chroma_venv = None
    else:
        _print(f"  MISSING — 跑 codev-platform setup --auto 或手动:")
        _print(f"    cd {platform_repo / 'tools' / 'chroma'}")
        _print("    uv venv && uv sync")
        chroma_venv = None
    _print()

    # 3. 探测 / 下模型
    model_root_default = Path.home() / "models"
    embed_candidates = [
        Path(r"D:\models\Qwen3-Embedding-0.6B"),
        model_root_default / "Qwen3-Embedding-0.6B",
        platform_repo / "models" / "paraphrase-multilingual-MiniLM-L12-v2",
    ]
    embed_path = next((str(m) for m in embed_candidates if m.is_dir()), None)
    rer_candidates = [
        Path(r"D:\models\Qwen3-Reranker-0.6B"),
        model_root_default / "Qwen3-Reranker-0.6B",
    ]
    reranker_path = next((str(r) for r in rer_candidates if r.is_dir()), "")
    _print("=== step 3/4: 模型 ===")
    if embed_path:
        _print(f"  embed:    {embed_path}")
    elif args.auto and _which("huggingface-cli"):
        target = model_root_default / "Qwen3-Embedding-0.6B"
        rc = _run_subprocess([_which("huggingface-cli"), "download", "Qwen/Qwen3-Embedding-0.6B", "--local-dir", str(target)], label=f"download Qwen3-Embedding -> {target}")
        if rc == 0 and target.is_dir():
            embed_path = str(target)
    else:
        _print("  MISSING embed (~1GB), 跑 --auto 或:")
        _print(f"    huggingface-cli download Qwen/Qwen3-Embedding-0.6B --local-dir {model_root_default / 'Qwen3-Embedding-0.6B'}")
    if reranker_path:
        _print(f"  reranker: {reranker_path}")
    elif args.auto and _which("huggingface-cli"):
        target = model_root_default / "Qwen3-Reranker-0.6B"
        rc = _run_subprocess([_which("huggingface-cli"), "download", "Qwen/Qwen3-Reranker-0.6B", "--local-dir", str(target)], label=f"download Qwen3-Reranker -> {target}")
        if rc == 0 and target.is_dir():
            reranker_path = str(target)
    else:
        _print("  MISSING reranker (可选), 跑 --auto 或:")
        _print(f"    huggingface-cli download Qwen/Qwen3-Reranker-0.6B --local-dir {model_root_default / 'Qwen3-Reranker-0.6B'}")
    _print()

    _print("=== step 4/4: 写 config ===")

    # 4. 写 config
    cfg = load_config()
    changed = False
    if chroma_venv and cfg.get("runtime", {}).get("chroma_venv") != chroma_venv:
        cfg.setdefault("runtime", {})["chroma_venv"] = chroma_venv
        changed = True
    if platform_repo.is_dir():
        data_dir = str(platform_repo / "data")
        if cfg.get("data", {}).get("platform_data_dir") != data_dir:
            cfg.setdefault("data", {})["platform_data_dir"] = data_dir
            changed = True
    if embed_path and cfg.get("models", {}).get("embed_path") != embed_path:
        cfg.setdefault("models", {})["embed_path"] = embed_path
        changed = True
    if reranker_path and cfg.get("models", {}).get("reranker_path") != reranker_path:
        cfg.setdefault("models", {})["reranker_path"] = reranker_path
        changed = True

    if changed and not args.dry_run:
        p = save_config(cfg)
        _print(f"=== 写入 config: {p} ===")
        _print("  runtime.chroma_venv / data.platform_data_dir / models.* 已更新")
    elif changed:
        _print("=== [dry-run] 会更新的字段 ===")
        _print(json.dumps({"runtime": cfg.get("runtime"), "data": cfg.get("data"), "models": cfg.get("models")}, indent=2, ensure_ascii=False))
    else:
        _print("=== config 无需更新 (探测值已 match) ===")

    _print()
    if chroma_venv and embed_path:
        _print("READY: 三仓 + venv + 模型齐备, 任一仓打开 Claude Code 即可")
        return 0
    else:
        _print("INCOMPLETE: 装 .venv / 下载模型后再跑一次 setup")
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
    p = argparse.ArgumentParser(prog="claude-platform", description="多项目 AI 工具栈 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_init = sub.add_parser("init", help="创建 <cwd>/.claude/project.json")
    sp_init.add_argument("project_id", nargs="?", help="不给则交互输入")
    sp_init.add_argument("--display-name", default=None)
    sp_init.add_argument("--force", action="store_true", help="覆盖已有文件")
    sp_init.set_defaults(func=cmd_init)

    sp_cur = sub.add_parser("current", help="打印当前 project_id + 来源")
    sp_cur.set_defaults(func=cmd_current)

    sp_ls = sub.add_parser("list-projects", help="列出 platform-meta/projects/ 已注册项目")
    sp_ls.set_defaults(func=cmd_list_projects)

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
    sp_setup.add_argument("--auto", action="store_true", help="缺 venv 自动 uv sync, 缺模型自动 huggingface-cli download")
    sp_setup.set_defaults(func=cmd_setup)

    sp_cfg = sub.add_parser("config", help="~/.codev-platform/config.json 管理")
    sp_cfg.add_argument("action", choices=["show", "init", "path"], help="show=打印当前 / init=写默认 / path=只打印文件位置")
    sp_cfg.add_argument("--force", action="store_true", help="init 时覆盖已有文件")
    sp_cfg.set_defaults(func=cmd_config)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
