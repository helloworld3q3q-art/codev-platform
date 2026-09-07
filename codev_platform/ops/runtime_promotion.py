"""``runtime promote`` 的 CLI 适配层。"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from codev_platform.runtime_thin_promotion import ThinPromotionError, promote_staged_release
from codev_platform.runtime_thin_promotion_adapters import production_promotion_ports
from codev_platform.runtime_thin_promotion_source import stage_daily_target_release


def cmd_runtime_promote(args: argparse.Namespace) -> int:
    """执行日常薄发布；错误只输出固定码，不回显系统路径或底层命令。"""
    try:
        root = _runtime_root(args)
        target, rollback = _promotion_target(args, root)
        result = promote_staged_release(
            root,
            target_release_id=target,
            rollback_release_id=rollback,
            ports=production_promotion_ports(root, service_user=args.service_user),
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        _emit({"error": "runtime_promote_failed", "kind": "runtime", "status": "error"}, error=True)
        return 1
    _emit(
        {
            "kind": "runtime_promotion",
            "result": dataclasses.asdict(result),
            "status": "ok",
        }
    )
    return 0


def _promotion_target(args: argparse.Namespace, runtime_root: Path) -> tuple[str, str]:
    target_release = getattr(args, "target_release", None)
    target_revision = getattr(args, "target_revision", None)
    rollback_anchor = getattr(args, "rollback_anchor", None)
    if target_release is not None:
        if (
            target_revision is not None
            or getattr(args, "repo", None) is not None
            or type(rollback_anchor) is not str
        ):
            raise ThinPromotionError("日常薄发布参数组合无效")
        return target_release, rollback_anchor
    if target_revision is None or getattr(args, "repo", None) is None or rollback_anchor is not None:
        raise ThinPromotionError("日常薄发布参数组合无效")
    staged = stage_daily_target_release(
        runtime_root,
        repo=args.repo,
        target_revision=target_revision,
        service_user=args.service_user,
    )
    return staged.target_release_id, staged.baseline_release_id


def _runtime_root(args: argparse.Namespace) -> Path:
    root = getattr(args, "runtime_root", None)
    if not isinstance(root, Path) or not root.is_absolute():
        raise ThinPromotionError("日常薄发布必须显式指定运行时根")
    return root


def _emit(payload: dict[str, object], *, error: bool = False) -> None:
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        file=sys.stderr if error else sys.stdout,
        flush=True,
    )


__all__ = ["cmd_runtime_promote"]
