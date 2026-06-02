"""builtin.vue 插件测试 — detect 真值 (命中本栈 / 不误命中无关 repo) + analyze 合成产出。

不绑项目名: detect 用合成 Vue / 无关 repo 验证基于 repo 内容判定。analyze 在临时小样本
fixture 上产出非空且 schema 合法 (frontend_component / frontend_route / frontend_api_call
+ renders / calls_api)。每测自清注册表, 避免污染进程级单例。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.graph.schema import AnalyzerResult, EdgeKind, NodeKind
from codev_platform.plugins import (
    clear_registry,
    registered_names,
    run_applicable,
)
from codev_platform.plugins.builtin.vue import PLUGIN_NAME as VUE_NAME, VuePlugin


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---------------- detect: 基于 repo 内容, 不基于项目名 ----------------

def test_vue_detect_by_package_json(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"vue": "^3.4.0"}}', encoding="utf-8"
    )
    assert VuePlugin().detect(tmp_path) is True


def test_vue_detect_by_vue_file(tmp_path):
    (tmp_path / "App.vue").write_text(
        "<template><div/></template>", encoding="utf-8"
    )
    assert VuePlugin().detect(tmp_path) is True


def test_vue_detect_negative_python_repo(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    assert VuePlugin().detect(tmp_path) is False


def test_vue_detect_negative_react_repo(tmp_path):
    """纯 React 仓 (有 react 依赖 + tsx, 无 vue / *.vue) 不被 vue 插件误命中。"""
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18.2.0"}}', encoding="utf-8"
    )
    (tmp_path / "App.tsx").write_text(
        "export default () => null;", encoding="utf-8"
    )
    assert VuePlugin().detect(tmp_path) is False


# ---------------- analyze: 合成最小 repo, 验链路 ----------------

def _build_vue_fixture(root: Path) -> None:
    (root / "package.json").write_text(
        '{"dependencies": {"vue": "^3.4.0", "vue-router": "^4.3.0", '
        '"axios": "^1.6.0"}}',
        encoding="utf-8",
    )
    src = root / "src"
    views = src / "views"
    views.mkdir(parents=True)
    # SFC 组件 + 内联 api 调用 (template + script setup)。
    (views / "FooView.vue").write_text(
        "<template>\n  <div>{{ data }}</div>\n</template>\n"
        "<script setup>\n"
        "import { ref } from 'vue';\n"
        "import axios from 'axios';\n"
        "const data = ref(null);\n"
        "async function load() {\n"
        "  const r = await axios.post('/api/v1/foo');\n"
        "  data.value = r.data;\n"
        "}\n"
        "</script>\n",
        encoding="utf-8",
    )
    (views / "BarView.vue").write_text(
        "<template><span/></template>\n", encoding="utf-8"
    )
    # Vue Router 路由表。
    router = src / "router"
    router.mkdir(parents=True)
    (router / "index.ts").write_text(
        "import { createRouter, createWebHistory } from 'vue-router';\n"
        "import FooView from '@/views/FooView.vue';\n"
        "import BarView from '@/views/BarView.vue';\n"
        "const routes = [\n"
        "  { path: '/foo', name: 'FooView', component: FooView },\n"
        "  { path: '/bar', name: 'BarView', component: BarView },\n"
        "];\n"
        "export default createRouter({ history: createWebHistory(), routes });\n",
        encoding="utf-8",
    )
    # 同仓 FastAPI 后端 (calls_api 边的目标参照, 不并入 vue 结果)。
    (root / "server.py").write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter()\n'
        '@router.post("/api/v1/foo")\n'
        'def foo():\n    return 1\n',
        encoding="utf-8",
    )


def test_vue_analyze_produces_valid_nonempty(tmp_path):
    _build_vue_fixture(tmp_path)
    result = VuePlugin().analyze(tmp_path, "demo")

    assert isinstance(result, AnalyzerResult)
    assert result.plugin == VUE_NAME

    components = [
        n for n in result.nodes
        if n.kind == NodeKind.FRONTEND_COMPONENT.value
    ]
    routes = [
        n for n in result.nodes if n.kind == NodeKind.FRONTEND_ROUTE.value
    ]
    api_calls = [
        n for n in result.nodes if n.kind == NodeKind.FRONTEND_API_CALL.value
    ]
    renders = [e for e in result.edges if e.kind == EdgeKind.RENDERS.value]
    calls_api = [e for e in result.edges if e.kind == EdgeKind.CALLS_API.value]

    # 2 个 SFC -> 2 个 component。
    assert len(components) == 2
    stems = {n.name for n in components}
    assert {"FooView", "BarView"} <= stems
    assert all(n.language == "vue" for n in components)

    # 2 条路由 -> 2 个 route 节点 + 2 条 renders 边 (route -> component)。
    assert len(routes) == 2
    assert len(renders) == 2

    # FooView 内联 axios.post('/api/v1/foo') -> 1 个 api_call。
    assert len(api_calls) >= 1
    foo_api = [n for n in api_calls if n.meta.get("url") == "/api/v1/foo"]
    assert len(foo_api) == 1

    # calls_api 边: api 调用 -> 同仓 FastAPI endpoint (URL 精确匹配)。
    assert len(calls_api) == 1
    assert calls_api[0].confidence == 1.0

    # schema 合法: 所有节点能 to_dict / from_dict 往返。
    for n in result.nodes:
        assert n.id and n.kind and n.project_id == "demo"
        from codev_platform.graph.schema import GraphNode
        assert GraphNode.from_dict(n.to_dict()).id == n.id
    for e in result.edges:
        assert e.source and e.target and e.kind


def test_vue_analyze_template_only_component(tmp_path):
    """template-only (无 script / 无 api) 的 SFC 仍产出 component 节点。"""
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"vue": "^3.4.0"}}', encoding="utf-8"
    )
    (tmp_path / "Plain.vue").write_text(
        "<template><p>hi</p></template>\n", encoding="utf-8"
    )
    result = VuePlugin().analyze(tmp_path, "demo")
    comps = [
        n for n in result.nodes
        if n.kind == NodeKind.FRONTEND_COMPONENT.value
    ]
    assert len(comps) == 1
    assert comps[0].name == "Plain"


# ---------------- registry: 自动发现 + run_applicable ----------------

def test_vue_plugin_autoregistered():
    assert VUE_NAME in registered_names()


def test_run_applicable_on_vue_fixture(tmp_path):
    _build_vue_fixture(tmp_path)
    results = run_applicable(tmp_path, "demo")
    by_name = {r.plugin: r for r in results}
    assert VUE_NAME in by_name
    assert by_name[VUE_NAME].ok
    assert by_name[VUE_NAME].summary["nodes"] > 0
