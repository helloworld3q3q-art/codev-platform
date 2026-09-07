"""平台栈能力的只读派生视图 —— capability matrix (Phase 9)。

**不是新真值源**: 聚合已注册插件的自描述 (name / version / produces / prov_source) 成
一张"本平台支持哪些语言栈、各产什么 NodeKind、边来源类是什么"的视图。CLI `plugins list`
与审计测试消费它。加语言**零改本文件** —— describe_capabilities 自动纳入新注册插件。

与 ownership.kind_owners() 同源 (都读插件 produces), 但视角不同: kind_owners 是
kind -> owners (归属约束), 本视图是 plugin -> 能力 (面向人/CLI 的栈能力清单)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StackCapability:
    """一个 analyzer 插件 (= 一个语言/框架栈适配器) 的能力描述。

    plugin:      插件唯一名 (如 "builtin.backend_spring")。
    version:     语义化版本。
    produces:    该插件 analyze 产出的架构级 NodeKind 值 (来自 plugin.produces 声明)。
    prov_source: 该插件所产边的默认来源类 (graph.schema.ProvSource 值; None = 不产边/未声明)。
    """

    plugin: str
    version: str
    produces: tuple[str, ...]
    prov_source: str | None


def describe_capabilities() -> list[StackCapability]:
    """聚合所有已注册插件的能力 (按 plugin 名排序; list_plugins 已稳定排序)。"""
    from codev_platform.plugins.registry import list_plugins

    return [
        StackCapability(
            plugin=p.name,
            version=getattr(p, "version", "0.0.0"),
            produces=tuple(getattr(p, "produces", ()) or ()),
            prov_source=getattr(p, "prov_source", None),
        )
        for p in list_plugins()
    ]
