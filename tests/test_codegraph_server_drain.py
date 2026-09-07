from __future__ import annotations

import asyncio
import inspect
import json
import threading
from contextlib import asynccontextmanager, contextmanager, suppress
from types import SimpleNamespace

import pytest

from codev_platform.codegraph import server as cg_server
from codev_platform.codegraph.backend_inbox import CodegraphBackendDrainingError

@contextmanager
def _空操作租约(_path):
    yield


@asynccontextmanager
async def _空stdio(_parameters):
    yield object(), object()


class _协作会话:
    def __init__(self, _read=None, _write=None) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def initialize(self) -> None:
        return None

    async def call_tool(self, _name, _args):
        return SimpleNamespace(content=[], isError=False)


async def _取消并等待(task) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


class _BackendHarness:
    def __init__(
        self,
        monkeypatch,
        tmp_path,
        *,
        writer_pending=None,
        session_type=_协作会话,
        lease=_空操作租约,
        drain_grace=None,
        cancel_timeout=None,
    ) -> None:
        from codev_platform.codegraph import maintenance_gate as codegraph_gate

        self.repo = tmp_path / "repo"
        self.repo.mkdir(exist_ok=True)
        self.pending = False
        check = writer_pending or (lambda: self.pending)
        monkeypatch.setattr(codegraph_gate, "codegraph_backend_start_permitted", lambda: True)
        monkeypatch.setattr(codegraph_gate, "require_codegraph_service_start_permitted", lambda: None)
        monkeypatch.setattr(cg_server, "codegraph_operation_lease", lease)
        monkeypatch.setattr(cg_server, "codegraph_writer_pending", lambda _repo: check())
        monkeypatch.setattr(cg_server, "stdio_client", _空stdio)
        monkeypatch.setattr(cg_server, "ClientSession", session_type)
        monkeypatch.setattr(cg_server, "_WRITER_INTENT_POLL_SEC", 0.001)
        monkeypatch.setattr(cg_server, "_flog", lambda _message: None)
        if drain_grace is not None:
            monkeypatch.setattr(cg_server, "_BACKEND_DRAIN_GRACE_SEC", drain_grace)
        if cancel_timeout is not None:
            monkeypatch.setattr(
                cg_server,
                "_BACKEND_CHILD_CANCEL_TIMEOUT_SEC",
                cancel_timeout,
            )
        self.backend = cg_server._Backend("demo", self.repo)


class _CallControl:
    def __init__(self, cancel_mode: str = "propagate") -> None:
        self.cancel_mode = cancel_mode
        self.started = asyncio.Event()
        self.started_sync = threading.Event()
        self.cancel_seen = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cancel_attempts = 0

    async def call(self):
        self.started.set()
        self.started_sync.set()
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_attempts += 1
                    self.cancel_seen.set()
                    if self.cancel_mode == "error":
                        raise RuntimeError("取消清理失败") from None
                    if self.cancel_mode == "propagate":
                        raise
            return SimpleNamespace(content=[], isError=False)
        finally:
            self.finished.set()


def _受控会话(control: _CallControl, *, on_initialize=None):
    class Session(_协作会话):
        async def initialize(self) -> None:
            if on_initialize is not None:
                on_initialize()

        async def call_tool(self, _name, _args):
            return await control.call()

    return Session

def test_后端启动准入看到写意图时不创建工作任务(monkeypatch, tmp_path):
    """写方已经声明意图时，懒启动必须在创建 worker 前快速拒绝。"""
    spawned = False

    async def 工作线程(self) -> None:
        nonlocal spawned
        spawned = True
        self._startup.mark_ready()

    harness = _BackendHarness(monkeypatch, tmp_path)
    harness.pending = True
    monkeypatch.setattr(cg_server._Backend, "_run", 工作线程)

    async def 运行() -> None:
        with pytest.raises(CodegraphBackendDrainingError):
            await harness.backend.ensure()

    asyncio.run(运行())

    assert spawned is False
    assert harness.backend._startup.task is None


def test_后端取得操作租约后看到写意图时不创建stdio(monkeypatch, tmp_path):
    """租约后二次探测封闭“启动检查后、取得租约前”的竞态窗口。"""
    lease_acquired = False
    stdio_created = False

    @contextmanager
    def lease(_path):
        nonlocal lease_acquired
        lease_acquired = True
        yield

    @asynccontextmanager
    async def stdio(_parameters):
        nonlocal stdio_created
        stdio_created = True
        raise RuntimeError("写意图存在时不得创建 stdio")
        yield object(), object()

    harness = _BackendHarness(monkeypatch, tmp_path, lease=lease)
    harness.pending = True
    monkeypatch.setattr(cg_server, "stdio_client", stdio)
    asyncio.run(harness.backend._run())

    assert lease_acquired is True
    assert stdio_created is False
    assert isinstance(harness.backend._startup.error, CodegraphBackendDrainingError)


def test_同步写意图探针不阻塞事件循环与另一仓请求(monkeypatch, tmp_path):
    """单仓文件锁探针阻塞时，HTTP 事件循环和其他仓必须继续推进。"""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    entered = threading.Event()
    release = threading.Event()
    heartbeat = threading.Event()
    other_repo_done = threading.Event()
    observed: dict[str, bool] = {}

    def probe(repo):
        if repo == repo_a:
            entered.set()
            assert release.wait(timeout=1.0)
        else:
            other_repo_done.set()
        return False

    def coordinate() -> None:
        assert entered.wait(timeout=1.0)
        observed["heartbeat"] = heartbeat.wait(timeout=0.2)
        observed["other_repo"] = other_repo_done.wait(timeout=0.2)
        release.set()

    async def await_probe(backend) -> bool:
        result = backend._writer_pending()
        return await result if inspect.isawaitable(result) else result

    async def run() -> None:
        first = asyncio.create_task(await_probe(cg_server._Backend("a", repo_a)))
        second = asyncio.create_task(await_probe(cg_server._Backend("b", repo_b)))
        pulse = asyncio.create_task(asyncio.to_thread(heartbeat.set))
        await asyncio.gather(first, second, pulse)

    monkeypatch.setattr(cg_server, "codegraph_writer_pending", probe)
    coordinator = threading.Thread(target=coordinate)
    coordinator.start()
    asyncio.run(run())
    coordinator.join(timeout=1.0)

    assert observed == {"heartbeat": True, "other_repo": True}


def test_写意图探针异常时失败关闭并终结全部请求(monkeypatch, tmp_path):
    """探针故障必须释放租约，并让当前、排队请求都以异常结束。"""
    initialized = threading.Event()
    lease_released = threading.Event()
    call = _CallControl()
    after_init_count = 0
    active_before_failure = False

    class ProbeFailure(RuntimeError):
        pass

    def probe() -> bool:
        nonlocal after_init_count, active_before_failure
        if not initialized.is_set():
            return False
        after_init_count += 1
        if after_init_count == 1:  # session initialize 后的第二次启动竞态检查。
            return False
        active_before_failure = call.started_sync.wait(timeout=0.2)
        raise ProbeFailure("写意图探针不可用")

    @contextmanager
    def lease(_path):
        try:
            yield
        finally:
            lease_released.set()

    harness = _BackendHarness(
        monkeypatch,
        tmp_path,
        writer_pending=probe,
        session_type=_受控会话(call, on_initialize=initialized.set),
        lease=lease,
    )

    async def run() -> None:
        current = asyncio.create_task(harness.backend.request("call", "current", {}))
        queued = asyncio.create_task(harness.backend.request("call", "queued", {}))
        done, pending = await asyncio.wait({current, queued}, timeout=0.6)
        for task in pending:
            await _取消并等待(task)
        worker = harness.backend._startup.task
        if worker is not None and not worker.done():
            await asyncio.wait({worker}, timeout=0.2)
        assert not pending
        assert all(not task.cancelled() and task.exception() is not None for task in done)
        assert worker is not None and worker.done()

    asyncio.run(run())

    assert active_before_failure is True
    assert call.cancel_attempts == 1
    assert lease_released.is_set() is True


def test_空闲后端发现写意图后退出并释放操作租约(monkeypatch, tmp_path):
    """没有当前调用时，写意图 watcher 应立即结束该仓 stdio 会话。"""
    lease_released = False

    @contextmanager
    def lease(_path):
        nonlocal lease_released
        try:
            yield
        finally:
            lease_released = True

    harness = _BackendHarness(monkeypatch, tmp_path, lease=lease)

    async def 运行() -> None:
        task = asyncio.create_task(harness.backend._run())
        await asyncio.wait_for(harness.backend._startup.ready.wait(), timeout=0.2)
        harness.pending = True
        drained = True
        try:
            await asyncio.wait_for(task, timeout=0.2)
        except asyncio.TimeoutError:
            drained = False
        assert drained is True

    asyncio.run(运行())

    assert lease_released is True


def test_排空时当前调用宽限内完成且排队请求被拒绝(monkeypatch, tmp_path):
    """排空先拒绝队列，但不破坏能在固定宽限内完成的当前调用。"""
    call = _CallControl()
    harness = _BackendHarness(
        monkeypatch,
        tmp_path,
        session_type=_受控会话(call),
        drain_grace=0.1,
    )

    async def 运行() -> None:
        current = asyncio.create_task(harness.backend.request("call", "current", {}))
        await asyncio.wait_for(call.started.wait(), timeout=0.2)
        queued = asyncio.create_task(harness.backend.request("call", "queued", {}))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        harness.pending = True
        queued_done, _ = await asyncio.wait({queued}, timeout=0.2)
        rejected_before_release = queued in queued_done
        call.release.set()
        result = await asyncio.wait_for(current, timeout=0.2)
        worker = harness.backend._startup.task
        assert worker is not None
        worker_done, _ = await asyncio.wait({worker}, timeout=0.2)
        await _取消并等待(queued)
        if worker not in worker_done:
            await _取消并等待(worker)
        assert result.isError is False
        assert rejected_before_release is True
        with pytest.raises(CodegraphBackendDrainingError):
            queued.result()
        assert worker in worker_done

    asyncio.run(运行())


def test_写意图与取队列同时完成时不启动尚未执行的请求(monkeypatch, tmp_path):
    """已出队但尚未调用 stdio 的请求仍属于排队请求，不享受当前调用宽限。"""
    backend = cg_server._Backend("demo", tmp_path)
    called = False

    class Session:
        async def call_tool(self, _name, _args):
            nonlocal called
            called = True
            return SimpleNamespace(content=[], isError=False)

    async def 写意图已出现(_check, *, poll_sec):
        assert poll_sec > 0

    async def 运行固定结果() -> None:
        result = asyncio.get_running_loop().create_future()

        async def 返回固定结果(writer):
            await writer
            return (("call", "queued", {}), result), True

        monkeypatch.setattr(cg_server, "wait_for_writer_intent", 写意图已出现)
        monkeypatch.setattr(backend, "_next_request_or_drain", 返回固定结果)
        await asyncio.wait_for(backend._serve_requests(Session()), timeout=0.2)
        with pytest.raises(CodegraphBackendDrainingError):
            result.result()

    asyncio.run(运行固定结果())

    assert called is False


def test_owner取消不被子调用清理异常吞掉(tmp_path):
    """服务关闭取消 owner 时，子调用清理异常不得替换父任务的 CancelledError。"""
    backend = cg_server._Backend("demo", tmp_path)
    child_started = asyncio.Event()

    async def 子调用() -> None:
        child_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError("子调用取消清理失败") from None

    async def owner() -> None:
        child = asyncio.create_task(子调用())
        try:
            await asyncio.Event().wait()
        finally:
            await backend._cancel_child(child)

    async def 运行() -> None:
        task = asyncio.create_task(owner())
        await asyncio.wait_for(child_started.wait(), timeout=0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(运行())


def test_发布取消结果尊重子类排空异常覆盖(tmp_path):
    """兼容适配器覆盖异常工厂时，静态发布路径也必须保持多态。"""

    class CustomDrainingError(CodegraphBackendDrainingError):
        pass

    class Backend(cg_server._Backend):
        @staticmethod
        def _draining_error() -> CodegraphBackendDrainingError:
            return CustomDrainingError("custom draining")

    backend = Backend("demo", tmp_path)

    async def 运行() -> None:
        call = asyncio.create_task(asyncio.sleep(10))
        call.cancel()
        with suppress(asyncio.CancelledError):
            await call
        result = asyncio.get_running_loop().create_future()

        with pytest.raises(asyncio.CancelledError):
            backend._publish_call_result(call, result)

        assert isinstance(result.exception(), CustomDrainingError)

    asyncio.run(运行())


def test_owner在排空取消收敛中退出时不重复取消当前调用(monkeypatch, tmp_path):
    """排空路径接管 child 后，parent 取消不得让外层 finally 再收敛一次。"""
    backend = cg_server._Backend("demo", tmp_path)
    intent = asyncio.Event()
    call = _CallControl("ignore")

    async def 等待写意图() -> None:
        await intent.wait()

    async def 运行() -> None:
        result = asyncio.get_running_loop().create_future()
        writer = asyncio.create_task(等待写意图())
        execution = asyncio.create_task(
            backend._execute_request(
                _受控会话(call)(),
                ("call", "blocked", {}),
                result,
                writer,
            )
        )
        await asyncio.wait_for(call.started.wait(), timeout=0.2)
        intent.set()
        await asyncio.wait_for(call.cancel_seen.wait(), timeout=0.2)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution
        with pytest.raises(CodegraphBackendDrainingError):
            result.result()
        call.release.set()
        await asyncio.wait_for(call.finished.wait(), timeout=0.2)
        assert call.cancel_attempts == 2

    monkeypatch.setattr(cg_server, "_BACKEND_DRAIN_GRACE_SEC", 0.01)
    monkeypatch.setattr(cg_server, "_BACKEND_CHILD_CANCEL_TIMEOUT_SEC", 0.01)
    asyncio.run(运行())


@pytest.mark.parametrize("cancel_mode", ["清理异常", "持续吞取消"])
def test_排空宽限耗尽后有界取消当前调用并退出worker(
    monkeypatch,
    tmp_path,
    cancel_mode,
):
    """卡住的当前调用不能无限占租约；超时只取消该仓会话。"""
    call = _CallControl("error" if cancel_mode == "清理异常" else "ignore")
    harness = _BackendHarness(
        monkeypatch,
        tmp_path,
        session_type=_受控会话(call),
        drain_grace=0.01,
        cancel_timeout=0.01,
    )

    async def 运行() -> None:
        request = asyncio.create_task(harness.backend.request("call", "blocked", {}))
        await asyncio.wait_for(call.started.wait(), timeout=0.2)
        harness.pending = True
        request_done, _ = await asyncio.wait({request}, timeout=0.2)
        worker = harness.backend._startup.task
        assert worker is not None
        worker_done, _ = await asyncio.wait({worker}, timeout=0.2)
        if request not in request_done:
            await _取消并等待(request)
        call.release.set()
        if worker not in worker_done:
            await _取消并等待(worker)
        await asyncio.wait_for(call.finished.wait(), timeout=0.2)
        assert request in request_done
        with pytest.raises(CodegraphBackendDrainingError):
            request.result()
        assert worker in worker_done

    asyncio.run(运行())

    expected_attempts = 1 if cancel_mode == "清理异常" else 2
    assert call.cancel_attempts == expected_attempts


def test_写意图清除后下一请求懒启动新一代后端(monkeypatch, tmp_path):
    """排空只终止仓级 stdio 代，不停止 HTTP 代理或永久熔断该仓。"""
    generations = 0

    class Session(_协作会话):
        async def __aenter__(self):
            nonlocal generations
            generations += 1
            return self

    harness = _BackendHarness(
        monkeypatch,
        tmp_path,
        session_type=Session,
    )

    async def 运行() -> None:
        first = await harness.backend.request("call", "first", {})
        assert first.isError is False
        first_worker = harness.backend._startup.task
        assert first_worker is not None
        harness.pending = True
        drained, _ = await asyncio.wait({first_worker}, timeout=0.2)
        was_drained = first_worker in drained
        harness.pending = False
        second = await asyncio.wait_for(
            harness.backend.request("call", "second", {}), timeout=0.2
        )
        second_worker = harness.backend._startup.task
        assert second_worker is not None
        await _取消并等待(second_worker)
        assert second.isError is False
        assert was_drained is True

    asyncio.run(运行())

    assert generations == 2

def test_call_backend把排空异常映射为上游暂不可用():
    """客户端只看到稳定机器码，不得看到租约路径或内部锁细节。"""
    class DrainingBackend:
        alive = True

        async def request(self, _kind, _name, _args):
            raise CodegraphBackendDrainingError("内部锁路径 /secret/lease.lock")

    result = asyncio.run(
        cg_server._call_backend(
            DrainingBackend(),
            "demo",
            "codegraph_search",
            {"query": "Target"},
        )
    )

    payload = json.loads(result.content[0].text)
    assert result.isError is True
    assert payload["code"] == "upstream_unavailable"
    assert "排空" in payload["error"]
    assert "/secret/lease.lock" not in result.content[0].text
