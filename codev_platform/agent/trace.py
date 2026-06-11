"""每步 trace 落 jsonl(可观测 + 未来 eval 输入).

落在 data_root/agent_trace/<date>.jsonl。失败不抛(trace 不该拖垮 agent)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _trace_dir() -> Path:
    try:
        from codev_platform.core.paths import data_root
        base = Path(data_root())
    except Exception:  # noqa: BLE001
        base = Path.home() / ".codev-platform" / "data"
    d = base / "agent_trace"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Trace:
    def __init__(self, session_id: str, provider: str, model: str) -> None:
        self.session_id = session_id
        self.provider = provider
        self.model = model

    def _write(self, record: dict[str, Any]) -> None:
        record.update(
            ts=time.time(), session_id=self.session_id,
            provider=self.provider, model=self.model,
        )
        try:
            path = _trace_dir() / (time.strftime("%Y-%m-%d") + ".jsonl")
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — trace 失败静默
            pass

    def plan(self, query_type: str, tool_budget: int, preferred_lanes: list[str]) -> None:
        """planner(Phase 7)产出的查询计划落 trace,供 debug 面板 + eval 复盘。"""
        self._write({
            "event": "plan", "query_type": query_type,
            "tool_budget": tool_budget, "preferred_lanes": preferred_lanes,
        })

    def step(self, n: int, thought: str | None, tool: str | None, args: Any, result_summary: str | None) -> None:
        self._write({
            "event": "step", "n": n, "thought": thought,
            "tool": tool, "args": args, "result_summary": result_summary,
        })

    def done(self, stop_reason: str, steps: int, usage: dict[str, int] | None = None) -> None:
        """收尾事件。usage(input/output/cache_hit/miss tokens)落 trace —— 每查询 token+缓存
        足迹进 jsonl(带 session_id)= per-租户成本计量 + 缓存率监控的地基(Phase 8 可观测)。
        成本($)由下游按 provider/model 价表算, trace 只记中性 token, 不硬编单价。"""
        rec = {"event": "done", "stop_reason": stop_reason, "steps": steps}
        if usage:
            rec["usage"] = usage
        self._write(rec)
