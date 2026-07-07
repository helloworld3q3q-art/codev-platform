from __future__ import annotations

import asyncio
from types import SimpleNamespace

from mcp.types import TextContent

from codev_platform.codegraph import server as cg_server
from codev_platform.core.repos import RepoSpec


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


def test_backend_slots_for_uses_repo_specs_with_existing_codegraph_db(tmp_path, monkeypatch):
    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    for repo in (main, extra):
        db = repo / ".codegraph" / "codegraph.db"
        db.parent.mkdir(parents=True)
        db.write_bytes(b"")
    specs = [
        RepoSpec(root=main, tag="", is_main=True, source_project_id="demo"),
        RepoSpec(root=extra, tag="extra", is_main=False, source_project_id="extra-demo"),
    ]
    monkeypatch.setattr(cg_server, "project_repo_specs", lambda pid: specs)
    cg_server._backends.clear()

    slots = cg_server._backend_slots_for("demo")

    assert [(label, be.pid, be.repo) for label, be in slots] == [
        ("main", "demo", main),
        ("extra", "demo:extra", extra),
    ]


def test_backend_slots_for_skips_repo_without_codegraph_db(tmp_path, monkeypatch):
    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    db = main / ".codegraph" / "codegraph.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    specs = [
        RepoSpec(root=main, tag="", is_main=True, source_project_id="demo"),
        RepoSpec(root=extra, tag="extra", is_main=False, source_project_id="extra-demo"),
    ]
    monkeypatch.setattr(cg_server, "project_repo_specs", lambda pid: specs)
    cg_server._backends.clear()

    slots = cg_server._backend_slots_for("demo")

    assert [(label, be.pid, be.repo) for label, be in slots] == [("main", "demo", main)]
