"""code_recall agent 工具单测 (agent/tools/recall.py) —— 注册 + 入参校验 + 序列化。"""
from __future__ import annotations

import json

from codev_platform.agent.tools import build_default_registry
from codev_platform.agent.tools.recall import CodeRecallTool
from codev_platform.recall.service import CodeRecallHit


def test_code_recall_registered_in_default_registry():
    reg = build_default_registry("p")
    t = reg.get("code_recall")
    assert t is not None and t.name == "code_recall"
    # spec 可交给 provider(name/description/input_schema 齐)
    spec = next(s for s in reg.specs() if s["name"] == "code_recall")
    assert "query" in spec["input_schema"]["properties"]


def test_code_recall_empty_query_is_error():
    r = CodeRecallTool("p").run({"query": "   "})
    assert r.is_error and "query" in r.content


def test_code_recall_serializes_hits(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.recall.recall_code",
        lambda q, pid, **kw: [
            CodeRecallHit(ref="r1", score=0.5, name="weighted_rrf", kind="function",
                          file="codev_platform/recall/fusion.py", lanes=["codegraph"]),
            CodeRecallHit(ref="r2", score=0.4, name="reportImpact", kind="backend_endpoint",
                          file="web/routes/reports.py", lanes=["graph"]),
        ])
    r = CodeRecallTool("p").run({"query": "weighted rrf fusion", "limit": 5})
    assert not r.is_error
    data = json.loads(r.content)
    assert data["count"] == 2
    assert data["lanes"] == ["codegraph", "graph"]            # union 实际贡献 lane
    assert data["hits"][0]["name"] == "weighted_rrf" and data["hits"][0]["lanes"] == ["codegraph"]


def test_code_recall_failure_is_error_not_raise(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("store gone")
    monkeypatch.setattr("codev_platform.recall.recall_code", _boom)
    r = CodeRecallTool("p").run({"query": "x"})
    assert r.is_error and "召回失败" in r.content        # 异常转结果回灌, 不崩 loop
