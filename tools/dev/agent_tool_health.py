#!/usr/bin/env python
"""agent 工具体检 —— 逐个直调注册表里的 9 个工具(绕开模型 + loop guard),报 N/9。

为什么:模型驱动的"问一个问题验证所有工具"不可靠(弱模型重复同一工具被 loop guard 拦、
选错项目查空索引、GPU OOM 等),无法判断到底是工具坏了还是模型/环境问题。本脚本直接
`tool.run(probe)`,把"工具本身能不能跑通"和"模型会不会用"彻底分开。

用法(在平台机 / WSL,平台 venv):
    .venv/bin/python tools/dev/agent_tool_health.py [project_id]
默认 project_id=codev-platform;探针实体(ChatService / agent_messages / ChatPanel / agentChat)
按本仓调好,换项目需相应改 PROBES 的 ref。

退出码:0 = 全通过,1 = 有工具失败(CI / 巡检可用)。
"""
from __future__ import annotations

import sys

from codev_platform.agent.runctx import RunContext, set_run_context
from codev_platform.agent.tools import build_default_registry

# (工具名, 探针入参)。ref 取本仓真实实体,保证非空命中。
PROBES = [
    ("codegraph_search", {"query": "ChatService"}),
    ("codegraph_callers", {"name": "build_default_registry"}),
    ("codegraph_callees", {"name": "build_default_registry"}),
    ("impact_analysis", {"nodeRef": "agent_messages"}),
    ("table_usage", {"table": "agent_messages"}),
    ("page_dependencies", {"pageRef": "ChatPanel"}),
    ("api_callers", {"endpointRef": "agentChat"}),
    ("search_docs", {"query": "会话持久化 工具调用流"}),
    ("remember", {"content": "[agent_tool_health] probe, 可忽略"}),
]


def _is_failure(content: str, is_error: bool) -> bool:
    """除工具自报 is_error 外,再嗅探下游 daemon 回灌的错误体(search_docs 的 CUDA OOM
    走 SSE 返回 {"error": ...},工具层 is_error=False 会被误判 OK)。"""
    if is_error:
        return True
    low = content or ""
    return '"error"' in low or "CUDA error" in low or "不可用" in low


def main() -> int:
    pid = sys.argv[1] if len(sys.argv) > 1 else "codev-platform"
    set_run_context(RunContext(user_id="healthcheck", org_id="default", project_id=pid, task_id=None))
    reg = build_default_registry(pid)

    ok = 0
    for name, args in PROBES:
        tool = reg.get(name)
        if tool is None:
            print(f"[MISSING] {name}")
            continue
        try:
            res = tool.run(args)
            content = res.content or ""
            failed = _is_failure(content, bool(getattr(res, "is_error", False)))
        except Exception as e:  # noqa: BLE001 — 体检要捕获任何异常并归为 FAIL
            content = f"{type(e).__name__}: {e}"
            failed = True
        if not failed:
            ok += 1
        print(f"[{'FAIL' if failed else 'OK'}] {name} :: {content.replace(chr(10), ' ')[:90]}")

    total = len(PROBES)
    print(f"=== {ok}/{total} tools OK (project={pid}) ===")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
