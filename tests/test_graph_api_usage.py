"""前端 API 使用归因引擎 (api_usage): 页面源码引用某 url_registry 常量名 → 精确 uses_api 边。

覆盖: 命中精确归因 / 排除声明文件 / 噪声名不匹配 / 内联(非 registry)零产出(量化项目安全) /
读源每文件一次。
"""
from __future__ import annotations

from codev_platform.graph.schema import EdgeKind, GraphNode, NodeKind
from codev_platform.plugins.builtin._stack_scan.api_usage import resolve_api_usage_edges

_PID = "t"


def _api(name, file, *, registry=True):
    meta = {"url": "/x/" + name, "url_registry": True} if registry else {"url": "/x/" + name}
    return GraphNode(id=f"{_PID}:frontend_api_call:{file}:{name}", kind=NodeKind.FRONTEND_API_CALL.value,
                     name=name, project_id=_PID, file=file, meta=meta)


def _comp(file):
    return GraphNode(id=f"{_PID}:frontend_component:{file}", kind=NodeKind.FRONTEND_COMPONENT.value,
                     name=file.split("/")[-1], project_id=_PID, file=file)


def test_const_reference_attributes_to_using_page_only():
    nodes = [
        _api("PICK_CHECK_TURN", "common/js/URL.js"),
        _comp("common/js/URL.js"),
        _comp("pages/scan.vue"),
        _comp("pages/other.vue"),
    ]
    src = {
        "common/js/URL.js": "export const PICK_CHECK_TURN = SUFFIX + 'task/check/container';",
        "pages/scan.vue": "var url = URLRoot.PICK_CHECK_TURN.stringFormat(a, b);",
        "pages/other.vue": "var x = URLRoot.SOMETHING_ELSE;",   # 没引用该常量
    }
    edges = resolve_api_usage_edges(nodes, lambda f: src.get(f, ""))
    # 只有 scan.vue 命中; 声明文件 URL.js 自身排除; other.vue 不命中
    assert len(edges) == 1
    e = edges[0]
    assert e.kind == EdgeKind.USES_API.value
    assert e.source == f"{_PID}:frontend_component:pages/scan.vue"
    assert e.target == f"{_PID}:frontend_api_call:common/js/URL.js:PICK_CHECK_TURN"


def test_noise_short_names_not_matched():
    # minified bundle 的噪声名(单字母 / urls)不当常量, 否则满仓乱匹配假边。
    nodes = [
        _api("j", "static/index.min.js"),
        _api("urls", "static/index.min.js"),
        _comp("pages/a.vue"),
    ]
    src = {"pages/a.vue": "let j = 1; const urls = [];"}   # 含 j / urls 但它们不是真常量
    assert resolve_api_usage_edges(nodes, lambda f: src.get(f, "")) == []


def test_inline_api_calls_zero_output():
    # 量化项目: api_call 全内联(url_registry 无)→ 引擎 no-op, 零影响。
    nodes = [
        _api("GET_STOCK", "src/api/stockapi.ts", registry=False),
        _comp("src/views/stock.vue"),
    ]
    src = {"src/views/stock.vue": "import { GET_STOCK } from '../api/stockapi'; GET_STOCK();"}
    assert resolve_api_usage_edges(nodes, lambda f: src.get(f, "")) == []


def test_source_read_once_per_file():
    # 驱动应每文件只读一次源码(跨策略共享 token), 不重复 IO。
    nodes = [_api("DATA_DICT", "URL.js"), _comp("URL.js"), _comp("pages/p.vue")]
    reads: list[str] = []

    def reader(f):
        reads.append(f)
        return {"URL.js": "export const DATA_DICT='/x';",
                "pages/p.vue": "URLRoot.DATA_DICT"}.get(f, "")

    resolve_api_usage_edges(nodes, reader)
    assert sorted(reads) == sorted(set(reads))   # 无重复读
