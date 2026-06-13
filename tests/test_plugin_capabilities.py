"""Phase 9 capability matrix + produces 归属护栏 (静态, 不依赖某项目恰好被 ingest)。

补 test_plugin_owner_uniqueness 的盲区: 那道闸要某项目真 ingest 出该 kind 才覆盖
(无 .NET fixture -> dotnet 产 backend_endpoint 却漏登记 KIND_OWNERS 静默漏检)。本组用
插件自描述 produces 做**静态**断言, 无需 fixture 即锁定归属与能力视图正确。
"""
from __future__ import annotations

from codev_platform.graph.schema import NodeKind
from codev_platform.plugins import list_plugins, registered_names
from codev_platform.plugins.capabilities import describe_capabilities
from codev_platform.plugins.ownership import POST_PASS_OWNERS, kind_owners

_VALID_KINDS = {k.value for k in NodeKind}


def test_all_plugin_produces_are_valid_nodekinds() -> None:
    """每个注册插件声明的 produces 必须是合法 NodeKind 值 (typo / 漂移护栏)。"""
    for p in list_plugins():
        for kind in getattr(p, "produces", ()) or ():
            assert kind in _VALID_KINDS, f"{p.name} produces 非法 NodeKind: {kind!r}"


def test_dotnet_owns_backend_endpoint() -> None:
    """回归锁: builtin.dotnet 产 backend_endpoint, 必须在 kind_owners 里 (2026-06-13 修漏登记)。

    旧 KIND_OWNERS 手维护表漏了 dotnet, 而既有 owner 闸无 .NET fixture 抓不到。produces
    派生后结构性消除该漏洞 —— 此断言守它不复发。
    """
    owners = kind_owners()
    assert "builtin.dotnet" in owners.get(NodeKind.BACKEND_ENDPOINT.value, set())


def test_backend_endpoint_multi_owner_covers_all_stacks() -> None:
    """backend_endpoint 是语言中性 kind, 四个后端栈插件都应是其 owner (territory 不重叠)。"""
    owners = kind_owners()[NodeKind.BACKEND_ENDPOINT.value]
    assert owners >= {
        "builtin.backend_fastapi", "builtin.backend_spring",
        "builtin.node", "builtin.dotnet",
    }


def test_kind_owners_matches_known_baseline() -> None:
    """派生归属表覆盖已知基线 (防 produces 声明被误删/改 -> 静默丢 owner)。"""
    owners = kind_owners()
    assert owners[NodeKind.DB_TABLE.value] == {"builtin.sql"}
    assert owners[NodeKind.DB_COLUMN.value] == {"builtin.sql"}
    assert owners[NodeKind.FRONTEND_COMPONENT.value] == {"builtin.vue"}
    assert owners[NodeKind.FRONTEND_ROUTE.value] == {"builtin.frontend_react", "builtin.vue"}
    assert owners[NodeKind.FRONTEND_API_CALL.value] == {"builtin.frontend_react", "builtin.vue"}


def test_post_pass_owners_merged() -> None:
    """非插件 post-pass 产物 (frontend_module / business_domain) 经 POST_PASS_OWNERS 并入。"""
    owners = kind_owners()
    for kind, names in POST_PASS_OWNERS.items():
        assert owners.get(kind) == names


def test_describe_capabilities_covers_all_plugins() -> None:
    """capability 视图每个注册插件一行, produces / prov_source 与插件自描述一致 (派生非另立真值源)。"""
    caps = describe_capabilities()
    assert {c.plugin for c in caps} == set(registered_names())
    by_name = {c.plugin: c for c in caps}
    for p in list_plugins():
        assert by_name[p.name].produces == tuple(getattr(p, "produces", ()) or ())
        assert by_name[p.name].prov_source == getattr(p, "prov_source", None)
