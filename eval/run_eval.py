"""eval harness runner (CLI 入口) —— dispatch 到各 suite + 汇总输出。

用法:
    python eval/run_eval.py --suite all
    python eval/run_eval.py --suite codegraph
    python eval/run_eval.py --suite retrieval            # 需 chroma daemon + 模型
    python eval/run_eval.py --suite recall  --project codev-platform
    python eval/run_eval.py --suite all --json           # 机器可读输出

各 suite 实现在 eval/suites/<name>.py(一 suite 一模块, 单一职责); 指标定义见 eval/metrics.py;
数据集在 eval/datasets/*.jsonl。新增 suite = 加一个 suite 模块 + 下方 _RUNNERS 加一行。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许 `python eval/run_eval.py` 直接跑 (把仓根加进 path)
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.suites._common import (  # noqa: E402
    DEFAULT_CODE_INTEL_PID,
    DEFAULT_GRAPH_PID,
    DEFAULT_RECALL_PID,
    DEFAULT_RETRIEVAL_PID,
)
from eval.suites.agent_e2e import run_agent_e2e, run_planner_e2e_ab  # noqa: E402
from eval.suites.code_intelligence import run_code_intelligence  # noqa: E402
from eval.suites.codegraph import run_codegraph  # noqa: E402
from eval.suites.memory import run_memory  # noqa: E402
from eval.suites.planner import run_planner  # noqa: E402
from eval.suites.recall import run_recall  # noqa: E402
from eval.suites.retrieval import run_retrieval  # noqa: E402

# suite 名 → (project, k) -> result 的 runner。dispatch 唯一真值源(--suite choices / "all" 都派生自它)。
_RUNNERS: dict[str, callable] = {
    "retrieval": lambda project, k: run_retrieval(project or DEFAULT_RETRIEVAL_PID, k=k),
    "codegraph": lambda project, k: run_codegraph(project or DEFAULT_GRAPH_PID),
    "memory": lambda project, k: run_memory(),
    "code_intelligence": lambda project, k: run_code_intelligence(project or DEFAULT_CODE_INTEL_PID),
    "planner": lambda project, k: run_planner(),
    "recall": lambda project, k: run_recall(project or DEFAULT_RECALL_PID, k=k),
    "agent_e2e": lambda project, k: run_agent_e2e(project or DEFAULT_RECALL_PID),
}


def _print_human(results: list[dict]) -> None:
    total_ok = 0
    total_metric = 0.0
    for res in results:
        suite = res["suite"]
        print(f"\n=== suite: {suite} ===")
        if res["status"] == "skipped":
            print(f"  [SKIPPED] {res['reason']}")
            print(f"  ({res['n']} cases 未跑)")
            continue
        print(f"  status: ok | cases: {res['n']} | project: {res.get('project_id')}")
        for mk, mv in res["metrics"].items():
            print(f"    {mk:<20} = {mv}")
        # memory suite: 显式报告 recall 子集是否被 skip (PG 缺)
        if res["suite"] == "memory":
            rc = res.get("recall", {})
            if rc.get("status") == "skipped":
                print(f"    recall 子集 [SKIPPED] {rc.get('reason')}")
            elif rc.get("status") == "ok":
                print(f"    recall 子集 ok | seed cleaned_rows = {rc.get('cleaned_rows')} "
                      f"(namespace {rc.get('namespace')})")
        # code_intelligence: 显式报告每个子 suite 的标注/未标注分布
        if res["suite"] == "code_intelligence":
            for name, s in res.get("sub", {}).items():
                print(f"    [{name}] {s['correct']}/{s['total']} 准 "
                      f"(未标注 {s['unlabeled']})")
        # planner: 显式报告分类错的 case(便于补词表)
        if res["suite"] == "planner":
            for d in res.get("details", []):
                if not d["ok"]:
                    print(f"    [miss] {d['expect']}!={d['got']}: {d['query']}")
        # 取一个代表性指标进总分 (recall@5 / recall / hit_rate / accuracy)
        m = res["metrics"]
        primary = (m.get("recall@5") or m.get("recall") or m.get("hit_rate")
                   or m.get("resolution_accuracy") or m.get("arch_role_accuracy")
                   or m.get("business_domain_accuracy") or m.get("classification_accuracy")
                   or m.get("grounding_coverage")
                   or 0.0)
        total_metric += primary
        total_ok += 1
    print("\n=== 总分 ===")
    if total_ok:
        print(f"  跑通 {total_ok} suite, 主指标均分 = {round(total_metric / total_ok, 3)}")
    else:
        print("  无 suite 跑通 (全部 skipped) —— 见上方 reason 准备后端。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="codev-platform eval harness")
    ap.add_argument("--suite", choices=[*_RUNNERS, "all"], default="all")
    ap.add_argument("--project", default=None, help="project_id 覆盖 (默认按 suite 选)")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    ap.add_argument("-k", type=int, default=5, help="retrieval/recall top-k (默认 5)")
    ap.add_argument("--llm", action="store_true",
                    help="planner suite: 额外用配置的 LLM provider 跑 keyword vs LLM A/B (需 WSL + key)")
    ap.add_argument("--judge", action="store_true",
                    help="agent_e2e: 额外 LLM-judge 给答案 1-5 主观质量分 (E3, 多一次 LLM 调用)")
    ap.add_argument("--planner-ab", action="store_true",
                    help="agent_e2e: 跑 planner off/keyword/llm 三变体比答案质量 (E4)")
    ap.add_argument("--hard-e2e", action="store_true",
                    help="agent_e2e: 用口语化硬集 agent_e2e_hard.jsonl (keyword 多误路由, 拉开 keyword vs llm)")
    args = ap.parse_args(argv)

    suites = list(_RUNNERS) if args.suite == "all" else [args.suite]

    # 构造配置的 LLM provider: agent_e2e 必需(跑真 loop); planner 仅 --llm 时需要(A/B)。
    # 失败(缺 key/依赖)→ None → planner 退纯关键词, agent_e2e skip。
    provider = None
    if args.llm or "agent_e2e" in suites:
        try:
            from codev_platform.agent.brain.registry import get_provider
            provider = get_provider()
        except Exception as exc:  # noqa: BLE001
            print(f"[eval] 构造 LLM provider 失败 (planner 退关键词 / agent_e2e 将 skip): {exc}",
                  file=sys.stderr)

    runners = dict(_RUNNERS)
    if args.llm:
        runners["planner"] = lambda project, k: run_planner(provider=provider)
    e2e_ds = "agent_e2e_hard.jsonl" if args.hard_e2e else "agent_e2e.jsonl"
    if args.planner_ab:
        runners["agent_e2e"] = lambda project, k: run_planner_e2e_ab(
            project or DEFAULT_RECALL_PID, provider=provider, dataset=e2e_ds)
    else:
        runners["agent_e2e"] = lambda project, k: run_agent_e2e(
            project or DEFAULT_RECALL_PID, provider=provider,
            judge_provider=(provider if args.judge else None), dataset=e2e_ds)

    results = [runners[s](args.project, args.k) for s in suites]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_human(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
