"""FastAPI/Python 调用边 resolver 测试 —— 重点验证「方法名 BFS 穿透 DI」。

codegraph 追不动 self._store.x()(要先解析 DI 类型);本 resolver 用方法名 BFS 绕过类型解析。
测试构造临时 repo: endpoint handler -> service.handle() -> self._store.set_task_state(),
验证 resolver 能跨 DI 把 endpoint 连到碰表 backend_function。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.call_resolvers.base import CallResolver
from codev_platform.graph.call_resolvers.fastapi import FastApiCallResolver
from codev_platform.graph.schema import EdgeKind, GraphNode, NodeKind


def _ep(pid: str, handler: str, *, lang: str = "python") -> GraphNode:
    return GraphNode(
        id=f"{pid}:backend_endpoint:POST:/x/{handler}",
        kind=NodeKind.BACKEND_ENDPOINT.value,
        name=handler, project_id=pid, language=lang,
        meta={"handler": handler},
    )


def _fn(pid: str, file: str, name: str) -> GraphNode:
    return GraphNode(
        id=f"{pid}:backend_function:{file}:{name}",
        kind=NodeKind.BACKEND_FUNCTION.value,
        name=name, project_id=pid, file=file,
    )


def _write(repo: Path, rel: str, body: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_satisfies_protocol():
    assert isinstance(FastApiCallResolver(), CallResolver)


def test_applies(tmp_path):
    r = FastApiCallResolver()
    ep_py = _ep("p", "ask")
    ep_java = _ep("p", "list", lang="java")
    fn = _fn("p", "store.py", "set_task_state")
    assert r.applies(tmp_path, [ep_py, fn]) is True
    assert r.applies(tmp_path, [ep_py]) is False        # 无碰表函数 → 不跑
    assert r.applies(tmp_path, [ep_java, fn]) is False   # 纯 java 端点 → 交给 spring/codegraph
    assert r.applies(tmp_path, []) is False


def test_resolve_crosses_di(tmp_path):
    # handler ask -> svc.handle() -> self._store.set_task_state()  (DI: codegraph 追不动)
    _write(tmp_path, "app.py", (
        "class Svc:\n"
        "    def __init__(self, store):\n"
        "        self._store = store\n"
        "    def handle(self):\n"
        "        self._store.set_task_state(1)\n"
        "\n"
        "def ask():\n"
        "    svc.handle()\n"
    ))
    ep = _ep("proj", "ask")
    fn = _fn("proj", "store.py", "set_task_state")
    edges = FastApiCallResolver().resolve(tmp_path, "proj", [ep, fn])
    assert len(edges) == 1
    e = edges[0]
    assert e.source == ep.id
    assert e.target == fn.id
    assert e.kind == EdgeKind.CALLS.value
    assert e.confidence == 0.65          # < codegraph 0.7: 仅补 DI 盲区, 精确解析优先
    assert e.meta["resolver"] == "fastapi"
    assert e.meta["via_handler"] == "ask"


def test_resolve_skips_generic_names(tmp_path):
    # handler 只调通用名 get()(黑名单) → 即便有碰表函数恰叫 get 也不连(防过连)。
    _write(tmp_path, "app.py", "def ask():\n    self._store.get(1)\n")
    ep = _ep("proj", "ask")
    fn = _fn("proj", "store.py", "get")
    assert FastApiCallResolver().resolve(tmp_path, "proj", [ep, fn]) == []


def test_resolve_depth_3_limit(tmp_path):
    # 深度 4 链(handler→a→b→c→碰表) 超 _MAX_DEPTH=3, 末端追不到。
    _write(tmp_path, "chain.py", (
        "def h():\n    a()\n"
        "def a():\n    b()\n"
        "def b():\n    c()\n"
        "def c():\n    deep_save()\n"
    ))
    ep = _ep("proj", "h")
    fn = _fn("proj", "store.py", "deep_save")
    assert FastApiCallResolver().resolve(tmp_path, "proj", [ep, fn]) == []


def test_resolve_no_path_no_edge(tmp_path):
    # handler 不调用任何碰表函数 → 无边。
    _write(tmp_path, "app.py", "def ask():\n    return 1\n")
    ep = _ep("proj", "ask")
    fn = _fn("proj", "store.py", "set_task_state")
    assert FastApiCallResolver().resolve(tmp_path, "proj", [ep, fn]) == []


def test_resolve_same_name_merge_is_known_tradeoff(tmp_path):
    # 文档化权衡(fastapi.py docstring + _scan_calls): 全局按**函数名**合并 → 跨文件同名函数
    # 调用树被并到一起, 会"串台"。这里 a.py 的 health 经 save 到碰表, b.py 的 health 啥也不调,
    # 但合并后端点 handler=health 仍可达 real_write → 连一条弱信号边(conf 0.65, 非 bug)。
    # 锁定此行为: 它是穿透 DI 的代价(按 file 隔离会断跨文件 DI 链), 留给未来类型推断版收窄。
    _write(tmp_path, "a.py", "def health():\n    save()\ndef save():\n    real_write()\n")
    _write(tmp_path, "b.py", "def health():\n    return 1\n")
    ep = _ep("proj", "health")
    fn = _fn("proj", "store.py", "real_write")
    edges = FastApiCallResolver().resolve(tmp_path, "proj", [ep, fn])
    assert len(edges) == 1
    assert edges[0].confidence == 0.65  # 弱信号: 影响分析消费方可按 conf 阈值过滤


def test_resolve_dedups_same_target(tmp_path):
    # handler 经两条路径都到同一碰表函数 → 仅一条边。
    _write(tmp_path, "app.py", (
        "def ask():\n"
        "    a()\n"
        "    b()\n"
        "def a():\n"
        "    save()\n"
        "def b():\n"
        "    save()\n"
    ))
    ep = _ep("proj", "ask")
    fn = _fn("proj", "store.py", "save")
    edges = FastApiCallResolver().resolve(tmp_path, "proj", [ep, fn])
    assert len(edges) == 1
