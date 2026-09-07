"""release wheel 静态载荷的生成字节码清理适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

from codev_platform import runtime_wheel
from codev_platform.runtime_generated_bytecode_core import (
    GeneratedBytecodeInventory,
    GeneratedBytecodePlan,
    RuntimeGeneratedBytecodeError,
    apply_generated_bytecode_cleanup,
    plan_generated_bytecode_cleanup,
)
from codev_platform.runtime_wheel import WheelPayloadProof


_RELEASE_PLAN_KIND = "release-wheel-inventory-v1"


class RuntimeReleaseGeneratedBytecodeError(RuntimeGeneratedBytecodeError):
    """release wheel 载荷无法证明生成字节码可安全清理。"""


@dataclass(frozen=True, slots=True)
class ReleaseGeneratedBytecodeCleanupProof:
    """release 缓存删除完成后的无路径结果。"""

    deleted_files: int
    deleted_cache_directories: int


def plan_verified_release_generated_bytecode(
    purelib: Path,
    proof: WheelPayloadProof,
    controlled_base_pth: Path,
) -> GeneratedBytecodePlan:
    """只接受现有 wheel 静态载荷 verifier 精确证明的当前缓存。"""
    try:
        return plan_generated_bytecode_cleanup(
            Path(purelib),
            kind=_RELEASE_PLAN_KIND,
            snapshot_factory=partial(
                _collect_release_inventory,
                proof=proof,
                controlled_base_pth=Path(controlled_base_pth),
            ),
            strict_verifier=partial(
                _verify_release_inventory,
                proof=proof,
                controlled_base_pth=Path(controlled_base_pth),
            ),
        )
    except RuntimeGeneratedBytecodeError:
        raise RuntimeReleaseGeneratedBytecodeError("release 字节码预检无法证明") from None


def apply_verified_release_generated_bytecode(
    plan: GeneratedBytecodePlan,
) -> ReleaseGeneratedBytecodeCleanupProof:
    """删除已证明 release 缓存，随后复用原有严格 wheel 安装校验。"""
    try:
        result = apply_generated_bytecode_cleanup(plan, kind=_RELEASE_PLAN_KIND)
    except RuntimeGeneratedBytecodeError:
        raise RuntimeReleaseGeneratedBytecodeError("release 字节码无法安全清理") from None
    if result.verification_proof is not None:
        raise RuntimeReleaseGeneratedBytecodeError("release 字节码清理证明类型无效")
    return ReleaseGeneratedBytecodeCleanupProof(
        deleted_files=result.deleted_files,
        deleted_cache_directories=result.deleted_cache_directories,
    )


def _collect_release_inventory(
    root: Path,
    *,
    proof: WheelPayloadProof,
    controlled_base_pth: Path,
) -> GeneratedBytecodeInventory:
    try:
        inventory, actual, directories = runtime_wheel._collect_installed_application_inventory(
            proof,
            root,
            controlled_base_pth,
        )

        def verify_virtual(
            remaining: dict[str, Path],
            virtual_directories: set[str],
        ) -> None:
            runtime_wheel._verify_installed_application_snapshot(
                remaining,
                virtual_directories,
                inventory,
            )

        return GeneratedBytecodeInventory(
            actual=actual,
            directories=directories,
            known_files=inventory.allowed_files,
            source_files=inventory.source_files,
            verify_virtual=verify_virtual,
        )
    except runtime_wheel.RuntimeWheelError:
        raise RuntimeReleaseGeneratedBytecodeError("release 静态载荷不能形成缓存预检") from None
    except (OSError, TypeError, ValueError):
        raise RuntimeReleaseGeneratedBytecodeError("release 静态载荷预检失败") from None


def _verify_release_inventory(
    root: Path,
    *,
    proof: WheelPayloadProof,
    controlled_base_pth: Path,
) -> None:
    runtime_wheel.verify_installed_application(proof, root, controlled_base_pth)


__all__ = [
    "ReleaseGeneratedBytecodeCleanupProof",
    "RuntimeReleaseGeneratedBytecodeError",
    "apply_verified_release_generated_bytecode",
    "plan_verified_release_generated_bytecode",
]
