"""builtin.cross_link — 把现有 cross-link sqlite 适配器包成首个内置 AnalyzerPlugin。

Phase1 (graph/adapters/cross_link.py: build_cross_link_result) 已把 cross_layer.sqlite
映射成统一 AnalyzerResult; 本插件只是给它套上 AnalyzerPlugin 协议外壳, 接入 registry,
让 `codev-platform plugins list` 能看见、executor 能隔离执行。**不改适配器现有代码**,
纯薄封装 + 委派。

detect() 选择 —— 恒返回 True:
    cross-link 是平台核心能力 (不像 frontend.vue 那样按技术栈命中), 任何被平台索引的
    project 都"潜在适用"。是否真有 cross-link 数据 (sqlite 是否存在) 由 analyze 处理:
    缺库时 build_cross_link_result 返回空 AnalyzerResult, 不抛。把"缺库"当成"无发现"
    而非"不适用", 与适配器既定语义 (缺索引返空) 对齐; 也避免 detect 重复做一次
    库存在性探测 (路径解析与 analyze 内部一致, 单一真值源在适配器)。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.adapters.cross_link import (
    PLUGIN_NAME,
    build_cross_link_result,
)
from codev_platform.graph.schema import AnalyzerResult
from codev_platform.plugins.base import AnalyzerPlugin


class CrossLinkPlugin(AnalyzerPlugin):
    """内置插件: 暴露 cross-link 跨层图谱 (table/endpoint/api 节点 + 读写/调用边)。"""

    name = PLUGIN_NAME  # "builtin.cross_link" (单一真值源在适配器)
    version = "0.1.0"

    def detect(self, repo_path: Path) -> bool:
        # 恒 True: cross-link 是平台核心能力, 缺库由 analyze 返空处理 (见模块 docstring)。
        return True

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        # 纯委派现有 Phase1 适配器; 缺索引返空 AnalyzerResult, 不抛。
        return build_cross_link_result(project_id)
