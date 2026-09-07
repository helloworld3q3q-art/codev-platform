"""reindex worker CLI 的隔离运行时延迟接线。"""
from __future__ import annotations


def worker_execution_mode(cfg: dict) -> str:
    """读取 worker 唯一执行模式；默认 isolated，legacy 必须显式声明。"""
    from codev_platform.reindex.execution_mode import execution_mode

    return execution_mode(cfg)


def build_isolated_worker(cfg: dict, owner_token: str):
    """仅在 worker 动作中构造 production isolated runtime。"""
    from codev_platform.reindex.isolated_worker_composer import build_isolated_worker

    return build_isolated_worker(cfg, owner_token)


__all__ = ["build_isolated_worker", "worker_execution_mode"]
