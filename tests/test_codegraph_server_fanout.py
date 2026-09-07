from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager, contextmanager, suppress
from types import SimpleNamespace

import pytest
from mcp.types import TextContent

from codev_platform.codegraph import server as cg_server
from codev_platform.core.repos import RepoSpec


class _FakeBackend:
    def __init__(
        self, text: str | None = None, *, boom: bool = False, is_error: bool = False
    ) -> None:
        self.text = text or ""
        self.boom = boom
        self.is_error = is_error
        self.alive = True
        self.calls: list[dict | None] = []

    async def request(self, kind: str, name: str | None = None, args: dict | None = None):
        if self.boom:
            raise RuntimeError(self.text or "boom")
        if kind == "list":
            return SimpleNamespace(tools=[])
        self.calls.append(args)
        return SimpleNamespace(
            content=[TextContent(type="text", text=self.text)],
            isError=self.is_error,
        )


def test_后端环境强制关闭watcher与共享daemon(monkeypatch):
    """常态 MCP 后端不得继承外部环境中的 watcher 或 daemon 开关。"""
    monkeypatch.setenv("CODEGRAPH_NO_WATCH", "0")
    monkeypatch.setenv("CODEGRAPH_NO_DAEMON", "0")

    environment = cg_server._codegraph_backend_environment()

    assert environment["CODEGRAPH_NO_WATCH"] == "1"
    assert environment["CODEGRAPH_NO_DAEMON"] == "1"


def test_后端启动参数显式关闭watcher():
    """进程命令行必须显式关闭常态 watcher。"""
    assert cg_server._codegraph_backend_arguments() == ["serve", "--mcp", "--no-watch"]


def test_已构造后端在启动时读取server最新命令(monkeypatch, tmp_path):
    """兼容门面替换命令后，已缓存的后端也必须沿用旧动态读取语义。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    observed: list[str] = []
    backend = cg_server._Backend("demo", repo)

    @asynccontextmanager
    async def stdio(parameters):
        observed.append(parameters.command)
        yield object(), object()

    class Session:
        def __init__(self, _read, _write) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def initialize(self) -> None:
            return None

    async def writer_absent() -> None:
        return None

    async def serve(_session) -> None:
        return None

    monkeypatch.setattr(cg_server, "_CODEGRAPH_CMD", "replacement-codegraph")
    monkeypatch.setattr(cg_server, "stdio_client", stdio)
    monkeypatch.setattr(cg_server, "ClientSession", Session)
    monkeypatch.setattr(backend, "_require_backend_spawn_permitted", lambda: None)
    monkeypatch.setattr(backend, "_require_writer_absent", writer_absent)
    monkeypatch.setattr(backend, "_serve_requests", serve)

    asyncio.run(backend._run_stdio_session())

    assert observed == ["replacement-codegraph"]


def test_已构造后端动态转发server日志门面(monkeypatch, tmp_path):
    """后端不得捕获构造期 logger，便于运行期统一替换日志出口。"""
    monkeypatch.setattr(cg_server, "_flog", lambda _message: None)
    backend = cg_server._Backend("demo", tmp_path)
    observed: list[str] = []
    monkeypatch.setattr(cg_server, "_flog", observed.append)

    async def run() -> None:
        async def fail() -> None:
            raise RuntimeError("detached failed")

        task = asyncio.create_task(fail())
        await asyncio.wait({task})
        backend._consume_detached_child(task)

    asyncio.run(run())

    assert len(observed) == 1
    assert "detached failed" in observed[0]


def test_后端整个stdio会话持续持有共享操作租约(monkeypatch, tmp_path):
    """MCP 的追赶同步窗口必须与 reindex `codegraph sync` 串行化。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    events: list[str] = []
    held = {"value": False}

    @contextmanager
    def lease(path):
        assert path == repo
        held["value"] = True
        events.append("enter")
        try:
            yield
        finally:
            held["value"] = False
            events.append("exit")

    @asynccontextmanager
    async def stdio(_parameters):
        assert held["value"] is True
        yield object(), object()

    class Session:
        def __init__(self, _read, _write) -> None:
            pass

        async def __aenter__(self):
            assert held["value"] is True
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def initialize(self) -> None:
            assert held["value"] is True

    monkeypatch.setattr(cg_server, "codegraph_operation_lease", lease)
    monkeypatch.setattr(cg_server, "stdio_client", stdio)
    monkeypatch.setattr(cg_server, "ClientSession", Session)
    monkeypatch.setattr(cg_server, "_flog", lambda _message: None)
    backend = cg_server._Backend("demo", repo)

    async def run() -> None:
        task = asyncio.create_task(backend._run())
        await asyncio.wait_for(backend._startup.ready.wait(), timeout=1.0)
        assert held["value"] is True
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert events == ["enter", "exit"]


def test_后端在创建stdio前二次复核维护marker(monkeypatch, tmp_path):
    """初次准入后 marker 新启用时，不得再创建会被维护方收敛的子进程。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    repo = tmp_path / "repo"
    repo.mkdir()
    stdio_created = False

    @asynccontextmanager
    async def stdio(_parameters):
        nonlocal stdio_created
        stdio_created = True
        yield object(), object()

    monkeypatch.setattr(codegraph_gate, "codegraph_backend_start_permitted", lambda: False)
    monkeypatch.setattr(cg_server, "stdio_client", stdio)
    monkeypatch.setattr(cg_server, "_flog", lambda _message: None)
    backend = cg_server._Backend("demo", repo)

    asyncio.run(backend._run())

    assert stdio_created is False
    assert isinstance(backend._startup.error, codegraph_gate.CodegraphMaintenanceGateError)


def test_后端冷启动强校验在工作线程执行(monkeypatch, tmp_path):
    """systemd/cgroup 强校验可能阻塞，不能占住 MCP 事件循环。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    repo = tmp_path / "repo"
    repo.mkdir()
    caller_thread = threading.get_ident()
    check_threads: list[int] = []
    release = asyncio.Event()

    def 强校验() -> None:
        check_threads.append(threading.get_ident())

    async def 就绪后等待(self) -> None:
        self._startup.mark_ready()
        await release.wait()

    monkeypatch.setattr(codegraph_gate, "require_codegraph_service_start_permitted", 强校验)
    monkeypatch.setattr(codegraph_gate, "codegraph_backend_start_permitted", lambda: True)
    monkeypatch.setattr(cg_server._Backend, "_run", 就绪后等待)
    backend = cg_server._Backend("demo", repo)

    async def 运行() -> None:
        await backend.ensure()
        release.set()
        assert backend._startup.task is not None
        await asyncio.wait_for(backend._startup.task, timeout=0.1)

    asyncio.run(运行())

    assert check_threads and check_threads[0] != caller_thread


def test_HTTP启动先复核维护门禁(monkeypatch):
    """HTTP 监听之前必须拒绝维护期或锁状态不可证明的启动。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    def _拒绝启动() -> None:
        raise codegraph_gate.CodegraphMaintenanceGateError("维护门禁拒绝 CodeGraph 启动")

    monkeypatch.setattr(codegraph_gate, "require_codegraph_service_start_permitted", _拒绝启动)

    with pytest.raises(codegraph_gate.CodegraphMaintenanceGateError):
        asyncio.run(cg_server.run_http(port=0))


def test_HTTP启动在runtime_mask保持时拒绝(monkeypatch):
    """restore 后未显式恢复 CodeGraph 时，直接 HTTP 启动也不能绕过 hold。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_runtime_mask_active", lambda: True)

    with pytest.raises(codegraph_gate.CodegraphMaintenanceGateError):
        asyncio.run(cg_server.run_http(port=0))


def test_后端ensure维护门禁拒绝时不创建任务(monkeypatch, tmp_path):
    """每次懒后端启动前都要复核，拒绝时不能触发 CodeGraph spawn。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "codegraph_backend_start_permitted", lambda: False)
    monkeypatch.setattr(
        codegraph_gate,
        "require_codegraph_service_start_permitted",
        lambda: pytest.fail("marker 已启用时不得再执行受管服务强校验"),
    )
    backend = cg_server._Backend("demo", tmp_path)

    with pytest.raises(codegraph_gate.CodegraphMaintenanceGateError):
        asyncio.run(backend.ensure())

    assert backend._startup.task is None


def test_后端ensure受管服务身份拒绝时不创建任务(monkeypatch, tmp_path):
    """手工 Python 代理即使维护许可通过，也不得启动 CodeGraph 子进程。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    def _拒绝服务身份() -> None:
        raise codegraph_gate.CodegraphMaintenanceGateError("当前进程不属于固定 CodeGraph unit")

    monkeypatch.setattr(
        codegraph_gate,
        "require_codegraph_service_start_permitted",
        _拒绝服务身份,
    )
    monkeypatch.setattr(
        codegraph_gate,
        "codegraph_backend_start_permitted",
        lambda: True,
    )
    backend = cg_server._Backend("demo", tmp_path)

    with pytest.raises(codegraph_gate.CodegraphMaintenanceGateError):
        asyncio.run(backend.ensure())

    assert backend._startup.task is None


def test_已启动代理在runtime_mask后ensure仍拒绝请求(monkeypatch, tmp_path):
    """已有 detached 代理不得因后端已存在而继续触发 catch-up。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_runtime_mask_active", lambda: True)
    backend = cg_server._Backend("demo", tmp_path)

    with pytest.raises(codegraph_gate.CodegraphMaintenanceGateError):
        asyncio.run(backend.ensure())

    assert backend._startup.task is None


def test_list_tools缓存命中仍复核维护门禁(monkeypatch):
    """工具缓存不能成为绕过维护门禁的旁路。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    def _拒绝请求() -> None:
        raise codegraph_gate.CodegraphMaintenanceGateError("维护门禁拒绝 CodeGraph 请求")

    monkeypatch.setattr(codegraph_gate, "require_codegraph_request_permitted", _拒绝请求)
    monkeypatch.setattr(cg_server, "_TOOLS_CACHE", [object()])

    assert asyncio.run(cg_server.list_tools()) == []


def test_list_tools后端异常不写入路径或凭据到稳定日志(monkeypatch):
    secret = "/private/codegraph.db token=不应落盘"
    observed: list[str] = []
    monkeypatch.setattr(
        cg_server._maintenance_gate,
        "require_codegraph_request_permitted",
        lambda: None,
    )
    monkeypatch.setattr(cg_server, "_TOOLS_CACHE", None)
    monkeypatch.setattr(cg_server, "_active_pid", lambda: "demo")
    monkeypatch.setattr(
        cg_server,
        "_backend_slots_for",
        lambda _pid: [("main", _FakeBackend(secret, boom=True))],
    )
    monkeypatch.setattr(cg_server, "_flog", observed.append)

    assert asyncio.run(cg_server.list_tools()) == []
    assert len(observed) == 1
    assert secret not in observed[0]


def test_已启动代理在runtime_mask后拒绝list与call(monkeypatch):
    """缓存命中和工具调用都必须再次确认 hold，不能访问旧后端。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(
        codegraph_gate,
        "require_codegraph_request_permitted",
        lambda: (_ for _ in ()).throw(codegraph_gate.CodegraphMaintenanceGateError("维护门禁")),
    )
    monkeypatch.setattr(cg_server, "_TOOLS_CACHE", [object()])
    monkeypatch.setattr(
        cg_server,
        "_backend_slots_for",
        lambda _pid: pytest.fail("runtime hold 生效后不得访问后端"),
    )
    monkeypatch.setattr(cg_server, "_log_usage", lambda _record: None)

    listed = asyncio.run(cg_server.list_tools())
    result = asyncio.run(cg_server.call_tool("codegraph_search", {"query": "Target"}))

    assert listed == []
    assert len(result) == 1
    assert "维护门禁" in result[0].text


def test_call_tool维护门禁拒绝时不访问后端(monkeypatch):
    """调用工具必须先过门禁，不能直接触发后端 ensure 或懒启动。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    def _拒绝请求() -> None:
        raise codegraph_gate.CodegraphMaintenanceGateError("维护门禁拒绝 CodeGraph 请求")

    monkeypatch.setattr(codegraph_gate, "require_codegraph_request_permitted", _拒绝请求)
    monkeypatch.setattr(
        cg_server,
        "_backend_slots_for",
        lambda _pid: pytest.fail("维护门禁拒绝后不得访问后端"),
    )
    monkeypatch.setattr(cg_server, "_log_usage", lambda _record: None)

    result = asyncio.run(cg_server.call_tool("codegraph_search", {"query": "Target"}))

    assert len(result) == 1
    assert "维护门禁" in result[0].text


def _run_call(monkeypatch, slots, args=None):
    monkeypatch.setattr(cg_server, "_backend_slots_for", lambda pid: slots)
    monkeypatch.setattr(cg_server, "_log_usage", lambda record: None)
    token = cg_server._current_project_id.set("demo")
    try:
        return asyncio.run(cg_server.call_tool("codegraph_search", args or {"query": "Target"}))
    finally:
        cg_server._current_project_id.reset(token)


def test_call_tool_single_backend_preserves_raw_content(monkeypatch):
    content = _run_call(monkeypatch, [("main", _FakeBackend("main-result"))])

    assert [c.text for c in content] == ["main-result"]


def test_call_tool_multi_backend_adds_repo_sections(monkeypatch):
    content = _run_call(
        monkeypatch,
        [
            ("main", _FakeBackend("main-result")),
            ("extra", _FakeBackend("extra-result")),
        ],
    )

    texts = [c.text for c in content]
    assert texts == [
        '{"repo": "main", "separator": true}',
        "main-result",
        '{"repo": "extra", "separator": true}',
        "extra-result",
    ]


def test_call_tool_multi_backend_fail_soft_when_one_repo_fails(monkeypatch):
    content = _run_call(
        monkeypatch,
        [
            ("main", _FakeBackend("main-result")),
            ("extra", _FakeBackend("extra failed", boom=True)),
        ],
    )

    texts = [c.text for c in content]
    assert "main-result" in texts
    assert "extra failed" not in texts


def test_call_tool_structured_merge_is_opt_in_and_strips_platform_arg(monkeypatch):
    main = _FakeBackend("main-result")
    extra = _FakeBackend("extra-result")
    content = _run_call(
        monkeypatch,
        [
            ("main", main),
            ("extra", extra),
        ],
        {"query": "Target", "_codev_merge": "json"},
    )

    assert len(content) == 1
    payload = json.loads(content[0].text)
    assert payload["merge"] == "codev-fanout-v1"
    assert payload["project_id"] == "demo"
    assert [r["repo"] for r in payload["repos"]] == ["main", "extra"]
    assert payload["repos"][0]["content"][0]["text"] == "main-result"
    assert payload["repos"][1]["content"][0]["text"] == "extra-result"
    assert payload["failures"] == []
    assert main.calls == [{"query": "Target"}]
    assert extra.calls == [{"query": "Target"}]


def test_call_tool_structured_merge_reports_failed_repo(monkeypatch):
    content = _run_call(
        monkeypatch,
        [
            ("main", _FakeBackend("main-result")),
            ("extra", _FakeBackend("extra failed", boom=True)),
        ],
        {"query": "Target", "_codev_merge": "structured"},
    )

    payload = json.loads(content[0].text)
    assert [r["repo"] for r in payload["repos"]] == ["main"]
    assert payload["failures"] == [{"repo": "extra", "error": "backend_call_failed"}]


def test_call_tool单后端异常不向MCP返回底层详情(monkeypatch):
    secret = "/private/path token=不应回显"

    content = _run_call(monkeypatch, [("main", _FakeBackend(secret, boom=True))])

    assert secret not in content[0].text
    payload = json.loads(content[0].text)
    assert payload["code"] == "internal"


def test_call_tool后端错误内容在普通与结构化聚合中均脱敏(monkeypatch):
    secret = "visible-sample token=masked-value"

    plain = _run_call(
        monkeypatch,
        [("main", _FakeBackend(secret, is_error=True))],
    )
    structured = _run_call(
        monkeypatch,
        [("main", _FakeBackend(secret, is_error=True))],
        {"_codev_merge": "structured"},
    )

    assert secret not in "".join(item.text for item in plain)
    assert secret not in structured[0].text


def test_backend_slots_for_uses_repo_specs_with_existing_codegraph_db(tmp_path, monkeypatch):
    main = tmp_path / "main"
    main.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
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
    main = tmp_path / "main"
    main.mkdir()
    extra = tmp_path / "extra"
    extra.mkdir()
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
