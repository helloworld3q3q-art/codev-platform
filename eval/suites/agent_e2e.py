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

from eval.suites._common import load_jsonl, token_match


def _mentions(answer: str, anchor: str) -> bool:
    """anchor 是否作为独立 token 出现在 answer —— 委托共享 `token_match`(单一真值源, recall
    suite 同用)。token 边界杜绝裸子串假阳("sql"⊄"sqlite"); _/. 算边界故 `x.classify_query(` 命中。"""
    return token_match(answer, anchor)


def score_case(case: dict, answer: str, tools_used: list[str], tool_call_count: int,
               budget: int | None = None) -> dict:
    """纯函数: 对单个 case 的答案 + 工具使用打确定性分。脱 AgentLoop, 可单测。"""
    must = case.get("must_mention", []) or []
    hit = [m for m in must if _mentions(answer, m)]
    coverage = round(len(hit) / len(must), 3) if must else 1.0
    bad = [m for m in (case.get("must_not", []) or []) if _mentions(answer, m)]
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


def _bootstrap_ci(values: list[float], *, iters: int = 2000, alpha: float = 0.05,
                  seed: int = 12345) -> list[float] | None:
    """对一组 per-case 分数做**确定性** bootstrap 百分位区间(默认 95%)。

    小集(n=10~12)+ grounding 取值聚在 0/0.5/1 非正态 → bootstrap 比正态近似更诚实
    (Phase A 红线: "n 小, CI 比点估诚实")。固定 seed 保可复现 + 可单测。
    n=0 → None; n=1 → 退化为点(区间宽 0, 诚实反映"单点无法估方差")。
    """
    n = len(values)
    if n == 0:
        return None
    if n == 1:
        return [round(values[0], 3), round(values[0], 3)]
    import random
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return [round(lo, 3), round(hi, 3)]


def aggregate(details: list[dict]) -> dict:
    """逐 case 子分 → suite 级均值/比率(+ grounding 的 bootstrap 95% CI, 供 Gate A 比组)。"""
    n = len(details)
    if not n:
        return {}
    gc_vals = [d["grounding_coverage"] for d in details]
    m = {
        "grounding_coverage": round(sum(gc_vals) / n, 3),
        "grounding_ci95": _bootstrap_ci(gc_vals),   # 跨 case 均值的 95% CI(false vs control 比 CI 重叠否)
        "grounding_n": n,
        "hallucination_rate": round(sum(1 for d in details if d["hallucinated"]) / n, 3),
        "tool_appropriate_rate": round(sum(1 for d in details if d["tool_appropriate"]) / n, 3),
        "within_budget_rate": round(sum(1 for d in details if d["within_budget"]) / n, 3),
    }
    judged = [d["judge_score"] for d in details if d.get("judge_score") is not None]
    if judged:
        m["judge_score_avg"] = round(sum(judged) / len(judged), 2)
        m["judged_n"] = len(judged)
    return m


def _skip(rows: list, reason: str) -> dict:
    return {"suite": "agent_e2e", "status": "skipped", "n": len(rows), "reason": reason}


_JUDGE_SYS = (
    "你是答案质量评审。给定[问题][评分标准(若有)][答案], 按以下标准给 1-5 整数分, **只输出数字**:\n"
    "5=完整正确且有据; 4=基本正确小遗漏; 3=部分正确或含糊; 2=大量缺失/跑题; 1=错误或答非所问。"
)


def judge_answer(case: dict, answer: str, provider) -> int | None:
    """LLM-judge: 按 rubric 给答案 1-5 整数分。provider 故障/输出非法 → None(不计入)。

    E3: grounding 测不到"答得对不对/顺不顺", judge 补这块。默认不调(噪声 + 成本), `--judge` 开。
    """
    if provider is None:
        return None
    from codev_platform.agent.brain.types import Message
    rubric = case.get("rubric", "")
    user = f"[问题] {case.get('query','')}\n[评分标准] {rubric or '(无, 按通用标准)'}\n[答案] {answer}"
    try:
        turn = provider.chat(_JUDGE_SYS, [Message(role="user", content=user)], [])
    except Exception:  # noqa: BLE001
        return None
    return _parse_score(turn.text if turn else None)


def _parse_score(text: str | None) -> int | None:
    """从 judge 文本抽 1-5 整数。取**首个完整数字 token**再校验范围 —— "10"/"2024" 不该被截成
    1/2(审计 #2), 范围外 → None。无数字 → None。"""
    if not text:
        return None
    digits = ""
    for ch in text.strip():
        if ch.isdigit():
            digits += ch
        elif digits:
            break          # 首个数字 token 结束
    if not digits:
        return None
    v = int(digits)
    return v if 1 <= v <= 5 else None


def _summarize_runs(case: dict, run_scores: list[dict], reps: int) -> dict:
    """把同 case 的 N 次跑分汇总成一条 detail: grounding 取均值 + 报 min/max 跨度(暴露方差);
    hallucinated 取并集(任一次幻觉即记); missing/tools/tool_calls 取最后一次代表。"""
    gc = [s["grounding_coverage"] for s in run_scores]
    mean_gc = round(sum(gc) / len(gc), 3)
    last = run_scores[-1]
    bad = sorted(set().union(*[set(s["hallucinated"]) for s in run_scores]))
    d = {
        "grounding_coverage": mean_gc,
        "grounding_min": round(min(gc), 3),
        "grounding_max": round(max(gc), 3),
        "missing_mentions": last["missing_mentions"],
        "hallucinated": bad,
        "hallucination_runs": sum(1 for s in run_scores if s["hallucinated"]),
        # 多数票(>50% 跑达标即记 True), reps=1 时即单次结果。
        "tool_appropriate": sum(1 for s in run_scores if s["tool_appropriate"]) * 2 >= reps,
        "within_budget": sum(1 for s in run_scores if s["within_budget"]) * 2 >= reps,
        "tools_used": last["tools_used"],
        "tool_calls": last["tool_calls"],
        "runs": reps,
    }
    return d


def run_agent_e2e(project_id: str, provider=None, policy=None, judge_provider=None,
                  dataset: str = "agent_e2e.jsonl", repeat: int = 1,
                  cross_project: bool = False) -> dict:
    """跑 agent loop 答每个 case + 确定性打分(+ 可选 LLM-judge)。

    provider=None → skip。policy=None → 用配置档(loop_policy()); 传入自定义 policy 支持 planner
    变体对比(E4 A/B)。judge_provider!=None → 每 case 额外 LLM-judge 1-5(E3); 可传**异于被测**的
    provider 做非自评(self-judge 偏宽实测给幻觉答案也 5.0)。
    repeat>1: 每 case 跑 N 次, grounding 取均值 + 报 min/max 跨度 —— 压小集非确定方差(质量面板:
    n=5/6 时 1 case 翻转 = ±0.17, 无均值无 CI 不可信)。
    dataset: 数据集文件名(default 易集 / hard 口语化 / quality 诊断难集 / false_premise / control)。
    cross_project=True: **不按 project_id 过滤**, 每 case 用各自 r["project_id"] 建 registry 全跑 ——
    false_premise/control 集跨 ≥2 项目防单仓过拟合(Phase A), 一次跑全集才好算跨组 CI。
    需 WSL 后端(工具调 codegraph/graph/chroma); 缺后端 loop 仍跑但 grounding 低(真实信号, 不额外 skip)。
    """
    rows = [r for r in load_jsonl(dataset)
            if cross_project or r.get("project_id", project_id) == project_id]
    if not rows:
        return _skip(rows, f"无 project={project_id} 的 agent_e2e 用例")
    if provider is None:
        return _skip(rows, "需 LLM provider(config + key)跑 agent loop; 缺则跳过(WSL 步)")

    from codev_platform.agent.brain.registry import loop_policy
    from codev_platform.agent.loop import AgentLoop
    from codev_platform.agent.tools import build_default_registry

    policy = policy or loop_policy()
    reps = max(1, repeat)
    details = []
    for r in rows:
        pid = r.get("project_id", project_id)
        run_scores = []
        last_answer = ""
        for _ in range(reps):
            loop = AgentLoop(provider, build_default_registry(pid), policy=policy)
            res = loop.run(r["query"])
            last_answer = res.answer
            tools = [s.tool for s in res.steps if s.tool]
            run_scores.append(score_case(r, res.answer, tools, len(tools), budget=policy.max_steps))
        d = {"query": r["query"], "expect_type": r.get("expect_type", ""),
             **_summarize_runs(r, run_scores, reps)}
        if judge_provider is not None:
            d["judge_score"] = judge_answer(r, last_answer, judge_provider)
        d["project_id"] = pid
        details.append(d)
    pid_label = (",".join(sorted({d["project_id"] for d in details}))
                 if cross_project else project_id)
    return {"suite": "agent_e2e", "status": "ok", "n": len(details), "repeat": reps,
            "project_id": pid_label, "metrics": aggregate(details), "details": details}


def run_planner_e2e_ab(project_id: str, provider=None,
                       dataset: str = "agent_e2e.jsonl") -> dict:
    """E4: 同数据集跑 3 个 planner 变体, 比答案质量 —— 验"分类更准→答案更好"。

    变体: off(planner 关) / keyword(planner 开, 关键词分类) / llm(planner 开, LLM 分类)。
    dataset=agent_e2e_hard.jsonl(口语化硬集, keyword 多误判 general)才能拉开 keyword vs llm 差;
    易集上三变体趋同(分类一致 + grounding 饱和)。⚠️ 小集是**趋势**非定论([[recall-weight-ab-finding]])。
    """
    from codev_platform.agent.policy import LoopPolicy

    variants = {
        "off": LoopPolicy(planner_enabled=False),
        "keyword": LoopPolicy(planner_enabled=True, planner_llm_enabled=False),
        "llm": LoopPolicy(planner_enabled=True, planner_llm_enabled=True),
    }
    reps = {name: run_agent_e2e(project_id, provider=provider, policy=pol, dataset=dataset)
            for name, pol in variants.items()}
    any_ok = any(r["status"] == "ok" for r in reps.values())
    n = next((r["n"] for r in reps.values() if "n" in r), 0)
    # 扁平化关键指标进 metrics(_print_human/总分友好), 完整逐变体留 variants。
    flat: dict = {}
    for name, r in reps.items():
        if r["status"] == "ok":
            for mk in ("grounding_coverage", "tool_appropriate_rate", "within_budget_rate"):
                flat[f"{name}.{mk}"] = r["metrics"].get(mk)
    out = {
        "suite": "agent_e2e_planner_ab",
        "status": "ok" if any_ok else "skipped",
        "n": n,
        "project_id": project_id,
        "metrics": flat,
        "variants": {name: (r["metrics"] if r["status"] == "ok" else r.get("reason"))
                     for name, r in reps.items()},
    }
    if not any_ok:
        out["reason"] = next((r.get("reason") for r in reps.values()), "skipped")
    return out
