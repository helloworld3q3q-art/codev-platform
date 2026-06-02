"""插件执行隔离 + 结果校验 (Phase 2 plugin runtime: executor + result validator).

铁律 (decision doc §改造原则 6/7/8):
    6. 插件失败不能影响核心服务可用性 —— analyze() 抛任何异常都被捕获,转成带
       error/错误码的降级结果,绝不向上冒泡拖垮调用方。
    8. 插件执行必须可观测 —— 记录耗时 / 错误码 / 输出摘要。

结果校验 (decision doc §7):所有插件输出必须经 schema 校验 —— analyze() 必须返回
AnalyzerResult,否则视为 INVALID_RESULT 错误码降级。

输出:ExecutionResult (一次插件执行的可观测产出),不论成功失败都返回它,核心据此
聚合 (成功的并入图谱,失败的入审计 + 告警),从不抛异常给调用方。
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codev_platform.graph.schema import AnalyzerResult


# 错误码 (decision doc §插件错误码) —— 稳定标识,便于审计统计 + 告警归类。
ERR_DETECT_FAILED = "DETECT_FAILED"          # detect() 抛异常
ERR_ANALYZE_FAILED = "ANALYZE_FAILED"        # analyze() 抛异常
ERR_INVALID_RESULT = "INVALID_RESULT"        # analyze() 返回的不是 AnalyzerResult
ERR_NOT_APPLICABLE = "NOT_APPLICABLE"        # detect() 返回 False (非错误,跳过)


@dataclass
class ExecutionResult:
    """一次插件执行的可观测产出 (成功 / 失败 / 跳过统一用它)。

    plugin / plugin_version: 来源归因。
    ok:        True=拿到合法 AnalyzerResult;False=失败或被跳过。
    result:    成功时的 AnalyzerResult;失败 / 跳过时 None。
    error_code: 失败 / 跳过的错误码 (见本模块常量);成功时 None。
    error:     人类可读错误说明 (含异常类型 + 消息);成功时 ""。
    elapsed_ms: 执行耗时 (含 detect + analyze)。
    summary:   输出摘要 (nodes/edges/findings 计数),便于审计日志。
    """

    plugin: str
    plugin_version: str
    ok: bool
    result: AnalyzerResult | None = None
    error_code: str | None = None
    error: str = ""
    elapsed_ms: float = 0.0
    summary: dict[str, Any] = field(default_factory=dict)


def _summarize(result: AnalyzerResult) -> dict[str, Any]:
    return {
        "nodes": len(result.nodes),
        "edges": len(result.edges),
        "evidences": len(result.evidences),
        "findings": len(result.findings),
    }


def run_plugin(
    plugin: Any,
    repo_path: Path | str,
    project_id: str,
    *,
    run_detect: bool = True,
) -> ExecutionResult:
    """隔离执行单个插件:detect (可选) -> analyze -> 校验返回类型。

    任何阶段异常都被捕获,转成带 error_code 的降级 ExecutionResult,不向上抛。

    run_detect=False 时跳过 detect (调用方已确认适用,直接 analyze)。
    """
    name = getattr(plugin, "name", repr(plugin))
    version = getattr(plugin, "version", "0.0.0")
    repo = Path(repo_path)
    t0 = time.perf_counter()

    if run_detect:
        try:
            applicable = plugin.detect(repo)
        except Exception as exc:  # noqa: BLE001 — 插件不可信,任何异常都降级
            return ExecutionResult(
                plugin=name, plugin_version=version, ok=False,
                error_code=ERR_DETECT_FAILED,
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            )
        if not applicable:
            return ExecutionResult(
                plugin=name, plugin_version=version, ok=False,
                error_code=ERR_NOT_APPLICABLE,
                error="detect() returned False (plugin not applicable to this repo)",
                elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            )

    try:
        result = plugin.analyze(repo, project_id)
    except Exception as exc:  # noqa: BLE001 — 隔离:插件 analyze 崩了不拖垮核心
        return ExecutionResult(
            plugin=name, plugin_version=version, ok=False,
            error_code=ERR_ANALYZE_FAILED,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        )

    # 结果 schema 校验:必须是 AnalyzerResult。
    if not isinstance(result, AnalyzerResult):
        return ExecutionResult(
            plugin=name, plugin_version=version, ok=False,
            error_code=ERR_INVALID_RESULT,
            error=f"analyze() must return AnalyzerResult, got {type(result).__name__}",
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        )

    # 补全来源归因 (插件没填时由 executor 兜底)。
    if result.plugin is None:
        result.plugin = name
    if result.plugin_version is None:
        result.plugin_version = version

    elapsed = (time.perf_counter() - t0) * 1000.0
    return ExecutionResult(
        plugin=name, plugin_version=version, ok=True,
        result=result, elapsed_ms=elapsed, summary=_summarize(result),
    )
