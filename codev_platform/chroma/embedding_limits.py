"""嵌入模型序列长度策略；保持纯函数，供 daemon 与离线索引器复用。"""
from __future__ import annotations

_CPU_DEFAULT_MAX_SEQ_LENGTH = 512


def resolve_embed_max_seq_length(device: object, configured: object) -> int:
    """解析序列上限；CPU 默认收紧到 512，GPU 默认沿用模型能力。"""
    if configured not in (None, ""):
        if isinstance(configured, bool):
            raise TypeError("嵌入序列长度上限必须是整数")
        value = int(configured)
        if value < 0:
            raise ValueError("嵌入序列长度上限不能为负数")
        return value
    return _CPU_DEFAULT_MAX_SEQ_LENGTH if str(device).strip().lower() == "cpu" else 0


def apply_embed_max_seq_length(model: object, limit: int) -> None:
    """只收紧模型长度上限；0 保持模型原值，绝不扩张模型能力。"""
    if isinstance(limit, bool) or type(limit) is not int or limit < 0:
        raise ValueError("嵌入序列长度上限必须是非负整数")
    if limit <= 0:
        return
    current = getattr(model, "max_seq_length", None)
    if not isinstance(current, int) or current <= 0:
        model.max_seq_length = limit
        return
    model.max_seq_length = min(current, limit)


__all__ = ["apply_embed_max_seq_length", "resolve_embed_max_seq_length"]
