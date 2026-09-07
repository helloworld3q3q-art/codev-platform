"""builtin.node (Node/Express 后端栈) 测试 — detect 真值 + analyze 合成 fixture 产出。

不绑项目名: detect 用合成的 express / koa / fastify / 无关 repo 验证基于 package.json
内容判定。analyze 在合成最小 repo 上扫出 backend_endpoint 节点 + schema 合法。
每测自清注册表, 避免污染进程级单例。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.graph.schema import AnalyzerResult, NodeKind
from codev_platform.plugins import clear_registry, registered_names, run_applicable
from codev_platform.plugins.builtin.node import (
    PLUGIN_NAME as NODE_NAME,
    NodeBackendPlugin,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---------------- detect: 基于 package.json 内容, 不基于项目名 ----------------

def test_node_detect_express(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"express": "^4.18.2"}}', encoding="utf-8"
    )
    assert NodeBackendPlugin().detect(tmp_path) is True


def test_node_detect_koa(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"koa": "^2.14.0"}}', encoding="utf-8"
    )
    assert NodeBackendPlugin().detect(tmp_path) is True


def test_node_detect_fastify_devdeps(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"devDependencies": {"fastify": "^4.0.0"}}', encoding="utf-8"
    )
    assert NodeBackendPlugin().detect(tmp_path) is True


def test_node_detect_negative_react_only(tmp_path):
    # 纯前端 React 仓不应被误命中本后端栈。
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18.2.0"}}', encoding="utf-8"
    )
    assert NodeBackendPlugin().detect(tmp_path) is False


def test_node_detect_negative_no_package_json(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    assert NodeBackendPlugin().detect(tmp_path) is False


# ---------------- analyze: 合成最小 repo, 验产出 + schema 合法 ----------------

def test_node_analyze_extracts_endpoints(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"express": "^4.18.2"}}', encoding="utf-8"
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "routes.js").write_text(
        "const router = express.Router();\n"
        "router.get('/api/v1/foo', (req, res) => res.json({}));\n"
        "router.post('/api/v1/bar', (req, res) => res.json({}));\n"
        "app.use('/health', healthRouter);\n"
        "const x = lodash.get(obj, 'a.b');\n",  # 非路由对象, 不应误命中
        encoding="utf-8",
    )
    result = NodeBackendPlugin().analyze(tmp_path, "demo")
    assert isinstance(result, AnalyzerResult)

    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    by_url = {n.meta["url"]: n for n in eps}
    assert "/api/v1/foo" in by_url
    assert "/api/v1/bar" in by_url
    assert "/health" in by_url
    assert by_url["/api/v1/foo"].meta["http_method"] == "GET"
    assert by_url["/api/v1/bar"].meta["http_method"] == "POST"
    assert by_url["/health"].meta["http_method"] == "USE"
    # lodash.get 的 obj='lodash' 不匹配路由对象, 故不产节点。
    assert all(n.meta.get("router_obj") in {"router", "app"} for n in eps)

    # schema 合法: 必填字段齐全 + node id 同构 + 可序列化往返。
    for n in eps:
        assert n.id.startswith("demo:backend_endpoint:")
        assert n.project_id == "demo"
        assert n.file is not None and n.line is not None
        assert n.language == "typescript"
        AnalyzerResult.from_dict(result.to_dict())  # 序列化往返不抛


def test_node_analyze_ts_express(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"koa": "^2.14.0", "@koa/router": "^12.0.0"}}',
        encoding="utf-8",
    )
    (tmp_path / "app.ts").write_text(
        "import Router from '@koa/router';\n"
        "const apiRouter = new Router();\n"
        "apiRouter.put('/api/items/:id', ctx => { ctx.body = {}; });\n",
        encoding="utf-8",
    )
    result = NodeBackendPlugin().analyze(tmp_path, "demo")
    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    assert len(eps) == 1
    assert eps[0].meta["url"] == "/api/items/:id"
    assert eps[0].meta["http_method"] == "PUT"
    assert eps[0].meta["router_obj"] == "apiRouter"


# ---------------- registry: 自动发现 + run_applicable ----------------

def test_node_plugin_autodiscovered():
    assert NODE_NAME in registered_names()


def test_run_applicable_on_synthetic_express_repo(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"express": "^4.18.2"}}', encoding="utf-8"
    )
    (tmp_path / "server.js").write_text(
        "const app = express();\n"
        "app.get('/ping', (req, res) => res.send('pong'));\n",
        encoding="utf-8",
    )
    results = run_applicable(tmp_path, "demo")
    by_name = {r.plugin: r for r in results}
    assert NODE_NAME in by_name and by_name[NODE_NAME].ok
    assert by_name[NODE_NAME].summary["nodes"] > 0
