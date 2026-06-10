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
