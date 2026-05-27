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

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
