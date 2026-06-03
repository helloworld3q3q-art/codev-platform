"""通用栈插件测试 — FrontendReact / FastApi detect 真值 + analyze 产出 + run_applicable。

不绑项目名: detect 用合成的 React / FastAPI / 无关 repo 验证基于内容判定。analyze 在
本仓 (codev-platform) 跑出非空统一 AnalyzerResult。run_applicable(本仓) 含这两个插件。
每测自清注册表, 避免污染进程级单例。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.graph.schema import AnalyzerResult, NodeKind, EdgeKind
from codev_platform.plugins import clear_registry, registered_names, run_applicable
from codev_platform.plugins.builtin import _stack_scan
from codev_platform.plugins.builtin.backend_fastapi import (
    PLUGIN_NAME as FASTAPI_NAME,
    FastApiPlugin,
)
from codev_platform.plugins.builtin.frontend_react import (
    PLUGIN_NAME as REACT_NAME,
    FrontendReactPlugin,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---------------- detect: 基于 repo 内容, 不基于项目名 ----------------

def test_react_detect_by_package_json(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18.2.0"}}', encoding="utf-8"
    )
    assert FrontendReactPlugin().detect(tmp_path) is True


def test_react_detect_by_tsx_file(tmp_path):
    (tmp_path / "App.tsx").write_text("export default () => null;", encoding="utf-8")
    assert FrontendReactPlugin().detect(tmp_path) is True


def test_react_detect_negative(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    assert FrontendReactPlugin().detect(tmp_path) is False


def test_fastapi_detect_by_import(tmp_path):
    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8"
    )
    assert FastApiPlugin().detect(tmp_path) is True


def test_fastapi_detect_by_router_decorator(tmp_path):
    (tmp_path / "routes.py").write_text(
        '@router.get("/api/x")\ndef h():\n    return 1\n', encoding="utf-8"
    )
    assert FastApiPlugin().detect(tmp_path) is True


def test_fastapi_detect_negative(tmp_path):
    (tmp_path / "App.tsx").write_text("export default () => null;", encoding="utf-8")
    assert FastApiPlugin().detect(tmp_path) is False


# ---------------- analyze: 合成最小 repo, 验链路 ----------------

def test_fastapi_analyze_extracts_endpoint(tmp_path):
    (tmp_path / "routes.py").write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter()\n'
        '@router.post("/api/v1/foo", operation_id="postFoo")\n'
        'def foo():\n    return 1\n',
        encoding="utf-8",
    )
    result = FastApiPlugin().analyze(tmp_path, "demo")
    assert isinstance(result, AnalyzerResult)
    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    assert len(eps) == 1
    assert eps[0].name == "postFoo"
    assert eps[0].meta["url"] == "/api/v1/foo"
    assert eps[0].meta["http_method"] == "POST"


def test_react_analyze_links_to_backend(tmp_path):
    apis = tmp_path / "src" / "services" / "apis"
    apis.mkdir(parents=True)
    (apis / "fooapi.ts").write_text(
        "const commonUrl = '';\n"
        "export async function postFoo(data) {\n"
        "  return await post({ url: `${commonUrl}/api/v1/foo`, data });\n"
        "}\n",
        encoding="utf-8",
    )
    pages = tmp_path / "src" / "pages" / "foo"
    pages.mkdir(parents=True)
    (pages / "index.tsx").write_text(
        "import { postFoo } from '@/services/apis/fooapi';\n"
        "export default function Foo() { postFoo({}); return null; }\n",
        encoding="utf-8",
    )
    (tmp_path / "routes.py").write_text(
        '@router.post("/api/v1/foo")\ndef foo():\n    return 1\n', encoding="utf-8"
    )

    result = FrontendReactPlugin().analyze(tmp_path, "demo")
    assert isinstance(result, AnalyzerResult)
    api_calls = [n for n in result.nodes
                 if n.kind == NodeKind.FRONTEND_API_CALL.value]
    routes = [n for n in result.nodes if n.kind == NodeKind.FRONTEND_ROUTE.value]
    renders = [e for e in result.edges if e.kind == EdgeKind.RENDERS.value]
    assert len(api_calls) == 1
    assert len(routes) == 1
    assert len(renders) == 1  # 页面 -> api 调用
    # calls_api 不再由前端插件产 (改由核心 linker pass 跨插件统一产, 见 test_ingest_linker)。
    assert [e for e in result.edges if e.kind == EdgeKind.CALLS_API.value] == []
    # 匹配逻辑仍单一真值源可用: 前端 api_call + 同 url 后端 endpoint -> 1 条精确 calls_api。
    backend = _stack_scan.scan_fastapi(tmp_path, "demo")
    linked = _stack_scan.link_api_calls(api_calls, backend)
    assert len(linked) == 1 and linked[0].confidence == 1.0


# ---------------- analyze: 在本仓 (codev-platform) 跑出非空 ----------------

def test_react_analyze_nonempty_on_repo():
    result = FrontendReactPlugin().analyze(REPO_ROOT, "codev-platform")
    assert isinstance(result, AnalyzerResult)
    assert len(result.nodes) > 0  # web-ui 有 frontend_api_call / frontend_route


def test_fastapi_analyze_nonempty_on_repo():
    result = FastApiPlugin().analyze(REPO_ROOT, "codev-platform")
    assert isinstance(result, AnalyzerResult)
    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    assert len(eps) > 0  # codev_platform/web/routes/*.py 有 @router.<method>


# ---------------- registry: 注册可见 + run_applicable 命中 ----------------

def test_stack_plugins_registered():
    names = registered_names()
    assert REACT_NAME in names
    assert FASTAPI_NAME in names


def test_run_applicable_on_repo_includes_stack_plugins():
    results = run_applicable(REPO_ROOT, "codev-platform")
    by_name = {r.plugin: r for r in results}
    assert REACT_NAME in by_name and by_name[REACT_NAME].ok
    assert FASTAPI_NAME in by_name and by_name[FASTAPI_NAME].ok
    assert by_name[REACT_NAME].summary["nodes"] > 0
    assert by_name[FASTAPI_NAME].summary["nodes"] > 0
