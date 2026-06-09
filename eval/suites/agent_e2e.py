"""agent 端到端 eval suite (Phase 7 完整版, E1+E2)。

测 agent **完整回答质量**, 不止 planner 分类。设计见
`docs/plans/roadmap-2026-06-07/agent-e2e-eval-design-2026-06-09.md`。

打分以**确定性 grounding 为主**(对答案措辞鲁棒, 零额外 LLM 成本):
- grounding_coverage: 答案命中 must_mention(真实文件/符号/表)比例。
- hallucination: 答案出现 must_not(不存在物)→ 标记。
- tool_appropriate: trace 是否用到 expect_tools 中任一类。
- within_budget: 工具调用数 ≤ 预算(planner)/ max_steps。
LLM-judge(主观质量)留 E3, 默认不做。

`score_case` 是纯函数(脱 AgentLoop), Windows 单测即可钉死。`run_agent_e2e` 跑真 loop ——
**需 provider + WSL 后端**(codegraph/graph/chroma daemon), 缺 provider 优雅 skip(像 recall suite)。
"""
from __future__ import annotations

from eval.suites._common import load_jsonl


def _norm(s: str | None) -> str:
    return (s or "").lower()


def score_case(case: dict, answer: str, tools_used: list[str], tool_call_count: int,
               budget: int | None = None) -> dict:
    """纯函数: 对单个 case 的答案 + 工具使用打确定性分。脱 AgentLoop, 可单测。"""
    a = _norm(answer)
    must = case.get("must_mention", []) or []
    hit = [m for m in must if _norm(m) in a]
    coverage = round(len(hit) / len(must), 3) if must else 1.0
    bad = [m for m in (case.get("must_not", []) or []) if _norm(m) in a]
    expect_tools = set(case.get("expect_tools", []) or [])
    used = set(t for t in (tools_used or []) if t)
    # 至少用到一个期望工具类 = 路子对(不要求精确集合, 工具可多可少)。无期望则恒 True。
    tool_ok = (not expect_tools) or bool(expect_tools & used)
    within = True if not budget or budget <= 0 else tool_call_count <= budget
    return {
        "grounding_coverage": coverage,
        "missing_mentions": [m for m in must if m not in hit],
        "hallucinated": bad,                 # 非空 = 出现禁止项(幻觉)
        "tool_appropriate": tool_ok,
        "within_budget": within,
        "tools_used": sorted(used),
        "tool_calls": tool_call_count,
    }


def aggregate(details: list[dict]) -> dict:
    """逐 case 子分 → suite 级均值/比率。"""
    n = len(details)
    if not n:
        return {}
    return {
        "grounding_coverage": round(sum(d["grounding_coverage"] for d in details) / n, 3),
        "hallucination_rate": round(sum(1 for d in details if d["hallucinated"]) / n, 3),
        "tool_appropriate_rate": round(sum(1 for d in details if d["tool_appropriate"]) / n, 3),
        "within_budget_rate": round(sum(1 for d in details if d["within_budget"]) / n, 3),
    }


def _skip(rows: list, reason: str) -> dict:
    return {"suite": "agent_e2e", "status": "skipped", "n": len(rows), "reason": reason}


def run_agent_e2e(project_id: str, provider=None) -> dict:
    """跑 agent loop 答每个 case + 确定性打分。

    provider=None(run_eval 构造失败/缺 key)→ skip。需 WSL 后端(工具调 codegraph/graph/chroma);
    缺后端时 loop 仍跑但答案 grounding 会低 —— 那是真实信号, 不额外 skip(只 provider 是硬前提)。
    """
    rows = [r for r in load_jsonl("agent_e2e.jsonl")
            if r.get("project_id", project_id) == project_id]
    if not rows:
        return _skip(rows, f"无 project={project_id} 的 agent_e2e 用例")
    if provider is None:
        return _skip(rows, "需 LLM provider(config + key)跑 agent loop; 缺则跳过(WSL 步)")

    from codev_platform.agent.brain.registry import loop_policy
    from codev_platform.agent.loop import AgentLoop
    from codev_platform.agent.tools import build_default_registry

    policy = loop_policy()
    details = []
    for r in rows:
        pid = r.get("project_id", project_id)
        loop = AgentLoop(provider, build_default_registry(pid), policy=policy)
        res = loop.run(r["query"])
        tools = [s.tool for s in res.steps if s.tool]
        sc = score_case(r, res.answer, tools, len(tools), budget=policy.max_steps)
        details.append({"query": r["query"], "expect_type": r.get("expect_type", ""),
                        "stop_reason": res.stop_reason, **sc})
    return {"suite": "agent_e2e", "status": "ok", "n": len(details),
            "project_id": project_id, "metrics": aggregate(details), "details": details}
