"""JS/TS request({ url, method }) API scanner tests."""
from __future__ import annotations

from codev_platform.graph.schema import NodeKind
from codev_platform.plugins.builtin._stack_scan.js_request import (
    scan_js_request_exports,
)


def _api_by_name(nodes):
    return {n.name: n for n in nodes if n.kind == NodeKind.FRONTEND_API_CALL.value}


def test_scan_export_function_request_object_template_prefix(tmp_path):
    api = tmp_path / "src" / "api" / "base"
    api.mkdir(parents=True)
    (api / "BaseBin.ts").write_text(
        "export function saveBaseBinApi(data) {\n"
        "  return request({\n"
        "    method: 'post',\n"
        "    url: `${ContextEnum.dip_imp_dsm}/baseBin/batchSaveData?userName=admin`,\n"
        "    data\n"
        "  })\n"
        "}\n",
        encoding="utf-8",
    )

    nodes = scan_js_request_exports(tmp_path, "p", suffixes=(".ts",))
    api_nodes = _api_by_name(nodes)
    assert api_nodes["saveBaseBinApi"].meta["url"] == "/baseBin/batchSaveData"
    assert api_nodes["saveBaseBinApi"].meta["http_method"] == "POST"


def test_scan_concat_and_relative_static_path(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "BaseCcs.ts").write_text(
        "export function getBaseCcsApi(data) {\n"
        "  return request({\n"
        "    method: 'get',\n"
        "    url: ContextEnum.dip_imp_dsm + 'baseCcs/getBaseCcs?' + toQuery(data)\n"
        "  })\n"
        "}\n",
        encoding="utf-8",
    )

    node = _api_by_name(scan_js_request_exports(tmp_path, "p", suffixes=(".ts",)))["getBaseCcsApi"]
    assert node.meta["url"] == "/baseCcs/getBaseCcs"
    assert node.meta["http_method"] == "GET"


def test_scan_dao_service_config_prefix_without_project_specific_branch(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "ImpDbLinkApi.ts").write_text(
        "const clientName = 'dip_imp_ism'\n"
        "export function queryAllEnabled() {\n"
        "  return request({\n"
        "    url: `${daoServiceClientConfig[clientName].serverUrl}/dbLink/queryAllEnabled`,\n"
        "    method: 'get'\n"
        "  })\n"
        "}\n",
        encoding="utf-8",
    )

    node = _api_by_name(scan_js_request_exports(tmp_path, "p", suffixes=(".ts",)))["queryAllEnabled"]
    assert node.meta["url"] == "/dbLink/queryAllEnabled"
    assert node.meta["http_method"] == "GET"


def test_scan_typescript_generic_request_call(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "Typed.ts").write_text(
        "export function queryTyped(data) {\n"
        "  return request<PageResult<Row>>({\n"
        "    url: `${ContextEnum.dip_imp_dsm}/typed/query`,\n"
        "    method: 'post',\n"
        "    data\n"
        "  })\n"
        "}\n",
        encoding="utf-8",
    )

    node = _api_by_name(scan_js_request_exports(tmp_path, "p", suffixes=(".ts",)))["queryTyped"]
    assert node.meta["url"] == "/typed/query"
    assert node.meta["http_method"] == "POST"


def test_scan_typescript_object_type_generic_request_call(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "TypedObject.ts").write_text(
        "export function queryTypedObject(data) {\n"
        "  return request<{ data: Row }>({\n"
        "    url: `${ContextEnum.dip_imp_dsm}/typed/object`,\n"
        "    method: 'get',\n"
        "    data\n"
        "  })\n"
        "}\n",
        encoding="utf-8",
    )

    node = _api_by_name(
        scan_js_request_exports(tmp_path, "p", suffixes=(".ts",))
    )["queryTypedObject"]
    assert node.meta["url"] == "/typed/object"
    assert node.meta["http_method"] == "GET"


def test_dynamic_path_segment_is_not_hardened_to_static_api(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "Dynamic.ts").write_text(
        "export function run(operate) {\n"
        "  return request({ url: `${ContextEnum.dip_imp_dsm}/baseCcsRel/${operate}` })\n"
        "}\n",
        encoding="utf-8",
    )

    assert scan_js_request_exports(tmp_path, "p", suffixes=(".ts",)) == []


def test_dynamic_middle_path_segment_is_not_hardened_to_suffix(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "DynamicMiddle.ts").write_text(
        "export function detail(id) {\n"
        "  return request({ url: `${ContextEnum.dip_imp_dsm}/base/${id}/detail`, method: 'get' })\n"
        "}\n",
        encoding="utf-8",
    )

    assert scan_js_request_exports(tmp_path, "p", suffixes=(".ts",)) == []


def test_non_http_object_call_with_url_is_ignored(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "Route.ts").write_text(
        "export function openProfile(router) {\n"
        "  return router.push({ url: '/settings/profile' })\n"
        "}\n",
        encoding="utf-8",
    )

    assert scan_js_request_exports(tmp_path, "p", suffixes=(".ts",)) == []


def test_export_const_does_not_absorb_following_export_function(tmp_path):
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "Boundary.ts").write_text(
        "export const helper = 1\n"
        "export function query() {\n"
        "  return request({ url: '/boundary/query', method: 'get' })\n"
        "}\n",
        encoding="utf-8",
    )

    nodes = scan_js_request_exports(tmp_path, "p", suffixes=(".ts",))
    assert list(_api_by_name(nodes)) == ["query"]
