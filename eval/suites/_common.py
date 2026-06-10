"""eval suite 共享层 —— dataset 载入 + 默认 project_id。

各 suite 模块只依赖本文件 + eval.metrics(纯指标), 不互相依赖, 也不依赖 run_eval(CLI 入口)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# eval/suites/_common.py → parents[1] = eval/
_DATASETS = Path(__file__).resolve().parents[1] / "datasets"

# 各 suite 默认 project_id(可被 --project 覆盖)。
DEFAULT_RETRIEVAL_PID = "codev-platform"   # 平台自身文档已索引
DEFAULT_GRAPH_PID = "openclaw-stock"        # codegraph 真实链路数据在该项目
DEFAULT_CODE_INTEL_PID = "codev-platform"   # A1/A2 软标签 analyzer 上线在平台
DEFAULT_RECALL_PID = "codev-platform"       # 双 lane(graph + codegraph)齐全


def token_match(text: str | None, anchor: str | None) -> bool:
    """anchor 是否作为**独立 token** 出现在 text(大小写不敏感, 非字母数字为边界)。

    eval 金标锚点匹配的单一真值源(recall / agent_e2e 共用)。杜绝裸子串假阳:
    "sql" 不命中 "sqlite"、"impact" 不命中 "reportimpact"; 但 _/. 算边界, 故
    "assess_soft" 命中 "assess_soft_labels"、"classify_query" 命中 "x.classify_query("。
    """
    a = (anchor or "").strip()
    if not a:
        return False
    pat = r"(?<![A-Za-z0-9])" + re.escape(a) + r"(?![A-Za-z0-9])"
    return re.search(pat, text or "", re.IGNORECASE) is not None


def load_jsonl(name: str) -> list[dict]:
    """读 eval/datasets/<name>.jsonl → dict 列表(跳空行)。"""
    rows: list[dict] = []
    with (_DATASETS / name).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _bootstrap_ci(values: list[float], *, iters: int = 2000, alpha: float = 0.05,
                  seed: int = 12345) -> list[float] | None:
    """对一组 per-case 分数做**确定性** bootstrap 百分位区间(默认 95%)。

    eval CI 的单一真值源(agent_e2e grounding 均值 + recall 的 paired delta 共用)。小集(n=6~18)
    + 取值非正态 → bootstrap 比正态近似更诚实。固定 seed 保可复现 + 可单测。values 可含负数
    (如 weighted-uniform 的 paired delta): CI 不含 0 → 差异显著, 含 0 → 不可判(同 Gate A 语义)。
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
