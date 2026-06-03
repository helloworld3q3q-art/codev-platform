"""builtin.backend_spring 插件测试 (unified-graph-lineage P1)。

验证 Spring MVC Controller -> backend_endpoint:
- detect 基于 repo 内容 (@RestController), 不靠目录名
- 类级 @RequestMapping base + 方法级 @Get/@Post/@RequestMapping 拼 url
- node id 与 FastAPI/Node 同构, language=java, handler 名捕获
- registry 自动发现本插件
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.plugins.builtin import _stack_scan
from codev_platform.plugins.builtin.backend_spring import PLUGIN_NAME, SpringPlugin

PID = "demo-spring"

_CONTROLLER = """\
package com.openclaw.stock.admin.controller;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/stock")
public class StockController {

    @GetMapping("/list")
    public Result list() { return null; }

    @PostMapping(value = "/create")
    public Result create(@RequestBody Dto dto) { return null; }

    @RequestMapping(value = "/legacy", method = RequestMethod.PUT)
    public Result legacy() { return null; }
}
"""

_NON_CONTROLLER = """\
package com.openclaw.stock.admin.service;

public class PlainService {
    public void doWork() { for (int i = 0; i < 3; i++) {} }
}
"""


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_detect_hits_on_controller(tmp_path: Path) -> None:
    _write(tmp_path, "src/StockController.java", _CONTROLLER)
    assert SpringPlugin().detect(tmp_path) is True


def test_detect_miss_without_java(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("no java here", encoding="utf-8")
    assert SpringPlugin().detect(tmp_path) is False


def test_detect_miss_on_plain_class(tmp_path: Path) -> None:
    _write(tmp_path, "src/PlainService.java", _NON_CONTROLLER)
    # 无 @RestController/@Controller 且无任何 Mapping -> 不命中。
    assert SpringPlugin().detect(tmp_path) is False


def test_scan_endpoints(tmp_path: Path) -> None:
    _write(tmp_path, "src/StockController.java", _CONTROLLER)
    nodes = _stack_scan.scan_spring(tmp_path, PID)
    by_url = {(n.meta["http_method"], n.meta["url"]): n for n in nodes}

    assert ("GET", "/api/stock/list") in by_url
    assert ("POST", "/api/stock/create") in by_url
    assert ("PUT", "/api/stock/legacy") in by_url

    get_list = by_url[("GET", "/api/stock/list")]
    assert get_list.kind == "backend_endpoint"
    assert get_list.language == "java"
    assert get_list.id == f"{PID}:backend_endpoint:GET:/api/stock/list"
    assert get_list.meta["base_path"] == "/api/stock"
    assert get_list.name == "list"  # handler 方法名捕获


def test_plain_class_yields_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "src/PlainService.java", _NON_CONTROLLER)
    assert _stack_scan.scan_spring(tmp_path, PID) == []


def test_node_id_matches_fastapi_shape_for_linker(tmp_path: Path) -> None:
    """同 url 的 Java 端点 id 形态与 FastAPI 一致 -> link_api_calls 可跨语言命中。"""
    _write(tmp_path, "src/StockController.java", _CONTROLLER)
    nodes = _stack_scan.scan_spring(tmp_path, PID)
    for n in nodes:
        assert n.id == f"{PID}:backend_endpoint:{n.meta['http_method']}:{n.meta['url']}"


def test_registry_autodiscovers_spring() -> None:
    from codev_platform.plugins import registry

    names = registry.registered_names()
    assert PLUGIN_NAME in names
