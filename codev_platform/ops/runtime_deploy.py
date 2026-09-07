"""生产部署计划的严格加载与无秘密 CLI 适配。"""

from __future__ import annotations

import json
import os
import stat
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from codev_platform.core.runtime_models import require_sha256
from codev_platform.runtime_deployment_contract import (
    DEPLOYMENT_PHASES,
    DeploymentPhase,
    DeploymentPlan,
    DeploymentReceipt,
    LegacyDeploymentReceiptAudit,
    RuntimeDeploymentError,
    decode_deployment_plan,
)


_MAX_PLAN_BYTES = 64 * 1024
_PROGRESS_STATES = {
    "started": "开始",
    "completed": "完成",
    "failed_safe": "失败，已恢复安全态",
    "safety_unproven": "失败，安全态未证明",
}
DeploymentRunner = Callable[..., DeploymentReceipt]


def load_deployment_plan(path: Path) -> DeploymentPlan:
    """从单次文件快照严格解码计划，拒绝重复键、额外键和路径竞态。"""
    return decode_deployment_plan(_read_plan_snapshot(path))


def cmd_runtime_deploy(args: object) -> int:
    """执行或续跑生产部署；stdout/stderr 永不回显路径、命令或底层异常。"""
    try:
        plan = load_deployment_plan(getattr(args, "plan", None))
        receipt = _deployment_runner()(plan, progress=_emit_progress)
        result = _public_receipt(plan, receipt)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        _emit(
            {
                "error": "runtime_deploy_failed",
                "kind": "deployment",
                "status": "error",
            },
            error=True,
        )
        return 1
    _emit({"kind": "deployment", "result": result, "status": "ok"})
    return 0


def _deployment_runner() -> DeploymentRunner:
    from codev_platform.runtime_production_deployment import run_production_deployment

    return run_production_deployment


def _read_plan_snapshot(path: object) -> bytes:
    if not isinstance(path, Path) or not path.is_absolute():
        raise RuntimeDeploymentError("生产部署计划路径无效")
    descriptor: int | None = None
    try:
        linked = path.lstat()
        if not _trusted_plan_metadata(linked):
            raise RuntimeDeploymentError("生产部署计划文件不受信任")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if _metadata_identity(linked) != _metadata_identity(opened):
            raise RuntimeDeploymentError("生产部署计划读取前发生变化")
        blocks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(8192, _MAX_PLAN_BYTES + 1 - total))
            if not block:
                break
            blocks.append(block)
            total += len(block)
            if total > _MAX_PLAN_BYTES:
                raise RuntimeDeploymentError("生产部署计划超出固定上限")
        after = os.fstat(descriptor)
        if _metadata_identity(opened) != _metadata_identity(after) or total != after.st_size:
            raise RuntimeDeploymentError("生产部署计划读取期间发生变化")
        return b"".join(blocks)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except (OSError, ValueError, TypeError):
        raise RuntimeDeploymentError("生产部署计划无法安全读取") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _trusted_plan_metadata(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and metadata.st_nlink == 1
        and 0 < metadata.st_size <= _MAX_PLAN_BYTES
    )


def _metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    # Windows 的 path stat / fd stat 会对 ctime 做不同精度换算；生产 Linux 保留 ctime 门禁。
    return (*identity, metadata.st_ctime_ns) if os.name == "posix" else identity


def _public_receipt(
    plan: DeploymentPlan,
    receipt: DeploymentReceipt,
) -> dict[str, object]:
    if type(receipt) is LegacyDeploymentReceiptAudit:
        raise RuntimeDeploymentError("旧 schema 1 审计回执不能作为生产成功结果")
    expected = tuple(phase.value for phase in DEPLOYMENT_PHASES)
    if (
        type(receipt) is not DeploymentReceipt
        or receipt.status != "complete"
        or receipt.completed_phases != expected
        or receipt.plan_sha256 != plan.digest
        or receipt.target_revision != plan.target_revision
    ):
        raise RuntimeDeploymentError("生产部署完成回执无效")
    try:
        release_id = require_sha256(receipt.release_id, field="release_id")
    except ValueError:
        raise RuntimeDeploymentError("生产部署完成回执无效") from None
    return {
        "completed_phases": list(receipt.completed_phases),
        "release_id": release_id,
        "status": receipt.status,
        "target_revision": receipt.target_revision,
    }


def _emit_progress(phase: DeploymentPhase, state: str) -> None:
    message = f"部署阶段 {phase.value} {_PROGRESS_STATES.get(state, '状态更新')}"
    try:
        _emit(
            {
                "message": message,
                "phase": phase.value,
                "state": state,
                "type": "deployment_progress",
            },
            error=True,
        )
    except OSError:
        # 进度通道不可写不能改变部署事务结果，最终回执仍由 stdout/退出码表达。
        return


def _emit(payload: Mapping[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


__all__ = ["cmd_runtime_deploy", "load_deployment_plan"]
