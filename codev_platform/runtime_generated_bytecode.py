"""base distribution inventory 的生成字节码清理适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path

from codev_platform import runtime_distribution_inventory as _inventory
from codev_platform.runtime_dependency_contract import DistributionPin
from codev_platform.runtime_distribution_inventory import DistributionInventoryProof
from codev_platform.runtime_generated_bytecode_core import (
    GeneratedBytecodeCleanupResult,
    GeneratedBytecodeInventory,
    GeneratedBytecodePlan,
    RuntimeGeneratedBytecodeError,
    apply_generated_bytecode_cleanup,
    plan_generated_bytecode_cleanup,
)


_BASE_PLAN_KIND = "base-distribution-inventory-v1"


class _BaseInventorySnapshotError(RuntimeError):
    """base inventory 不能为生成缓存形成受控虚拟快照。"""


def plan_verified_generated_bytecode(
    purelib: Path,
    pins: tuple[DistributionPin, ...],
) -> GeneratedBytecodePlan:
    """只接受可由既有 distribution inventory 精确证明的当前缓存。"""
    return plan_generated_bytecode_cleanup(
        Path(purelib),
        kind=_BASE_PLAN_KIND,
        snapshot_factory=partial(_collect_base_inventory, pins=pins),
        strict_verifier=partial(_verify_base_inventory, pins=pins),
    )


def apply_verified_generated_bytecode(
    plan: GeneratedBytecodePlan,
) -> GeneratedBytecodeCleanupProof:
    """删除已证明 base 缓存，随后复用原有严格 distribution inventory。"""
    result = apply_generated_bytecode_cleanup(plan, kind=_BASE_PLAN_KIND)
    proof = _require_distribution_proof(result)
    return GeneratedBytecodeCleanupProof(
        deleted_files=result.deleted_files,
        deleted_cache_directories=result.deleted_cache_directories,
        inventory_proof=proof,
    )


def _collect_base_inventory(
    root: Path,
    *,
    pins: tuple[DistributionPin, ...],
) -> GeneratedBytecodeInventory:
    try:
        purelib = _inventory._plain_directory(root, "基座 purelib")
        expected = _inventory._expected_pins(pins)
        actual, directories = _inventory._walk_inventory(purelib)
        claims, distributions = _inventory._collect_claims(purelib, expected)

        def verify_virtual(
            remaining: dict[str, Path],
            virtual_directories: set[str],
        ) -> DistributionInventoryProof:
            return _inventory._verify_inventory_snapshot(
                purelib,
                expected,
                remaining,
                virtual_directories,
                claims=claims,
                distributions=distributions,
            )

        return GeneratedBytecodeInventory(
            actual=actual,
            directories=directories,
            known_files=frozenset(claims),
            source_files=frozenset(claims),
            verify_virtual=verify_virtual,
        )
    except _inventory.RuntimeDistributionInventoryError:
        raise _BaseInventorySnapshotError("base 静态清单不能形成缓存预检") from None
    except (OSError, TypeError, ValueError):
        raise _BaseInventorySnapshotError("base 静态清单预检失败") from None


def _verify_base_inventory(
    root: Path,
    *,
    pins: tuple[DistributionPin, ...],
) -> DistributionInventoryProof:
    return _inventory.verify_distribution_inventory(root, pins)


def _require_distribution_proof(
    result: GeneratedBytecodeCleanupResult,
) -> DistributionInventoryProof:
    proof = result.verification_proof
    if type(proof) is not DistributionInventoryProof:
        raise RuntimeGeneratedBytecodeError("base 字节码清理证明类型无效")
    return proof


@dataclass(frozen=True, slots=True)
class GeneratedBytecodeCleanupProof:
    """base 清理完成后保留既有 distribution inventory 证明。"""

    deleted_files: int
    deleted_cache_directories: int
    inventory_proof: DistributionInventoryProof


__all__ = [
    "GeneratedBytecodeCleanupProof",
    "GeneratedBytecodePlan",
    "RuntimeGeneratedBytecodeError",
    "apply_verified_generated_bytecode",
    "plan_verified_generated_bytecode",
]
