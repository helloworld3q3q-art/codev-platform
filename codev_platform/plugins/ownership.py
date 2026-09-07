"""统一图谱节点 kind 的**生产者归属**单一真值源 (防跨插件重复产同类节点)。

背景 (2026-06-03 全栈血缘收敛): cross_link 适配器退场前, 它和 stack 插件 (sql/fastapi/
react) 各产一份同样的 endpoint/table/api 节点 -> 统一 store 里语义重复。根因是缺"每类
节点只能有一个 owner 生产者"的契约。

归属真值源 (2026-06-13 Phase 9 收敛): **不再手维护一张 kind->owner 表** (那份表与插件
真实产出会漂移 —— 典型: builtin.dotnet 产 backend_endpoint 却漏登记)。改为:

  - **插件自描述**: 每个 AnalyzerPlugin 在 base.produces 声明自己产的 NodeKind (生产者
    最清楚自己产什么, 单一真值源)。kind_owners() 反转聚合所有注册插件的 produces。
  - **post-pass 产物**: 少数 owned kind 由 ingest 的 post-pass (非 AnalyzerPlugin 实例,
    无对象可挂 produces) 产, 在 POST_PASS_OWNERS 显式登记 —— 这是它们唯一的归属声明处。

新增插件要产某 kind, **只在该插件类声明 produces** 即自动成 owner (零改本文件)。
test_plugin_owner_uniqueness 对 ingest 出的真实节点断言 plugin ∈ kind_owners(), 命中即红
—— 防"又一个扫描器重复产 endpoint/table"复发, 且 produces 漏声明 (产了没登记) 也会被抓。

未登记的 kind (project / file / 文档类等) 不在 kind_owners() 里 = 不做归属约束。
"""
from __future__ import annotations

from codev_platform.graph.schema import NodeKind

# post-pass 产出的 owned kind: 由 ingest post-pass (frontend_deps / analyzers, 非
# AnalyzerPlugin 实例) 产, 无插件对象可挂 produces -> 在此显式登记 (唯一归属声明真值源)。
POST_PASS_OWNERS: dict[str, set[str]] = {
    # 前端模块依赖图 (dependency-cruiser 接入, ingest _frontend_deps_pass 产)。
    NodeKind.FRONTEND_MODULE.value: {"builtin.frontend_deps"},
    # 软节点 (综合分析器派生, 非确定性血缘): 业务域归类。owner = analyzers 框架 post-pass。
    NodeKind.BUSINESS_DOMAIN.value: {"builtin.analyzers"},
    # 软节点 (Phase 4 结构社区, 算法派生): 同走 analyzers post-pass(CommunityAnalyzer)。
    NodeKind.COMMUNITY.value: {"builtin.analyzers"},
}


def kind_owners() -> dict[str, set[str]]:
    """架构级 NodeKind -> 允许产它的 owner 集合 (单一真值源派生视图)。

    = 反转所有已注册插件的 produces 声明 (kind -> 声明产它的插件名集合)
      ∪ POST_PASS_OWNERS (非插件 post-pass 产物的显式登记)。

    同一 kind 可有多 owner (如 backend_endpoint 由 fastapi/spring/node/dotnet 各按框架产,
    territory 不重叠)。触发 registry 发现 (list_plugins 内部 _ensure_discovered)。
    """
    from codev_platform.plugins.registry import list_plugins

    owners: dict[str, set[str]] = {}
    for plugin in list_plugins():
        for kind in getattr(plugin, "produces", ()) or ():
            owners.setdefault(kind, set()).add(plugin.name)
    for kind, names in POST_PASS_OWNERS.items():
        owners.setdefault(kind, set()).update(names)
    return owners
