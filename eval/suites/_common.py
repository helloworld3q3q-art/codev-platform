"""eval suite 共享层 —— dataset 载入 + 默认 project_id。

各 suite 模块只依赖本文件 + eval.metrics(纯指标), 不互相依赖, 也不依赖 run_eval(CLI 入口)。
"""
from __future__ import annotations

import json
from pathlib import Path

# eval/suites/_common.py → parents[1] = eval/
_DATASETS = Path(__file__).resolve().parents[1] / "datasets"

# 各 suite 默认 project_id(可被 --project 覆盖)。
DEFAULT_RETRIEVAL_PID = "codev-platform"   # 平台自身文档已索引
DEFAULT_GRAPH_PID = "openclaw-stock"        # codegraph 真实链路数据在该项目
DEFAULT_CODE_INTEL_PID = "codev-platform"   # A1/A2 软标签 analyzer 上线在平台
DEFAULT_RECALL_PID = "codev-platform"       # 双 lane(graph + codegraph)齐全


def load_jsonl(name: str) -> list[dict]:
    """读 eval/datasets/<name>.jsonl → dict 列表(跳空行)。"""
    rows: list[dict] = []
    with (_DATASETS / name).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
