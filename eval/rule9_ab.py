"""rule9 on/off A/B —— 单独量 CODE_UNDERSTANDING_SYSTEM 规则9(抗错误前提)的净效。

闭合 daily-summary §三十 留的 follow-up: 难集首跑的 hallucination 改善大半来自修金标(去假阳),
规则9 净效被混淆。这里在**已修好的** quality 难集上做 rule9 on(默认全 prompt)vs off(同 prompt
去掉规则9 那行)的对照, 隔离规则9 自身效果。

用法(WSL, 需 provider + 后端): python -m eval.rule9_ab [--project codev-platform] [--repeat 2]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from codev_platform.agent.brain.registry import get_provider  # noqa: E402
from codev_platform.agent.prompts import CODE_UNDERSTANDING_SYSTEM  # noqa: E402
from eval.suites.agent_e2e import run_agent_e2e  # noqa: E402

_DATASET = "agent_e2e_quality.jsonl"


def _without_rule9(prompt: str) -> str:
    """去掉规则9 那行(以 '9. ' 开头)—— off 变体。其余 prompt 逐字不动。"""
    kept = [ln for ln in prompt.splitlines() if not ln.lstrip().startswith("9. ")]
    return "\n".join(kept)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="rule9 on/off A/B on agent_e2e quality set")
    ap.add_argument("--project", default="codev-platform")
    ap.add_argument("--repeat", type=int, default=2)
    args = ap.parse_args(argv)

    provider = get_provider()
    off_prompt = _without_rule9(CODE_UNDERSTANDING_SYSTEM)
    if off_prompt == CODE_UNDERSTANDING_SYSTEM:
        print("WARN: 未找到规则9 行(prompt 可能已改), off 变体与 on 相同", file=sys.stderr)

    on = run_agent_e2e(args.project, provider=provider, dataset=_DATASET, repeat=args.repeat)
    off = run_agent_e2e(args.project, provider=provider, dataset=_DATASET, repeat=args.repeat,
                        system=off_prompt)
    mo, mf = on["metrics"], off["metrics"]
    print("rule9 ON  :", mo)
    print("rule9 OFF :", mf)
    print("DELTA(on-off): hallucination_rate=%s  grounding_coverage=%s" % (
        round(mo["hallucination_rate"] - mf["hallucination_rate"], 3),
        round(mo["grounding_coverage"] - mf["grounding_coverage"], 3),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
