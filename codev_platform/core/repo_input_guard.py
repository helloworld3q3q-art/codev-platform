"""隔离索引的文件输入围栏：只允许冻结仓向量中的 Git tracked 文件。"""
from __future__ import annotations

import os
import subprocess
from fnmatch import fnmatch
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath

from codev_platform.core.process_tree import run_tree

PROVEN_REINDEX_INPUT_ENV = "CODEV_REINDEX_PROVEN_INPUTS"
REINDEX_TARGET_COMMIT_ENV = "CODEV_REINDEX_TARGET_COMMIT"
_GIT_TIMEOUT_SEC = 15
_MAX_PATTERNS = 128
_MAX_PATTERN_CHARS = 1_024
_MAX_PATTERN_PARTS = 128
_NON_INTERACTIVE_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "SSH_ASKPASS_REQUIRE": "never",
}


def _canonical_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    normalized: list[Path] = []
    seen: set[Path] = set()
    for value in roots:
        try:
            root = Path(value).resolve(strict=True)
        except OSError:
            raise ValueError("索引输入仓根不可用") from None
        if not root.is_dir() or root in seen:
            raise ValueError("索引输入仓根无效或重复")
        normalized.append(root)
        seen.add(root)
    if not normalized:
        raise ValueError("索引输入仓向量为空")
    return tuple(normalized)


def _tracked_paths(root: Path) -> frozenset[str]:
    env = os.environ.copy()
    env.update(_NON_INTERACTIVE_GIT_ENV)
    try:
        completed = run_tree(
            ["git", "-C", str(root), "ls-files", "-z", "--cached"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_GIT_TIMEOUT_SEC,
            env=env,
            no_window=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("索引输入 tracked 清单不可用") from None
    if completed.returncode != 0 or type(completed.stdout) is not bytes:
        raise ValueError("索引输入 tracked 清单无效")
    try:
        entries = {
            os.fsdecode(raw).replace("\\", "/")
            for raw in completed.stdout.split(b"\0")
            if raw
        }
    except (UnicodeError, ValueError):
        raise ValueError("索引输入 tracked 路径无法解码") from None
    return frozenset(entries)


def _is_absolute_pattern(pattern: str) -> bool:
    return PurePosixPath(pattern.replace("\\", "/")).is_absolute() or (
        PureWindowsPath(pattern).is_absolute() or bool(PureWindowsPath(pattern).drive)
    )


def _glob_parts(value: str) -> tuple[str, ...]:
    return tuple(part for part in value.replace("\\", "/").split("/") if part not in {"", "."})


def _matches_glob(path_parts: tuple[str, ...], pattern_parts: tuple[str, ...]) -> bool:
    pending = [(0, 0)]
    visited: set[tuple[int, int]] = set()
    while pending:
        path_at, pattern_at = pending.pop()
        state = (path_at, pattern_at)
        if state in visited:
            continue
        visited.add(state)
        if pattern_at == len(pattern_parts):
            if path_at == len(path_parts):
                return True
            continue
        pattern = pattern_parts[pattern_at]
        if pattern == "**":
            pending.append((path_at, pattern_at + 1))
            if path_at < len(path_parts):
                pending.append((path_at + 1, pattern_at))
        elif path_at < len(path_parts) and fnmatch(path_parts[path_at], pattern):
            pending.append((path_at + 1, pattern_at + 1))
    return False


def _bounded_patterns(patterns: Iterable[str], label: str) -> tuple[str, ...]:
    values = tuple(patterns)
    if len(values) > _MAX_PATTERNS:
        raise ValueError(f"受证明 {label} 数量超过上限")
    for pattern in values:
        if (
            type(pattern) is not str
            or len(pattern) > _MAX_PATTERN_CHARS
            or len(_glob_parts(pattern)) > _MAX_PATTERN_PARTS
        ):
            raise ValueError(f"受证明 {label} 长度或组件数无效")
    return values


class TrackedRepoInputGuard:
    """批量缓存 Git tracked 清单并验证索引器即将读取的路径。"""

    def __init__(self, roots: Iterable[Path]) -> None:
        self._roots = _canonical_roots(roots)
        self._tracked = {root: _tracked_paths(root) for root in self._roots}

    @property
    def main_root(self) -> Path:
        """冻结向量第一项是唯一主仓。"""
        return self._roots[0]

    def validate_main_root(self, root: Path) -> Path:
        try:
            actual = Path(root).resolve(strict=True)
        except OSError:
            raise ValueError("隔离 reindex 主仓不可用") from None
        if actual != self.main_root:
            raise ValueError("隔离 reindex --repo 与冻结主仓不一致")
        return actual

    def validate_doc_patterns(self, patterns: Iterable[str]) -> None:
        for pattern in _bounded_patterns(patterns, "doc_patterns"):
            if (
                type(pattern) is not str
                or not pattern
                or "\x00" in pattern
                or _is_absolute_pattern(pattern)
                or ".." in PurePosixPath(pattern.replace("\\", "/")).parts
            ):
                raise ValueError("受证明 doc_pattern 必须是仓内安全相对 glob")

    def validate_external_patterns(self, patterns: Iterable[str]) -> None:
        for pattern in _bounded_patterns(patterns, "external_doc_paths"):
            if type(pattern) is not str or not pattern or "\x00" in pattern:
                raise ValueError("受证明 external_doc_paths 格式无效")
            if _is_absolute_pattern(pattern):
                raise ValueError("受证明 external_doc_paths 禁止绝对路径")

    def _validate_search_anchor(self, anchor: Path, pattern: str) -> None:
        parts = _glob_parts(pattern)
        wildcard_at = next(
            (index for index, part in enumerate(parts) if any(char in part for char in "*?[")),
            len(parts),
        )
        if ".." in parts[wildcard_at:]:
            raise ValueError("受证明 external glob 禁止在通配符后回退目录")
        prefix = anchor.joinpath(*parts[:wildcard_at])
        try:
            resolved = prefix.resolve(strict=False)
        except OSError:
            raise ValueError("受证明 external glob 搜索锚无效") from None
        if not any(resolved == root or resolved.is_relative_to(root) for root in self._roots):
            raise ValueError("受证明 external glob 搜索锚逃逸冻结仓根")

    def _tracked_matches(
        self,
        *,
        anchor: Path,
        patterns: Iterable[str],
        roots: Iterable[Path],
    ) -> tuple[Path, ...]:
        normalized_patterns = tuple(_glob_parts(pattern) for pattern in patterns)
        if not normalized_patterns:
            return ()
        matched: set[Path] = set()
        for root in roots:
            for relative in self._tracked[root]:
                candidate = root / Path(*relative.split("/"))
                try:
                    anchored = os.path.relpath(candidate, anchor).replace("\\", "/")
                except ValueError:
                    continue
                if any(_matches_glob(_glob_parts(anchored), pattern) for pattern in normalized_patterns):
                    matched.add(candidate)
        return self.validate_files(sorted(matched))

    def match_main_files(self, patterns: Iterable[str]) -> tuple[Path, ...]:
        values = tuple(patterns)
        self.validate_doc_patterns(values)
        return self._tracked_matches(
            anchor=self.main_root,
            patterns=values,
            roots=(self.main_root,),
        )

    def match_external_files(
        self,
        anchor: Path,
        patterns: Iterable[str],
    ) -> tuple[Path, ...]:
        actual_anchor = self.validate_main_root(anchor)
        values = tuple(patterns)
        self.validate_external_patterns(values)
        for pattern in values:
            self._validate_search_anchor(actual_anchor, pattern)
        return self._tracked_matches(
            anchor=actual_anchor,
            patterns=values,
            roots=self._roots,
        )

    def validate_files(self, paths: Iterable[Path]) -> tuple[Path, ...]:
        validated: list[Path] = []
        for value in paths:
            try:
                path = Path(value).resolve(strict=True)
            except OSError:
                raise ValueError("索引输入文件不可用") from None
            if not path.is_file():
                raise ValueError("索引输入不是普通文件")
            owners = [root for root in self._roots if path.is_relative_to(root)]
            if not owners:
                raise ValueError("索引输入逃逸冻结仓根或经过不安全符号链接")
            root = max(owners, key=lambda item: len(item.parts))
            relative = path.relative_to(root).as_posix()
            if relative not in self._tracked[root]:
                raise ValueError("索引输入文件不是 Git tracked 内容")
            validated.append(path)
        return tuple(validated)


def proven_repo_input_guard() -> TrackedRepoInputGuard | None:
    """仅在隔离 runner 显式启用时，从严格仓 override 构造输入围栏。"""
    marker = os.environ.get(PROVEN_REINDEX_INPUT_ENV)
    if marker is None:
        return None
    if marker != "1":
        raise ValueError("受证明索引输入门禁标志无效")
    from codev_platform.core.config import REINDEX_CONFIG_DIGEST_ENV, load_config
    from codev_platform.core.project_id import validate
    from codev_platform.core.repo_runtime_override import REINDEX_REPO_OVERRIDE_ENV
    from codev_platform.core.repos import project_repo_specs

    if (
        os.environ.get(REINDEX_CONFIG_DIGEST_ENV) is None
        or os.environ.get(REINDEX_REPO_OVERRIDE_ENV) is None
    ):
        raise ValueError("受证明索引缺少配置摘要或冻结仓向量")
    raw_project_id = os.environ.get("PLATFORM_PROJECT_ID")
    if type(raw_project_id) is not str:
        raise ValueError("受证明索引项目身份缺失")
    try:
        project_id = validate(raw_project_id)
    except Exception:
        raise ValueError("受证明索引项目身份无效") from None
    if project_id != raw_project_id:
        raise ValueError("受证明索引项目身份必须是规范值")
    cfg = load_config()
    specs = project_repo_specs(project_id, cfg=cfg)
    return TrackedRepoInputGuard(spec.root for spec in specs)


__all__ = [
    "PROVEN_REINDEX_INPUT_ENV",
    "REINDEX_TARGET_COMMIT_ENV",
    "TrackedRepoInputGuard",
    "proven_repo_input_guard",
]
