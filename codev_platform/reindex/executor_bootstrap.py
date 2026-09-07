"""executor 子进程的固定白名单策略组合边界。"""
from __future__ import annotations

from collections.abc import Mapping

from codev_platform.reindex.attempt_inputs import (
    AttemptInputStrategy,
    ConfiguredAttemptInputStrategy,
    freeze_attempt_input_strategies,
)


def build_attempt_input_strategies(cfg: dict) -> Mapping[str, AttemptInputStrategy]:
    """A4 只组合 configured；Plan B 在此显式增加 exact_workspace。"""
    return freeze_attempt_input_strategies({
        "configured": ConfiguredAttemptInputStrategy(cfg),
    })


__all__ = ["build_attempt_input_strategies"]
