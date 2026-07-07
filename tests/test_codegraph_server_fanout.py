from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mcp.types import TextContent

from codev_platform.codegraph import server as cg_server


class _FakeBackend:
    def __init__(self, text: str | None = None, *, boom: bool = False) -> None:
        self.text = text or ""
        self.boom = boom
        self.alive = True

    async def request(self, kind: str, name: str | None = None, args: dict | None = None):
        if self.boom:
            raise RuntimeError(self.text or "boom")
        if kind == "list":
            return SimpleNamespace(tools=[])
        return SimpleNamespace(
            content=[TextContent(type="text", text=self.text)],
            isError=False,
        )


def _run_call(monkeypatch, slots):
    monkeypatch.setattr(cg_server, "_backend_slots_for", lambda pid: slots)
    monkeypatch.setattr(cg_server, "_log_usage", lambda record: None)
    token = cg_server._current_project_id.set("demo")
    try:
        return asyncio.run(cg_server.call_tool("codegraph_search", {"query": "Target"}))
    finally:
        cg_server._current_project_id.reset(token)


def test_call_tool_single_backend_preserves_raw_content(monkeypatch):
    content = _run_call(monkeypatch, [("main", _FakeBackend("main-result"))])

    assert [c.text for c in content] == ["main-result"]


def test_call_tool_multi_backend_adds_repo_sections(monkeypatch):
    content = _run_call(monkeypatch, [
        ("main", _FakeBackend("main-result")),
        ("extra", _FakeBackend("extra-result")),
    ])

    texts = [c.text for c in content]
    assert texts == [
        '{"repo": "main", "separator": true}',
        "main-result",
        '{"repo": "extra", "separator": true}',
        "extra-result",
    ]


def test_call_tool_multi_backend_fail_soft_when_one_repo_fails(monkeypatch):
    content = _run_call(monkeypatch, [
        ("main", _FakeBackend("main-result")),
        ("extra", _FakeBackend("extra failed", boom=True)),
    ])

    texts = [c.text for c in content]
    assert "main-result" in texts
    assert "extra failed" not in texts
