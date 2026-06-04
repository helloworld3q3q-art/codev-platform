"""sync-rules / sync-skills 子命令 + 资源定位 —— 从 cli.py 拆出。

真值源资源 (rules / skills) 现位于包内 codev_platform/resources/ (relocate 进包,
wheel 也带得走)。_RULES_SRC / _SKILLS_SRC / _sync_dir 也被 setup_cmd 复用。
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from codev_platform.cli_cmds._shared import _eprint, _print

# codev-platform 的 rules / skills 真值源 —— 现位于包内 codev_platform/resources/
# (relocate: 进包后 wheel 也带得走, 普通 pip 安装的 sync-rules/sync-skills 才能工作)。
_CODEV_PKG_ROOT = Path(__file__).resolve().parents[2]  # codev-platform/ (legacy 仓根 fallback)


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
