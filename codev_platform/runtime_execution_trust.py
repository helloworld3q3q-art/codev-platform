"""在执行版本化运行时 Python 前证明路径所有权、权限与链接边界。"""

from __future__ import annotations

import os
from pathlib import Path
import stat


class RuntimeExecutionTrustError(RuntimeError):
    """待执行运行时仍可被非特权主体替换。"""


def verify_root_controlled_executable(path: Path) -> Path:
    """返回解释器的规范路径，并证明它不能被非 root 主体替换。"""
    try:
        selected = _absolute_lexical(path)
        resolved = selected.resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise RuntimeExecutionTrustError("root 控制解释器不可执行")
        if os.name == "posix":
            _require_posix_trusted_file_target(resolved)
        else:
            if selected != resolved or _is_linklike(selected):
                raise RuntimeExecutionTrustError("root 控制解释器包含不可信链接")
            _require_component_chain(Path(resolved.anchor), resolved, allow_leaf_symlink=False)
    except RuntimeExecutionTrustError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RuntimeExecutionTrustError("root 控制解释器路径不受信任") from None
    return resolved


def verify_execution_trust(
    runtime_root: Path,
    object_root: Path,
    managed_paths: tuple[tuple[Path, bool], ...],
) -> None:
    """静态验证受管对象；布尔值仅允许 POSIX 解释器叶子是可信链接。"""
    runtime = _absolute_lexical(runtime_root)
    managed = _absolute_lexical(object_root)
    _require_component_chain(Path(managed.anchor), managed, allow_leaf_symlink=False)
    try:
        managed.relative_to(runtime)
    except ValueError:
        raise RuntimeExecutionTrustError("运行时对象逃逸受管根") from None
    for path, allow_posix_leaf_symlink in managed_paths:
        _require_component_chain(
            managed,
            path,
            allow_leaf_symlink=os.name == "posix" and allow_posix_leaf_symlink,
        )
    if os.name == "posix":
        _require_posix_ancestor_trust(managed)
        _require_posix_tree_trust(managed, trusted_root=runtime)
    else:
        _require_reparse_free_tree(managed)


def verify_execution_trust_from_cwd(
    object_root: Path,
    managed_paths: tuple[tuple[Path, bool], ...],
) -> None:
    """在 root-fd worker 的继承 cwd 内复验执行路径，不重开 runtime 根。"""
    if os.name != "posix":
        raise RuntimeExecutionTrustError("cwd 执行信任仅支持 POSIX")
    managed = _require_cwd_relative_path(object_root, label="运行时对象")
    _require_posix_entry(Path("."), directory=True)
    _require_cwd_directory_chain(managed)
    for path, allow_posix_leaf_symlink in managed_paths:
        candidate = _require_cwd_relative_path(path, label="运行时执行路径")
        try:
            relative = candidate.relative_to(managed)
        except ValueError:
            raise RuntimeExecutionTrustError("运行时执行路径越界") from None
        _require_cwd_managed_path(
            managed,
            relative,
            allow_leaf_symlink=allow_posix_leaf_symlink,
        )
    _require_cwd_tree_trust(managed)


def _require_component_chain(root: Path, path: Path, *, allow_leaf_symlink: bool) -> None:
    anchor = _absolute_lexical(root)
    target = _absolute_lexical(path)
    try:
        relative = target.relative_to(anchor)
    except ValueError:
        raise RuntimeExecutionTrustError("运行时执行路径越界") from None
    current = anchor
    components = relative.parts or (".",)
    for index, component in enumerate(components):
        current = current if component == "." else current / component
        leaf = index == len(components) - 1
        if _is_linklike(current) and (not leaf or not allow_leaf_symlink):
            raise RuntimeExecutionTrustError("运行时执行路径包含不可信链接")
        if not leaf and not current.is_dir():
            raise RuntimeExecutionTrustError("运行时执行路径中间组件无效")


def _require_cwd_relative_path(path: Path, *, label: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or not candidate.parts:
        raise RuntimeExecutionTrustError(f"{label}必须是安全相对路径")
    if any(part in {"", ".", ".."} or "\x00" in part for part in candidate.parts):
        raise RuntimeExecutionTrustError(f"{label}必须是安全相对路径")
    return candidate


def _require_cwd_directory_chain(path: Path) -> None:
    current = Path(".")
    _require_posix_entry(current, directory=True)
    for component in path.parts:
        current /= component
        if _is_linklike(current):
            raise RuntimeExecutionTrustError("运行时执行路径包含不可信链接")
        _require_posix_entry(current, directory=True)


def _require_cwd_managed_path(
    object_root: Path,
    relative: Path,
    *,
    allow_leaf_symlink: bool,
) -> None:
    if not relative.parts:
        raise RuntimeExecutionTrustError("运行时执行路径必须包含受管叶子")
    current = object_root
    for index, component in enumerate(relative.parts):
        current /= component
        leaf = index == len(relative.parts) - 1
        if _is_linklike(current):
            if leaf and allow_leaf_symlink:
                _require_cwd_executable_link(current, object_root, depth=0)
                return
            raise RuntimeExecutionTrustError("运行时执行路径包含不可信链接")
        if leaf:
            _require_posix_entry(current, directory=current.is_dir())
            if allow_leaf_symlink and (not current.is_file() or not os.access(current, os.X_OK)):
                raise RuntimeExecutionTrustError("root 控制解释器不可执行")
        else:
            _require_posix_entry(current, directory=True)


def _require_cwd_tree_trust(object_root: Path) -> None:
    """在继承 cwd 内复现绝对入口的对象树权限与链接目标证明。"""
    pending = [object_root]
    while pending:
        current = pending.pop()
        try:
            metadata = current.lstat()
        except OSError:
            raise RuntimeExecutionTrustError("运行时目录树成员不存在") from None
        if stat.S_ISLNK(metadata.st_mode):
            _require_cwd_tree_symlink(object_root, current, metadata)
            continue
        _require_posix_entry(current, directory=stat.S_ISDIR(metadata.st_mode))
        if stat.S_ISDIR(metadata.st_mode):
            try:
                with os.scandir(current) as entries:
                    pending.extend(current / entry.name for entry in entries)
            except OSError:
                raise RuntimeExecutionTrustError("运行时目录树无法完整扫描") from None
        elif not stat.S_ISREG(metadata.st_mode):
            raise RuntimeExecutionTrustError("运行时目录树包含非普通成员")


def _require_cwd_tree_symlink(
    object_root: Path,
    link: Path,
    metadata: os.stat_result,
    *,
    depth: int = 0,
    visited: frozenset[tuple[str, ...]] | None = None,
) -> None:
    if depth >= 8:
        raise RuntimeExecutionTrustError("运行时符号链接层级过深")
    if metadata.st_uid != 0:
        raise RuntimeExecutionTrustError("运行时符号链接非 root 所有")
    try:
        target_text = os.readlink(link)
    except OSError:
        raise RuntimeExecutionTrustError("运行时符号链接目标无效") from None
    if os.path.isabs(target_text):
        _require_root_controlled_file_target(Path(target_text))
        return
    target = _cwd_relative_link_target(link, target_text)
    seen = frozenset() if visited is None else visited
    identity = tuple(link.parts)
    if identity in seen:
        raise RuntimeExecutionTrustError("运行时符号链接形成循环")
    _require_cwd_link_target(
        object_root,
        target,
        depth=depth + 1,
        visited=seen | {identity},
    )


def _cwd_relative_link_target(link: Path, target_text: str) -> Path:
    if not target_text or "\\" in target_text or "\x00" in target_text:
        raise RuntimeExecutionTrustError("运行时符号链接目标无效")
    components = list(link.parent.parts)
    for component in target_text.split("/"):
        if component in {"", "."}:
            continue
        if component == "..":
            if not components:
                raise RuntimeExecutionTrustError("运行时符号链接逃逸运行时根")
            components.pop()
            continue
        components.append(component)
    return Path(".") if not components else Path(*components)


def _require_cwd_link_target(
    object_root: Path,
    target: Path,
    *,
    depth: int,
    visited: frozenset[tuple[str, ...]],
) -> None:
    try:
        metadata = target.lstat()
    except OSError:
        raise RuntimeExecutionTrustError("运行时符号链接目标无效") from None
    if stat.S_ISLNK(metadata.st_mode):
        _require_cwd_tree_symlink(
            object_root,
            target,
            metadata,
            depth=depth,
            visited=visited,
        )
        return
    if stat.S_ISDIR(metadata.st_mode):
        _require_posix_entry(target, directory=True)
        if not _is_cwd_relative_to(target, object_root):
            _require_cwd_directory_chain(target)
        return
    if stat.S_ISREG(metadata.st_mode):
        _require_posix_entry(target, directory=False)
        if not _is_cwd_relative_to(target, object_root):
            _require_cwd_directory_chain(target.parent)
        return
    raise RuntimeExecutionTrustError("运行时符号链接目标无效")


def _is_cwd_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _require_root_controlled_file_target(path: Path) -> None:
    try:
        resolved = _absolute_lexical(path).resolve(strict=True)
        if not resolved.is_file():
            raise RuntimeExecutionTrustError("运行时外部链接目标不是普通文件")
        _require_posix_trusted_file_target(resolved)
    except RuntimeExecutionTrustError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RuntimeExecutionTrustError("运行时外部链接目标无效") from None


def _require_cwd_executable_link(link: Path, object_root: Path, *, depth: int) -> None:
    if depth >= 8:
        raise RuntimeExecutionTrustError("root 控制解释器链接层级过深")
    _require_root_owned_link(link)
    try:
        target_text = os.readlink(link)
    except OSError:
        raise RuntimeExecutionTrustError("root 控制解释器链接不可读") from None
    target = Path(target_text)
    if target.is_absolute():
        verify_root_controlled_executable(target)
        return
    components = _cwd_link_components(target_text)
    candidate = link.parent / Path(*components)
    try:
        relative = candidate.relative_to(object_root)
    except ValueError:
        raise RuntimeExecutionTrustError("root 控制解释器链接逃逸运行时对象") from None
    _require_cwd_executable_target(object_root, relative, depth=depth + 1)


def _cwd_link_components(target: str) -> tuple[str, ...]:
    if not target or "\\" in target or "\x00" in target:
        raise RuntimeExecutionTrustError("root 控制解释器链接目标无效")
    components = tuple(target.split("/"))
    if any(component in {"", ".", ".."} for component in components):
        raise RuntimeExecutionTrustError("root 控制解释器链接目标无效")
    return components


def _require_cwd_executable_target(
    object_root: Path,
    relative: Path,
    *,
    depth: int,
) -> None:
    current = object_root
    for index, component in enumerate(relative.parts):
        current /= component
        leaf = index == len(relative.parts) - 1
        if _is_linklike(current):
            if leaf:
                _require_cwd_executable_link(current, object_root, depth=depth)
                return
            raise RuntimeExecutionTrustError("root 控制解释器链接包含不可信链接")
        if leaf:
            _require_posix_entry(current, directory=False)
            if not current.is_file() or not os.access(current, os.X_OK):
                raise RuntimeExecutionTrustError("root 控制解释器不可执行")
        else:
            _require_posix_entry(current, directory=True)


def _require_posix_ancestor_trust(path: Path) -> None:
    current = Path(path.anchor)
    _require_posix_entry(current, directory=True)
    for component in path.parts[1:]:
        current /= component
        if _is_linklike(current):
            raise RuntimeExecutionTrustError("运行时根祖先链包含符号链接")
        _require_posix_entry(current, directory=True)


def _require_posix_tree_trust(root: Path, *, trusted_root: Path | None = None) -> None:
    boundary = root if trusted_root is None else trusted_root
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            metadata = current.lstat()
        except OSError:
            raise RuntimeExecutionTrustError("运行时目录树成员不存在") from None
        if stat.S_ISLNK(metadata.st_mode):
            _require_posix_symlink(root, boundary, current, metadata)
            continue
        _require_posix_entry(current, directory=stat.S_ISDIR(metadata.st_mode))
        if stat.S_ISDIR(metadata.st_mode):
            try:
                pending.extend(Path(entry.path) for entry in os.scandir(current))
            except OSError:
                raise RuntimeExecutionTrustError("运行时目录树无法完整扫描") from None
        elif not stat.S_ISREG(metadata.st_mode):
            raise RuntimeExecutionTrustError("运行时目录树包含非普通成员")


def _require_posix_symlink(
    object_root: Path,
    trusted_root: Path,
    link: Path,
    metadata: os.stat_result,
) -> None:
    if metadata.st_uid != 0:
        raise RuntimeExecutionTrustError("运行时符号链接非 root 所有")
    try:
        target = link.resolve(strict=True)
    except OSError:
        raise RuntimeExecutionTrustError("运行时符号链接目标无效") from None
    if target.is_relative_to(trusted_root):
        if not target.is_file() and not target.is_dir():
            raise RuntimeExecutionTrustError("运行时内部符号链接目标无效")
        if not target.is_relative_to(object_root):
            if target.is_dir():
                _require_posix_ancestor_trust(target)
            else:
                _require_posix_trusted_file_target(target)
        return
    _require_posix_trusted_file_target(target)


def _require_root_owned_link(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeExecutionTrustError("root 控制解释器链接不可读") from None
    if not stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0:
        raise RuntimeExecutionTrustError("root 控制解释器链接不受信任")


def _require_posix_trusted_file_target(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if _is_linklike(current):
            raise RuntimeExecutionTrustError("运行时外部链接目标链不可信")
        _require_posix_entry(current, directory=current != path)
    if not path.is_file():
        raise RuntimeExecutionTrustError("运行时外部链接目标不是普通文件")


def _require_posix_entry(path: Path, *, directory: bool) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeExecutionTrustError("运行时执行信任路径不存在") from None
    if metadata.st_uid != 0:
        raise RuntimeExecutionTrustError("运行时执行信任路径非 root 所有")
    if stat.S_IMODE(metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeExecutionTrustError("运行时执行信任路径对非所有者可写")
    if directory and not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeExecutionTrustError("运行时执行信任目录无效")


def _require_reparse_free_tree(root: Path) -> None:
    pending = [root]
    while pending:
        current = pending.pop()
        if _is_linklike(current):
            raise RuntimeExecutionTrustError("运行时目录树包含重解析点")
        try:
            if current.is_dir():
                pending.extend(Path(entry.path) for entry in os.scandir(current))
            elif not current.is_file():
                raise RuntimeExecutionTrustError("运行时目录树包含非普通成员")
        except OSError:
            raise RuntimeExecutionTrustError("运行时目录树无法完整扫描") from None


def _is_linklike(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeExecutionTrustError("运行时执行路径不存在") from None
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse)


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


__all__ = [
    "RuntimeExecutionTrustError",
    "verify_execution_trust",
    "verify_execution_trust_from_cwd",
    "verify_root_controlled_executable",
]
