"""AnalyzerPlugin 协议 — 插件与核心之间的契约 (Phase 2).

定位:把 codev-platform 升级为"模块化核心 + 插件化扩展"全链路 AI 平台时,客户差异
能力 (Frontend / Backend / Database / Connector / CodeGraph) 都以 AnalyzerPlugin 形式
接入。核心只依赖本协议,不关心具体语言 / 框架 / 外部系统:

    name:     插件唯一标识 (如 "builtin.sql" / "frontend.vue")。
    version:  语义化版本 (用于审计 + 增量 reindex 归因)。
    detect()  给定仓库路径,判断本插件是否适用 (技术栈命中)。
    analyze() 实际分析,返回统一图谱模型 AnalyzerResult (nodes/edges/evidences/findings)。

铁律 (plan §改造原则):
    - 插件输出必须是 AnalyzerResult (schema 校验在 executor 层兜底)。
    - 插件失败不能拖垮核心 (异常隔离在 executor 层,本协议只定义"应该怎样")。

风格:用 abc.ABC + Protocol 双轨 —— 内置插件继承 AnalyzerPlugin (ABC) 获得默认实现;
鸭子类型 / 第三方也可只满足 AnalyzerPluginProtocol。与 agent/brain/base.py:LLMProvider
策略接口一致 (核心只依赖接口,具体实现可替换)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Protocol, runtime_checkable

from codev_platform.graph.schema import AnalyzerResult


@runtime_checkable
class AnalyzerPluginProtocol(Protocol):
    """鸭子类型契约:任何拥有 name/version/detect/analyze 的对象都可当插件用。

    第三方插件 (后续放开时) 无需继承 ABC,只要结构匹配即可被 registry 接纳。
    """

    name: str
    version: str

    def detect(self, repo_path: Path) -> bool: ...

    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult: ...


class AnalyzerPlugin(ABC):
    """内置插件基类:子类必须声明 name/version + 实现 detect/analyze。

    name / version 用类属性声明 (子类覆盖);也可在 __init__ 设实例属性。
    """

    name: str = ""
    version: str = "0.0.0"

    @abstractmethod
    def detect(self, repo_path: Path) -> bool:
        """给定仓库根路径,判断本插件是否适用 (是否命中其技术栈 / 外部系统)。

        必须是廉价探测 (查标志文件 / 目录结构),不做完整扫描。
        """
        raise NotImplementedError

    @abstractmethod
    def analyze(self, repo_path: Path, project_id: str) -> AnalyzerResult:
        """对仓库做分析,返回统一图谱模型。

        返回值必须是 AnalyzerResult;失败应抛异常 (由 executor 捕获降级),
        不要吞掉异常返回空结果 —— 那样核心无法区分"无发现"与"分析失败"。
        """
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - 调试便利
        return f"<AnalyzerPlugin {self.name} v{self.version}>"
