"""platform-docs 多租户冷启动的纯 readiness 与预热策略。"""

from __future__ import annotations

from collections.abc import Callable


def service_ready(
    *,
    model_ready: bool,
    default_project_id: str | None,
    default_collection_ready: bool,
) -> bool:
    """无默认租户时允许 collection 按请求加载；模型始终必须先就绪。"""
    return model_ready and (default_project_id is None or default_collection_ready)


def prewarm_default_project(
    default_project_id: str | None,
    *,
    ensure_model: Callable[[], object],
    ensure_project: Callable[[str], object],
) -> None:
    """预热模型，并且只在存在默认租户时预加载其 collection。"""
    if default_project_id is None:
        ensure_model()
        return
    ensure_project(default_project_id)


__all__ = ["prewarm_default_project", "service_ready"]
