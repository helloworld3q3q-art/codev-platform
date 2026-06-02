"""OpenAPI 稳定性助手 (plan §十三) —— operationId 去重检测。

前端按 OpenAPI 生成类型 (openapi-typescript), operationId 重复会让生成的 client 方法名冲突。
提供一个纯函数扫 app.openapi() 找重复 operationId, 供契约测试断言 (plan §十三 Gate)。
"""
from __future__ import annotations


def duplicate_operation_ids(app) -> list[str]:
    """返回重复的 operationId 列表 (空 = 无重复)。供测试 assert == []。"""
    schema = app.openapi()
    seen: dict[str, int] = {}
    for path_item in (schema.get("paths") or {}).values():
        for op in path_item.values():
            if not isinstance(op, dict):
                continue
            op_id = op.get("operationId")
            if op_id:
                seen[op_id] = seen.get(op_id, 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)
