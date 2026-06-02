"""通用栈插件测试 — builtin.dotnet (.NET / C# ASP.NET) detect 真值 + analyze 产出。

不绑项目名: detect 用合成的 ASP.NET / 无关 repo 验证基于内容判定; analyze 在合成
最小 controller fixture 上产出非空且 schema 合法的 backend_endpoint 节点。
autodiscovery 就绪 -> 还验证插件被 registry 自动发现 (无需改 registry)。
每测自清注册表, 避免污染进程级单例。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.graph.schema import AnalyzerResult, GraphNode, NodeKind
from codev_platform.plugins import clear_registry, registered_names, run_applicable
from codev_platform.plugins.builtin.dotnet import (
    PLUGIN_NAME as DOTNET_NAME,
    DotNetPlugin,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registry()
    yield
    clear_registry()


# ---------------- detect: 基于 repo 内容, 不基于项目名 ----------------

def test_dotnet_detect_by_aspnetcore_using(tmp_path):
    (tmp_path / "WeatherController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        "public class WeatherController : ControllerBase {}\n",
        encoding="utf-8",
    )
    assert DotNetPlugin().detect(tmp_path) is True


def test_dotnet_detect_by_apicontroller_attribute(tmp_path):
    (tmp_path / "Foo.cs").write_text(
        "[ApiController]\n[Route(\"api/[controller]\")]\npublic class FooController {}\n",
        encoding="utf-8",
    )
    assert DotNetPlugin().detect(tmp_path) is True


def test_dotnet_detect_by_http_attribute(tmp_path):
    (tmp_path / "Bar.cs").write_text(
        "public class BarController {\n  [HttpGet]\n  public int Get() => 1;\n}\n",
        encoding="utf-8",
    )
    assert DotNetPlugin().detect(tmp_path) is True


def test_dotnet_detect_negative_plain_csharp(tmp_path):
    # 纯 C# 类库, 无任何 ASP.NET 迹象 -> 不命中。
    (tmp_path / "Calc.cs").write_text(
        "public class Calc { public int Add(int a, int b) => a + b; }\n",
        encoding="utf-8",
    )
    assert DotNetPlugin().detect(tmp_path) is False


def test_dotnet_detect_negative_python(tmp_path):
    (tmp_path / "main.py").write_text("print('hi')\n", encoding="utf-8")
    assert DotNetPlugin().detect(tmp_path) is False


# ---------------- analyze: 合成最小 controller, 验产出 + schema ----------------

def test_dotnet_analyze_extracts_endpoints(tmp_path):
    (tmp_path / "ProductsController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        "\n"
        "[ApiController]\n"
        "[Route(\"api/[controller]\")]\n"
        "public class ProductsController : ControllerBase\n"
        "{\n"
        "    [HttpGet]\n"
        "    public IActionResult List() { return Ok(); }\n"
        "\n"
        "    [HttpGet(\"{id}\")]\n"
        "    public IActionResult GetById(int id) { return Ok(); }\n"
        "\n"
        "    [HttpPost]\n"
        "    public IActionResult Create() { return Ok(); }\n"
        "}\n",
        encoding="utf-8",
    )
    result = DotNetPlugin().analyze(tmp_path, "demo")
    assert isinstance(result, AnalyzerResult)

    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    assert len(eps) == 3

    by_key = {(n.meta["http_method"], n.meta["url"]): n for n in eps}
    assert ("GET", "/api/Products") in by_key
    assert ("GET", "/api/Products/{id}") in by_key
    assert ("POST", "/api/Products") in by_key

    # schema 合法: 每个节点都是 GraphNode, 必填字段非空, language 标 csharp。
    for n in eps:
        assert isinstance(n, GraphNode)
        assert n.id.startswith("demo:backend_endpoint:")
        assert n.project_id == "demo"
        assert n.file == "ProductsController.cs"
        assert n.line and n.line > 0
        assert n.language == "csharp"
        assert n.meta["controller"] == "ProductsController"
        # to_dict() 不报错, kind 序列化成裸字符串。
        d = n.to_dict()
        assert d["kind"] == NodeKind.BACKEND_ENDPOINT.value


def test_dotnet_analyze_method_level_route_only(tmp_path):
    # 无类级 [Route], 仅方法模板 -> url 由方法模板决定。
    (tmp_path / "PingController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        "public class PingController : ControllerBase {\n"
        "    [HttpGet(\"health/ping\")]\n"
        "    public IActionResult Ping() => Ok();\n"
        "}\n",
        encoding="utf-8",
    )
    result = DotNetPlugin().analyze(tmp_path, "demo")
    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    assert len(eps) == 1
    assert eps[0].meta["url"] == "/health/ping"
    assert eps[0].meta["http_method"] == "GET"
    assert eps[0].name == "PingController.Ping"


def test_dotnet_analyze_ignores_non_controller_class(tmp_path):
    # 非控制器类 (无 Controller 命名 / 不继承 ControllerBase) 即使有 [HttpGet] 也不算入口。
    (tmp_path / "Helper.cs").write_text(
        "public class Helper {\n"
        "    [HttpGet]\n"
        "    public int Get() => 1;\n"
        "}\n",
        encoding="utf-8",
    )
    result = DotNetPlugin().analyze(tmp_path, "demo")
    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    assert eps == []


# ---------------- 回归: 多 controller / 组合特性 / route 冲突 ----------------

def test_dotnet_multi_controller_routes_no_bleed(tmp_path):
    # 同一文件两个 controller, 各自 [Route] 不串味 (前一个的前缀不漏给后一个)。
    (tmp_path / "Multi.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        "\n"
        "[ApiController]\n"
        "[Route(\"api/foo\")]\n"
        "public class FooController : ControllerBase\n"
        "{\n"
        "    [HttpGet]\n"
        "    public IActionResult GetFoo() => Ok();\n"
        "}\n"
        "\n"
        "[ApiController]\n"
        "[Route(\"api/bar\")]\n"
        "public class BarController : ControllerBase\n"
        "{\n"
        "    [HttpGet]\n"
        "    public IActionResult GetBar() => Ok();\n"
        "}\n",
        encoding="utf-8",
    )
    result = DotNetPlugin().analyze(tmp_path, "demo")
    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    by_url = {n.meta["url"]: n for n in eps}
    assert set(by_url) == {"/api/foo", "/api/bar"}
    assert by_url["/api/foo"].meta["controller"] == "FooController"
    assert by_url["/api/bar"].meta["controller"] == "BarController"


def test_dotnet_combined_attribute_http_verb(tmp_path):
    # 一个方括号里多个特性 [HttpGet, Produces(...)] 不应丢端点。
    (tmp_path / "ComboController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        "[Route(\"api/combo\")]\n"
        "public class ComboController : ControllerBase\n"
        "{\n"
        "    [HttpGet, Produces(\"application/json\")]\n"
        "    public IActionResult List() => Ok();\n"
        "\n"
        "    [Produces(\"application/json\"), HttpPost(\"create\")]\n"
        "    public IActionResult Create() => Ok();\n"
        "}\n",
        encoding="utf-8",
    )
    result = DotNetPlugin().analyze(tmp_path, "demo")
    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    by_key = {(n.meta["http_method"], n.meta["url"]): n for n in eps}
    assert ("GET", "/api/combo") in by_key
    assert ("POST", "/api/combo/create") in by_key


def test_dotnet_route_conflict_emits_finding(tmp_path):
    # 同一 (METHOD, url) 两个 action -> 仅保留 1 节点, 但产 route_conflict Finding。
    (tmp_path / "DupController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        "[Route(\"api/dup\")]\n"
        "public class DupController : ControllerBase\n"
        "{\n"
        "    [HttpGet]\n"
        "    public IActionResult First() => Ok();\n"
        "\n"
        "    [HttpGet]\n"
        "    public IActionResult Second() => Ok();\n"
        "}\n",
        encoding="utf-8",
    )
    result = DotNetPlugin().analyze(tmp_path, "demo")
    eps = [n for n in result.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value]
    assert len(eps) == 1  # 去重保留首个

    conflicts = [f for f in result.findings if f.kind == "route_conflict"]
    assert len(conflicts) == 1
    f = conflicts[0]
    assert f.severity == "medium"
    assert f.meta["http_method"] == "GET"
    assert f.meta["url"] == "/api/dup"
    assert len(f.meta["occurrences"]) == 2
    assert f.node_ids == ["demo:backend_endpoint:GET:/api/dup"]


# ---------------- autodiscovery: registry 自动发现本插件 ----------------

def test_dotnet_plugin_autodiscovered():
    assert DOTNET_NAME in registered_names()


def test_dotnet_run_applicable_on_synthetic_repo(tmp_path):
    (tmp_path / "OrdersController.cs").write_text(
        "using Microsoft.AspNetCore.Mvc;\n"
        "[ApiController]\n[Route(\"api/orders\")]\n"
        "public class OrdersController : ControllerBase {\n"
        "    [HttpGet]\n    public IActionResult All() => Ok();\n"
        "}\n",
        encoding="utf-8",
    )
    results = run_applicable(tmp_path, "demo")
    by_name = {r.plugin: r for r in results}
    assert DOTNET_NAME in by_name
    assert by_name[DOTNET_NAME].ok
    assert by_name[DOTNET_NAME].summary["nodes"] > 0
