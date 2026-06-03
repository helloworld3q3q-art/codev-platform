"""统一图谱节点 kind 的**生产者归属**单一真值源 (防跨插件重复产同类节点)。

背景 (2026-06-03 全栈血缘收敛): cross_link 适配器退场前, 它和 stack 插件 (sql/fastapi/
react) 各产一份同样的 endpoint/table/api 节点 -> 统一 store 里语义重复。根因是缺"每类
节点只能有一个 owner 生产者"的契约。本表把它显式化:

    架构级 NodeKind -> 允许产出它的 owner 插件集合 (按语言/框架可多 owner, 但territory 不重叠)。

新增插件若要产某 kind, **必须先在此登记**; test_plugin_owner_uniqueness 会对 ingest 出来的
真实节点断言 plugin ∈ 归属表, 命中即红 —— 防"又一个扫描器重复产 endpoint/table"复发。

未登记的 kind (project / file / 文档类等) 不做归属约束 (不在本表即不检查)。
"""
from __future__ import annotations

from codev_platform.graph.schema import NodeKind

KIND_OWNERS: dict[str, set[str]] = {
    # DB 层 + 表读写血缘函数: 只有 sql 插件 (DDL + Python/Java DML)。
    NodeKind.DB_TABLE.value: {"builtin.sql"},
    NodeKind.DB_COLUMN.value: {"builtin.sql"},
    NodeKind.BACKEND_FUNCTION.value: {"builtin.sql"},
    # 后端端点: 按框架分 owner (语言中性 kind, territory 不重叠)。
    NodeKind.BACKEND_ENDPOINT.value: {
        "builtin.backend_fastapi",
        "builtin.backend_spring",
        "builtin.node",
    },
    # 前端: React / Vue。
    NodeKind.FRONTEND_ROUTE.value: {"builtin.frontend_react", "builtin.vue"},
    NodeKind.FRONTEND_API_CALL.value: {"builtin.frontend_react", "builtin.vue"},
    NodeKind.FRONTEND_COMPONENT.value: {"builtin.vue"},
}
