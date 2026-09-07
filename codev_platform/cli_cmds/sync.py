"""sync-rules / sync-skills 子命令 + 资源定位 —— 从 cli.py 拆出。

真值源资源 (rules / skills) 现位于包内 codev_platform/resources/ (relocate 进包,
wheel 也带得走)。_RULES_SRC / _SKILLS_SRC / _sync_dir 也被 setup_cmd 复用。
"""
from __future__ import annotations

import argparse
import json
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
_CODEX_COMPATIBLE_ROOTS = {
    "rules": frozenset({"ai-tools-mcp.md"}),
    "skills": frozenset({"ai-health", "git-commit", "update-local-ai"}),
}


def _sync_dir(
    src: Path,
    dst: Path,
    kind: str,
    dry_run: bool,
    *,
    only: list[str] | tuple[str, ...] | None = None,
) -> int:
    """复制 src 下所有文件到 dst (含 README.md), 返回处理文件数。"""
    if not src.is_dir():
        _eprint(f"FATAL: {kind} 源目录不存在: {src}")
        return -1
    selected_items: list[Path] = []
    for item in src.rglob("*"):
        if item.is_dir() or item.name == "README.md":
            continue
        rel = item.relative_to(src)
        if only and not any(
            rel.as_posix() == selected.replace("\\", "/").strip("/")
            or rel.parts[0] == selected.replace("\\", "/").strip("/")
            for selected in only
            if selected.strip("/\\")
        ):
            continue
        selected_items.append(item)
    if only and not selected_items:
        _eprint(f"FATAL: {kind} --only 未命中任何资源")
        return -1
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    n_copied = 0
    n_skipped = 0
    for item in selected_items:
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


def _resource_targets(kind: str, target: str) -> list[tuple[str, Path]]:
    root = Path.cwd()
    surfaces = ("codex", "claude") if target == "both" else (target,)
    return [(surface, root / f".{surface}" / kind) for surface in surfaces]


def _surface_only(kind: str, surface: str, requested: list[str] | None) -> list[str] | None:
    """Fail closed when a resource has not declared Codex surface compatibility."""
    if surface != "codex":
        return requested
    allowed = _CODEX_COMPATIBLE_ROOTS[kind]
    if requested:
        selected_roots = {
            item.replace("\\", "/").strip("/").split("/")[0]
            for item in requested
        }
        unsupported = sorted(selected_roots - allowed)
        if unsupported:
            _eprint(f"FATAL: {kind} 尚无 Codex surface 适配: {', '.join(unsupported)}")
            return None
        return requested
    return sorted(allowed)


def cmd_sync_rules(args: argparse.Namespace) -> int:
    """按目标 surface/资源名复制跨项目规则。"""
    ok = True
    target = getattr(args, "target", "claude")
    requested = getattr(args, "only", None)
    targets = _resource_targets("rules", target)
    selections = [(surface, dst, _surface_only("rules", surface, requested)) for surface, dst in targets]
    if any(surface == "codex" and only is None for surface, _dst, only in selections):
        return 1
    for _surface, dst, only in selections:
        _print(f"sync rules: {_RULES_SRC} -> {dst}")
        n = _sync_dir(
            _RULES_SRC,
            dst,
            "rules",
            args.dry_run,
            only=only,
        )
        ok = ok and n >= 0
    return 0 if ok else 1


def cmd_sync_skills(args: argparse.Namespace) -> int:
    """按目标 surface/资源名复制跨项目 skills。"""
    ok = True
    target = getattr(args, "target", "claude")
    requested = getattr(args, "only", None)
    targets = _resource_targets("skills", target)
    selections = [(surface, dst, _surface_only("skills", surface, requested)) for surface, dst in targets]
    if any(surface == "codex" and only is None for surface, _dst, only in selections):
        return 1
    for _surface, dst, only in selections:
        _print(f"sync skills: {_SKILLS_SRC} -> {dst}")
        n = _sync_dir(
            _SKILLS_SRC,
            dst,
            "skills",
            args.dry_run,
            only=only,
        )
        ok = ok and n >= 0
    return 0 if ok else 1


# sync-hooks: 比 rules/skills 多一层 —— 脚本纯复制, 但 settings.json 的 hook 注册要 merge
# (业务仓可能已有自己的 settings, 不能整覆盖; 幂等, 重复 sync 不叠加)。
_HOOKS_SRC = _resource_src("hooks")

# 要 merge 进业务仓 .claude/settings.json 的 PreToolUse(Grep) hook。command=node + args exec form:
# Claude Code 自带 node, 三平台 (macOS/Linux/Windows) 通吃, 不依赖 powershell/git-bash。
_GREP_HOOK = {
    "matcher": "Grep",
    "hooks": [{
        "type": "command",
        "command": "node",
        "args": ["${CLAUDE_PROJECT_DIR}/.claude/hooks/mcp-first-guard.js"],
    }],
}


def _merge_grep_hook(settings: dict) -> bool:
    """幂等把 MCP-first PreToolUse(Grep) hook 加进 settings dict。已存在则不动。返回是否改动。"""
    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    for entry in pre:
        if entry.get("matcher") != "Grep":
            continue
        for h in entry.get("hooks", []):
            if any("mcp-first-guard" in str(a) for a in h.get("args", [])):
                return False  # 已装, 幂等跳过
    pre.append(_GREP_HOOK)
    return True


def _apply_grep_hook(settings_path: Path, dry_run: bool) -> str | None:
    """读 settings.json + 幂等 merge MCP-first(Grep) hook + 写回。返回状态串;解析失败返 None。

    接 path 参数(不硬编 cwd)→ `cmd_sync_hooks`(cwd)与 `sync_resources_to`(onboard 的 repo)共用。"""
    settings: dict = {}
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _eprint(f"WARN: {settings_path} 解析失败, 跳过 hook merge: {exc!s}")
            return None
    changed = _merge_grep_hook(settings)
    if changed and not dry_run:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not changed:
        return "hook 已存在(幂等跳过)"
    return "[dry-run] hook 将加" if dry_run else "hook 已加"


def sync_resources_to(repo: Path, *, dry_run: bool = False) -> bool:
    """把 rules/skills/hooks 同步到 `<repo>/.claude/` + merge MCP-first hook(onboard 编排复用)。

    接 repo 参数(非 cwd)→ 与 onboard 的 `--repo` 对齐;复用 `_sync_dir`/`_apply_grep_hook` 不复制逻辑。
    任一源目录缺失返 False(软步, 调用方 warn 不阻断接入)。"""
    claude = repo / ".claude"
    ok = True
    for src, name in ((_RULES_SRC, "rules"), (_SKILLS_SRC, "skills"), (_HOOKS_SRC, "hooks")):
        if _sync_dir(src, claude / name, name, dry_run) < 0:
            ok = False
    state = _apply_grep_hook(claude / "settings.json", dry_run)
    if state:
        _print(f"  settings.json: {state}")
    return ok


def cmd_sync_hooks(args: argparse.Namespace) -> int:
    """复制 resources/hooks/ 脚本到 <cwd>/.claude/hooks/ + 幂等 merge MCP-first 护栏到
    <cwd>/.claude/settings.json (项目级, commit; 保留业务仓现有 settings 不覆盖)."""
    cwd = Path.cwd()
    dst_hooks = cwd / ".claude" / "hooks"
    _print(f"sync hooks: {_HOOKS_SRC} -> {dst_hooks}")
    n = _sync_dir(_HOOKS_SRC, dst_hooks, "hooks", args.dry_run)
    if n < 0:
        return 1
    state = _apply_grep_hook(cwd / ".claude" / "settings.json", args.dry_run)
    if state is None:
        return 1
    _print(f"  settings.json: {state}")
    return 0
