"""前端 URL 注册文件提取(代码基础层)—— 企业前端集中声明 API 路径常量的检测。

验证: const 解析 + 同文件 SUFFIX 拼接 + URL 归一化(剥 query/模板参)+ 启发式/声明识别 +
端到端经 _link 连后端(URL 匹配)。通用不绑项目。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.graph.schema import EdgeKind, GraphNode, NodeKind
from codev_platform.plugins.builtin._stack_scan import link_api_calls, scan_url_registry

_URL_JS = """\
// 接口地址集中声明
export const SUFFIX = '/pda/';
export const CURRENCY_LIST = '/pda/currency/list'; // 货币类型
export const GET_NEW_VERSION = '/pda/getNewVersion?packageName={0}&type=ANDROID';
export const LOGIN = SUFFIX + 'login?local={0}';      // 拼接
export const SITE_LIST = SUFFIX + 'sit/list';
export const DYNAMIC = '/pda/x/' + someVar;           // 含变量 → resolve 不了, 跳过
export const NOT_API = 'hello world';                 // 非路径 → 跳过
"""


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content, encoding="utf-8")
    return tmp_path


def test_extracts_and_normalizes_url_constants(tmp_path):
    _write(tmp_path, "common/js/URL.js", _URL_JS)
    nodes = scan_url_registry(tmp_path, "p", declared_files=["common/js/URL.js"])
    by_name = {n.name: n.meta["url"] for n in nodes}
    assert by_name["CURRENCY_LIST"] == "/pda/currency/list"
    assert by_name["GET_NEW_VERSION"] == "/pda/getNewVersion"    # 剥 ?query
    assert by_name["LOGIN"] == "/pda/login"                       # SUFFIX 拼接 + 剥 query
    assert by_name["SITE_LIST"] == "/pda/sit/list"               # 拼接
    assert "DYNAMIC" not in by_name                               # 含变量, 不猜
    assert "NOT_API" not in by_name                              # 非 API 路径
    assert all(n.kind == NodeKind.FRONTEND_API_CALL.value for n in nodes)
    assert all(n.meta.get("url_registry") for n in nodes)


def test_heuristic_auto_detect_without_declared(tmp_path):
    # 无 declared_files → 启发式: 高密度 API 路径文件被识别, 普通文件不被
    _write(tmp_path, "src/api/urls.js", _URL_JS)
    _write(tmp_path, "src/util.js", "export const x = 1;\nexport const name = 'foo';\n")
    nodes = scan_url_registry(tmp_path, "p", min_paths=3)  # 启发式(fixture 4 路径, 阈值 3)
    files = {n.file for n in nodes}
    assert "src/api/urls.js" in files
    assert "src/util.js" not in files          # 普通文件(0 路径)不误判


def test_links_registry_urls_to_backend(tmp_path):
    # 端到端: 注册文件的 URL 经 _link 连到同 url 后端端点
    _write(tmp_path, "common/js/URL.js", _URL_JS)
    fe = scan_url_registry(tmp_path, "p", declared_files=["common/js/URL.js"])
    backend = [
        GraphNode(id="ep1", kind=NodeKind.BACKEND_ENDPOINT.value, name="currencyList",
                  project_id="p", file="C.java", meta={"url": "/pda/currency/list", "http_method": "POST"}),
        GraphNode(id="ep2", kind=NodeKind.BACKEND_ENDPOINT.value, name="login",
                  project_id="p", file="L.java", meta={"url": "/pda/login", "http_method": "POST"}),
    ]
    edges = link_api_calls(fe, backend)
    targets = {e.target for e in edges if e.kind == EdgeKind.CALLS_API.value}
    assert "ep1" in targets and "ep2" in targets   # 前端声明的 URL 连上后端


def test_declared_missing_file_no_crash(tmp_path):
    assert scan_url_registry(tmp_path, "p", declared_files=["nope/URL.js"]) == []
