"""内容寻址运行时的唯一激活与回滚入口。"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from codev_platform.core.runtime_models import ActivationResult, BaseMetadata, require_sha256
from codev_platform.runtime_deadline import runtime_operation_timeout


_RELEASE_ID = re.compile(r"[0-9a-f]{64}\Z")
_EXPLICIT_ACTIVATION_ERROR = "运行时显式回滚锚点激活失败"
_EXPLICIT_BASE_ERROR = "target 与回滚锚点必须使用同一基座"
_EXPLICIT_STATE_ERROR = "运行时显式回滚锚点激活失败且引用补偿失败，引用状态不可证明"


class RuntimeReleaseError(RuntimeError):
    """运行时激活引用无法被安全验证或原子替换。"""


class _ExplicitBaseMismatch(RuntimeError):
    """显式激活的两个已验证 release 不属于同一基座。"""


class _ExplicitStateUnproven(RuntimeError):
    """显式激活补偿失败，双引用状态无法继续证明。"""


def release_root(cfg: dict[str, Any] | None = None, explicit: Path | None = None) -> Path:
    """按显式参数、配置、用户数据目录的顺序解析运行时根。"""
    if explicit is not None:
        return Path(explicit).expanduser()
    configured = None
    if isinstance(cfg, dict):
        runtime = cfg.get("runtime")
        if isinstance(runtime, dict):
            configured = runtime.get("release_root")
    if configured:
        return Path(str(configured)).expanduser()
    return Path.home() / ".local" / "share" / "codev-platform"


@runtime_operation_timeout(120.0)
def activate_release(root: Path, release_id: str) -> ActivationResult:
    """验证目标后原子切换 current，并把旧 current 写入 previous。"""
    _require_posix_mutation()
    normalized = _require_release_id(release_id)
    canonical = _require_runtime_root(root)
    try:
        with _activation_lock(canonical):
            current = _read_link(canonical, "current", required=False)
            previous = _read_link(canonical, "previous", required=False)
            referenced = (normalized, current, previous)
            with _verified_releases(canonical, referenced):
                if current == normalized:
                    return ActivationResult(
                        active_release=normalized,
                        previous_release=previous,
                    )
                try:
                    if current is not None:
                        _replace_link(canonical, "previous", current)
                    _replace_link(canonical, "current", normalized)
                except BaseException as activation_error:
                    _compensate_activation(
                        canonical,
                        requested=normalized,
                        original_current=current,
                        original_previous=previous,
                        activation_error=activation_error,
                    )
                    raise
                return ActivationResult(
                    active_release=normalized,
                    previous_release=current,
                )
    except RuntimeReleaseError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise RuntimeReleaseError("运行时激活失败") from error


@runtime_operation_timeout(120.0)
def activate_release_with_rollback_anchor(
    root: Path,
    target_release_id: str,
    rollback_release_id: str,
) -> ActivationResult:
    """原子切换 target，并把调用方已证明的 release 固定为回滚锚点。"""
    _require_posix_mutation()
    target = _require_release_id(target_release_id)
    rollback = _require_release_id(rollback_release_id)
    if target == rollback:
        raise RuntimeReleaseError("target 与回滚锚点不能相同")
    result: ActivationResult | None = None
    failure_message = _EXPLICIT_ACTIVATION_ERROR
    try:
        result = _activate_release_with_rollback_anchor(root, target, rollback)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except _ExplicitBaseMismatch:
        failure_message = _EXPLICIT_BASE_ERROR
    except _ExplicitStateUnproven:
        failure_message = _EXPLICIT_STATE_ERROR
    except Exception:
        pass
    if result is None:
        raise RuntimeReleaseError(failure_message)
    return result


@runtime_operation_timeout(120.0)
def activate_release_from_expected_current(
    root: Path,
    target_release_id: str,
    expected_current_release_id: str,
) -> ActivationResult:
    """仅当 current 仍是预期锚点时，原子前移到目标 release。

    日常薄发布不能复用宽松的显式锚点激活：若有另一条发布链已改变
    ``current``，覆盖它会破坏并发发布的回滚边界。本入口把预期 current
    与回滚锚点收敛为同一个不可变 release，并保留完成态幂等性。
    """
    _require_posix_mutation()
    target = _require_release_id(target_release_id)
    expected_current = _require_release_id(expected_current_release_id)
    if target == expected_current:
        raise RuntimeReleaseError("target 与预期 current 不能相同")
    try:
        return _activate_release_from_expected_current(root, target, expected_current)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeReleaseError:
        raise
    except Exception:
        raise RuntimeReleaseError("运行时预期 current 激活失败") from None


@runtime_operation_timeout(120.0)
def rollback_release_from_expected_current(
    root: Path,
    expected_current_release_id: str,
    rollback_release_id: str,
) -> ActivationResult:
    """仅回滚本次薄发布已激活的 current，拒绝覆盖未知并发状态。"""
    _require_posix_mutation()
    expected_current = _require_release_id(expected_current_release_id)
    rollback = _require_release_id(rollback_release_id)
    if expected_current == rollback:
        raise RuntimeReleaseError("预期 current 与回滚锚点不能相同")
    try:
        return _rollback_release_from_expected_current(root, expected_current, rollback)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeReleaseError:
        raise
    except Exception:
        raise RuntimeReleaseError("运行时预期 current 回滚失败") from None


def _activate_release_with_rollback_anchor(
    root: Path,
    target: str,
    rollback: str,
) -> ActivationResult:
    """在内部边界执行双引用事务，公开入口只暴露固定错误。"""
    canonical = _require_runtime_root(root)
    with _activation_lock(canonical):
        current = _read_link_reference(canonical, "current", required=False)
        previous = _read_link_reference(canonical, "previous", required=False)
        with _verified_releases(canonical, (target, rollback)) as verified:
            if verified[target].base_id != verified[rollback].base_id:  # type: ignore[attr-defined]
                raise _ExplicitBaseMismatch
            if current == target and previous == rollback:
                return ActivationResult(target, rollback)
            try:
                if previous != rollback:
                    _replace_link(canonical, "previous", rollback)
                if current != target:
                    _replace_link(canonical, "current", target)
            except BaseException:
                _compensate_explicit_activation(
                    canonical,
                    target=target,
                    rollback=rollback,
                    original_current=current,
                    original_previous=previous,
                )
                raise
            return ActivationResult(target, rollback)


def _activate_release_from_expected_current(
    root: Path,
    target: str,
    expected_current: str,
) -> ActivationResult:
    """在激活锁内核对 current，再执行带固定回滚锚点的双引用切换。"""
    canonical = _require_runtime_root(root)
    with _activation_lock(canonical):
        current = _read_link_reference(canonical, "current", required=True)
        previous = _read_link_reference(canonical, "previous", required=False)
        with _verified_releases(canonical, (target, expected_current)) as verified:
            if verified[target].base_id != verified[expected_current].base_id:  # type: ignore[attr-defined]
                raise RuntimeReleaseError("target 与预期 current 必须使用同一基座")
            if current == target and previous == expected_current:
                return ActivationResult(target, expected_current)
            if current != expected_current:
                raise RuntimeReleaseError("current 已被其他发布链改变")
            try:
                if previous != expected_current:
                    _replace_link(canonical, "previous", expected_current)
                _replace_link(canonical, "current", target)
            except BaseException:
                _compensate_explicit_activation(
                    canonical,
                    target=target,
                    rollback=expected_current,
                    original_current=current,
                    original_previous=previous,
                )
                raise
            return ActivationResult(target, expected_current)


def _rollback_release_from_expected_current(
    root: Path,
    expected_current: str,
    rollback: str,
) -> ActivationResult:
    """在激活锁内只撤销本次已知的 target→anchor 前移。"""
    canonical = _require_runtime_root(root)
    with _activation_lock(canonical):
        current = _read_link_reference(canonical, "current", required=True)
        previous = _read_link_reference(canonical, "previous", required=True)
        with _verified_releases(canonical, (expected_current, rollback)) as verified:
            if verified[expected_current].base_id != verified[rollback].base_id:  # type: ignore[attr-defined]
                raise RuntimeReleaseError("预期 current 与回滚锚点必须使用同一基座")
            if current == rollback and previous == rollback:
                return ActivationResult(rollback, rollback)
            if current != expected_current or previous != rollback:
                raise RuntimeReleaseError("运行时引用已被其他发布链改变")
            try:
                _replace_link(canonical, "current", rollback)
            except BaseException as rollback_error:
                _compensate_activation(
                    canonical,
                    requested=rollback,
                    original_current=current,
                    original_previous=previous,
                    activation_error=rollback_error,
                )
                raise
            return ActivationResult(rollback, rollback)


@runtime_operation_timeout(120.0)
def rollback_release(root: Path) -> ActivationResult:
    """验证 previous 后只替换 current，保留 previous 作为稳定回滚锚点。"""
    _require_posix_mutation()
    canonical = _require_runtime_root(root)
    try:
        with _activation_lock(canonical):
            current = _read_link(canonical, "current", required=True)
            previous = _read_link(canonical, "previous", required=True)
            assert current is not None
            assert previous is not None
            with _verified_releases(canonical, (current, previous)):
                if current != previous:
                    try:
                        _replace_link(canonical, "current", previous)
                    except BaseException as rollback_error:
                        _compensate_activation(
                            canonical,
                            requested=previous,
                            original_current=current,
                            original_previous=previous,
                            activation_error=rollback_error,
                        )
                        raise
                return ActivationResult(
                    active_release=previous,
                    previous_release=previous,
                )
    except RuntimeReleaseError:
        raise
    except (OSError, RuntimeError, ValueError) as error:
        raise RuntimeReleaseError("运行时回滚失败") from error


def _require_release_id(value: object) -> str:
    if type(value) is not str or _RELEASE_ID.fullmatch(value) is None or set(value) == {"0"}:
        raise RuntimeReleaseError("运行时版本 ID 无效")
    return value


def _require_posix_mutation() -> None:
    if os.name != "posix":
        raise RuntimeReleaseError("运行时激活与回滚只支持 POSIX")


def _require_base_id(value: object) -> str:
    try:
        return require_sha256(value, field="base_id")
    except ValueError:
        raise RuntimeReleaseError("运行时基座 ID 无效") from None


@contextmanager
def _verified_releases(
    root: Path,
    release_ids: tuple[str | None, ...],
) -> Iterator[dict[str, object]]:
    """按 release→base 固定顺序持有共享锁，并在锁内完成全部复验。"""
    normalized = tuple(
        sorted({_require_release_id(item) for item in release_ids if item is not None})
    )
    if not normalized:
        raise RuntimeReleaseError("没有可验证的运行时版本")
    with ExitStack() as locks:
        for release_id in normalized:
            locks.enter_context(_id_lock(root, "release", release_id, shared=True))
        base_by_release = {
            release_id: _require_base_id(_read_release_base_id_locked(root, release_id))
            for release_id in normalized
        }
        base_ids = tuple(sorted(set(base_by_release.values())))
        for base_id in base_ids:
            locks.enter_context(_id_lock(root, "base", base_id, shared=True))
        verified_bases = {base_id: _verify_base_locked(root, base_id) for base_id in base_ids}
        verified_releases: dict[str, object] = {}
        for release_id in normalized:
            verified = _verify_release_locked(
                root,
                release_id,
                verified_base=verified_bases[base_by_release[release_id]],
            )
            if (
                getattr(verified, "release_id", None) != release_id
                or getattr(verified, "base_id", None) != base_by_release[release_id]
            ):
                raise RuntimeReleaseError("运行时版本锁定身份发生变化")
            verified_releases[release_id] = verified
        yield verified_releases


def _require_runtime_root(value: Path) -> Path:
    try:
        path = Path(value).expanduser()
        metadata = path.lstat()
        canonical = path.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise RuntimeReleaseError("运行时根目录不可用") from error
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink() or canonical != path.absolute():
        raise RuntimeReleaseError("运行时根目录不受信任")
    releases = canonical / "releases"
    if not releases.is_dir() or releases.is_symlink():
        raise RuntimeReleaseError("运行时版本根目录不受信任")
    return canonical


def _read_link(root: Path, name: str, *, required: bool) -> str | None:
    release_id = _read_link_reference(root, name, required=required)
    if release_id is None:
        return None
    target = root / "releases" / release_id
    try:
        resolved = target.resolve(strict=True)
    except OSError as error:
        raise RuntimeReleaseError(f"运行时 {name} 目标不可用") from error
    if target.is_symlink() or not target.is_dir() or resolved.parent != (root / "releases"):
        raise RuntimeReleaseError(f"运行时 {name} 目标不受信任")
    return release_id


def _read_link_reference(root: Path, name: str, *, required: bool) -> str | None:
    """只验证引用的词法边界，不要求旧目标仍然存在。"""
    path = root / name
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if required:
            raise RuntimeReleaseError(f"运行时 {name} 引用不存在") from None
        return None
    except OSError as error:
        raise RuntimeReleaseError(f"运行时 {name} 引用不可读") from error
    if not stat.S_ISLNK(metadata.st_mode):
        raise RuntimeReleaseError(f"运行时 {name} 引用不是符号链接")
    try:
        raw = os.readlink(path)
    except OSError as error:
        raise RuntimeReleaseError(f"运行时 {name} 引用不可读") from error
    prefix = "releases/"
    if not raw.startswith(prefix) or "/" in raw[len(prefix) :]:
        raise RuntimeReleaseError(f"运行时 {name} 引用越界")
    return _require_release_id(raw[len(prefix) :])


def _replace_link(root: Path, name: str, release_id: str) -> None:
    normalized = _require_release_id(release_id)
    temporary = root / f".{name}.{uuid4().hex}.tmp"
    owned = False
    try:
        temporary.symlink_to(f"releases/{normalized}", target_is_directory=True)
        owned = True
        os.replace(temporary, root / name)
        owned = False
        _fsync_directory(root)
    finally:
        if owned:
            temporary.unlink(missing_ok=True)


def _compensate_activation(
    root: Path,
    *,
    requested: str,
    original_current: str | None,
    original_previous: str | None,
    activation_error: BaseException,
) -> None:
    """任一目录项切换失败时恢复事务前状态，并对补偿失败关闭。"""
    try:
        _restore_link(
            root,
            "current",
            original=original_current,
            allowed_mutation=requested,
        )
        if original_current is not None:
            _restore_link(
                root,
                "previous",
                original=original_previous,
                allowed_mutation=original_current,
            )
    except BaseException as compensation_error:
        failure = RuntimeReleaseError("运行时激活失败且引用补偿失败，引用状态不可证明")
        failure.add_note(f"原始激活错误类型：{type(activation_error).__name__}")
        raise failure from compensation_error


def _compensate_explicit_activation(
    root: Path,
    *,
    target: str,
    rollback: str,
    original_current: str | None,
    original_previous: str | None,
) -> None:
    """显式双引用事务失败时，只撤销本事务可能写入的两个值。"""
    failed = False
    try:
        _restore_explicit_link(
            root,
            "current",
            original=original_current,
            allowed_mutation=target,
        )
        _restore_explicit_link(
            root,
            "previous",
            original=original_previous,
            allowed_mutation=rollback,
        )
    except BaseException:
        failed = True
    if failed:
        raise _ExplicitStateUnproven


def _restore_explicit_link(
    root: Path,
    name: str,
    *,
    original: str | None,
    allowed_mutation: str,
) -> None:
    """按词法引用补偿显式事务，允许恢复事务前的悬空引用。"""
    actual = _read_link_reference(root, name, required=False)
    if actual == original:
        return
    if actual != allowed_mutation:
        raise RuntimeReleaseError(f"{name} 补偿目标发生变化")
    if original is not None:
        _replace_link(root, name, original)
        return
    (root / name).unlink()
    _fsync_directory(root)


def _restore_link(
    root: Path,
    name: str,
    *,
    original: str | None,
    allowed_mutation: str,
) -> None:
    """只从本事务可产生的值恢复链接，拒绝覆盖未知并发状态。"""
    actual = _read_link(root, name, required=False)
    if actual == original:
        return
    if actual != allowed_mutation:
        raise RuntimeReleaseError(f"{name} 补偿目标发生变化")
    if original is not None:
        _replace_link(root, name, original)
        return
    (root / name).unlink()
    _fsync_directory(root)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_release_base_id_locked(root: Path, release_id: str) -> str:
    from codev_platform.runtime_build import read_release_base_id_locked

    return read_release_base_id_locked(root, release_id)


def _verify_base_locked(root: Path, base_id: str) -> BaseMetadata:
    from codev_platform.runtime_base import verify_base_locked

    return verify_base_locked(root, base_id)


def _verify_release_locked(
    root: Path,
    release_id: str,
    *,
    verified_base: BaseMetadata,
) -> object:
    from codev_platform.runtime_build import verify_release_locked

    return verify_release_locked(root, release_id, verified_base=verified_base)


def _id_lock(
    root: Path,
    kind: str,
    object_id: str,
    *,
    shared: bool,
) -> AbstractContextManager[None]:
    from codev_platform.runtime_storage import id_lock

    return id_lock(root, kind, object_id, shared=shared)


def _activation_lock(root: Path) -> AbstractContextManager[None]:
    from codev_platform.runtime_storage import activation_lock

    return activation_lock(root)


__all__ = [
    "RuntimeReleaseError",
    "activate_release",
    "activate_release_from_expected_current",
    "activate_release_with_rollback_anchor",
    "release_root",
    "rollback_release",
    "rollback_release_from_expected_current",
]
