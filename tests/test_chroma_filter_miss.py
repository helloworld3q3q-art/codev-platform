"""search_docs 显式过滤零命中诊断。"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from codev_platform.chroma import _tools


class _Collection:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.queries: list[dict] = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.result


def _run_search(monkeypatch, result: dict, args: dict):
    collection = _Collection(result)
    state = SimpleNamespace(
        collection=collection,
        bm25_index=None,
        init_error=None,
        last_request_at=None,
        active_collection_name="demo__platform_docs",
    )
    recall_events: list[dict] = []

    async def immediate(operation):
        return operation()

    monkeypatch.setattr(_tools, "_ensure_project", lambda _pid: state)
    monkeypatch.setattr(_tools, "_gpu_call", immediate)
    monkeypatch.setattr(_tools, "_get_gpu_sem", lambda: asyncio.Semaphore(1))
    monkeypatch.setattr(_tools, "_encode_query", lambda _query: [0.1])
    monkeypatch.setattr(_tools, "RERANKER_ENABLED", False)
    monkeypatch.setattr(_tools, "_flog", lambda _message: None)
    monkeypatch.setattr(_tools, "_log_recall", recall_events.append)

    token = _tools._current_project_id.set("demo")
    try:
        content = asyncio.run(_tools.call_tool("search_docs", args))
    finally:
        _tools._current_project_id.reset(token)
    return content, collection, recall_events


def _empty_result() -> dict:
    return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def test_filtered_empty_keeps_array_and_appends_filter_miss(monkeypatch):
    content, collection, recall_events = _run_search(
        monkeypatch,
        _empty_result(),
        {
            "query": "评分 DQ 影子观察",
            "category": "design",
            "module": "stock-pipeline",
        },
    )

    assert json.loads(content[0].text) == []
    diagnostic = json.loads(content[1].text)
    assert diagnostic["code"] == "filter_miss"
    assert diagnostic["requested_filters"] == {
        "category": "design",
        "module": "stock-pipeline",
    }
    assert diagnostic["suggested_retries"] == [
        {"category": "design", "module": "all"},
        {"category": "all", "module": "stock-pipeline"},
        {"category": "all", "module": "all"},
    ]
    assert len(collection.queries) == 1, "诊断不得静默执行放宽过滤后的查询"
    assert collection.queries[0]["where"] == {
        "$and": [{"category": "design"}, {"module": "stock-pipeline"}]
    }
    assert recall_events[0]["diagnostic"] == "filter_miss"


def test_unfiltered_empty_preserves_single_empty_array(monkeypatch):
    content, collection, recall_events = _run_search(
        monkeypatch,
        _empty_result(),
        {"query": "不存在的主题"},
    )

    assert len(content) == 1
    assert json.loads(content[0].text) == []
    assert "where" not in collection.queries[0]
    assert "diagnostic" not in recall_events[0]


def test_filtered_hit_preserves_existing_single_array_contract(monkeypatch):
    hit = {
        "ids": [["doc-1"]],
        "documents": [["文档内容"]],
        "metadatas": [[
            {
                "file": "docs/architecture/a.md",
                "category": "design",
                "module": "platform",
                "chunk_index": 0,
            }
        ]],
        "distances": [[0.1]],
    }
    content, _collection, recall_events = _run_search(
        monkeypatch,
        hit,
        {"query": "架构", "category": "design", "module": "platform"},
    )

    assert len(content) == 1
    payload = json.loads(content[0].text)
    assert payload[0]["file"] == "docs/architecture/a.md"
    assert "diagnostic" not in recall_events[0]
