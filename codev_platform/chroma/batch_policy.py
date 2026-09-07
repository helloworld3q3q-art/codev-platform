"""Chroma 写入批量策略。"""

from __future__ import annotations


def resolve_flush_batch_size(client: object, configured: int) -> int:
    """把业务批量上限钳制到当前 Chroma 后端允许的硬上限。"""
    if type(configured) is not int or configured <= 0:
        raise ValueError("PLATFORM_INDEX_FLUSH_BATCH 必须是正整数")
    try:
        client_limit = client.get_max_batch_size()
    except Exception as error:  # noqa: BLE001 - 后端能力不可证明时禁止写入
        raise RuntimeError("无法取得 Chroma 写入批量上限") from error
    if type(client_limit) is not int or client_limit <= 0:
        raise RuntimeError("Chroma 写入批量上限无效")
    return min(configured, client_limit)
