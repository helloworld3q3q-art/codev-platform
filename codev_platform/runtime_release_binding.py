"""在固定锁顺序内只读绑定当前内容寻址 release。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.runtime_models import (
    BaseMetadata,
    ReleaseMetadata,
    RuntimeModelError,
    require_runtime_revision,
    require_sha256,
)
from codev_platform.runtime_deadline import runtime_deadline_scope


class RuntimeReleaseBindingError(RuntimeError):
    """当前 release 无法在共享锁内形成稳定、完整的只读身份。"""


@dataclass(frozen=True, slots=True)
class BoundRelease:
    """已在锁内完整验证的当前 release 不可变快照。"""

    root: Path
    release_id: str
    runtime_revision: str
    base_id: str
    interpreter_path: Path

    def __post_init__(self) -> None:
        root = Path(self.root)
        interpreter = Path(self.interpreter_path)
        release_id = _require_sha256(self.release_id, "release_id")
        _require_sha256(self.base_id, "base_id")
        _require_revision(self.runtime_revision)
        normalized_root = Path(os.path.abspath(root))
        normalized_interpreter = Path(os.path.abspath(interpreter))
        release_dir = normalized_root / "releases" / release_id
        if (
            not root.is_absolute()
            or root != normalized_root
            or not interpreter.is_absolute()
            or interpreter != normalized_interpreter
            or interpreter == release_dir
            or not interpreter.is_relative_to(release_dir)
        ):
            raise RuntimeReleaseBindingError("绑定 release 的解释器路径无效")


@contextmanager
def bind_current_release(
    root: Path,
    expected_release_id: str,
    expected_runtime_revision: str,
) -> Iterator[BoundRelease]:
    """持有 activation→release→base 共享锁并绑定期望的 current。"""
    expected_id = _require_sha256(expected_release_id, "expected_release_id")
    expected_revision = _require_revision(expected_runtime_revision)
    with runtime_deadline_scope(120.0):
        with _bind_verified_current(
            root,
            expected_release_id=expected_id,
            expected_runtime_revision=expected_revision,
        ) as selected:
            yield selected


def snapshot_current_release(root: Path) -> BoundRelease:
    """完整验证 current 并返回快照；返回前释放全部共享锁。"""
    with runtime_deadline_scope(120.0):
        with _bind_verified_current(
            root,
            expected_release_id=None,
            expected_runtime_revision=None,
        ) as selected:
            return selected


@contextmanager
def _bind_verified_current(
    root: Path,
    *,
    expected_release_id: str | None,
    expected_runtime_revision: str | None,
) -> Iterator[BoundRelease]:
    locks = ExitStack()
    try:
        canonical = _canonical_runtime_root(Path(root))
        locks.enter_context(_activation_lock(canonical, shared=True))
        current = _read_current_release_id(canonical)
        if expected_release_id is not None and current != expected_release_id:
            raise RuntimeReleaseBindingError("current release 与期望身份不一致")
        locks.enter_context(_id_lock(canonical, "release", current, shared=True))
        base_id = _require_sha256(
            _read_release_base_id_locked(canonical, current),
            "base_id",
        )
        locks.enter_context(_id_lock(canonical, "base", base_id, shared=True))
        base = _verify_base_locked(canonical, base_id)
        release = _verify_release_locked(
            canonical,
            current,
            verified_base=base,
        )
        selected = _bound_release(canonical, current, base_id, base, release)
        if (
            expected_runtime_revision is not None
            and selected.runtime_revision != expected_runtime_revision
        ):
            raise RuntimeReleaseBindingError("current release 与期望版本不一致")
        if _read_current_release_id(canonical) != current:
            raise RuntimeReleaseBindingError("current release 在绑定期间发生漂移")
    except RuntimeReleaseBindingError:
        locks.close()
        raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        locks.close()
        raise
    except Exception as error:
        locks.close()
        raise RuntimeReleaseBindingError("当前 release 绑定失败") from error
    try:
        yield selected
    finally:
        locks.close()


def _bound_release(
    root: Path,
    release_id: str,
    base_id: str,
    base: object,
    release: object,
) -> BoundRelease:
    if type(base) is not BaseMetadata or base.base_id != base_id:
        raise RuntimeReleaseBindingError("基座锁定身份发生漂移")
    if (
        type(release) is not ReleaseMetadata
        or release.release_id != release_id
        or release.base_id != base_id
    ):
        raise RuntimeReleaseBindingError("release 锁定身份发生漂移")
    return BoundRelease(
        root=root,
        release_id=release.release_id,
        runtime_revision=release.runtime_revision,
        base_id=release.base_id,
        interpreter_path=root / "releases" / release.release_id / release.python_relative,
    )


def _require_sha256(value: object, field: str) -> str:
    try:
        return require_sha256(value, field=field)
    except RuntimeModelError:
        raise RuntimeReleaseBindingError(f"{field} 无效") from None


def _require_revision(value: object) -> str:
    try:
        return require_runtime_revision(value)
    except RuntimeModelError:
        raise RuntimeReleaseBindingError("expected_runtime_revision 无效") from None


def _canonical_runtime_root(root: Path) -> Path:
    from codev_platform.runtime_release import _require_runtime_root

    return _require_runtime_root(root)


def _read_current_release_id(root: Path) -> str:
    from codev_platform.runtime_release import _read_link

    current = _read_link(root, "current", required=True)
    if current is None:  # pragma: no cover - required=True 的防御性契约
        raise RuntimeReleaseBindingError("current release 不存在")
    return current


def _activation_lock(root: Path, *, shared: bool) -> AbstractContextManager[None]:
    from codev_platform.runtime_storage import activation_lock

    return activation_lock(root, shared=shared)


def _id_lock(
    root: Path,
    kind: str,
    object_id: str,
    *,
    shared: bool,
) -> AbstractContextManager[None]:
    from codev_platform.runtime_storage import id_lock

    return id_lock(root, kind, object_id, shared=shared)


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
) -> ReleaseMetadata:
    from codev_platform.runtime_build import verify_release_locked

    return verify_release_locked(root, release_id, verified_base=verified_base)


__all__ = [
    "BoundRelease",
    "RuntimeReleaseBindingError",
    "bind_current_release",
    "snapshot_current_release",
]
