"""graph / reports 端点的多租户隔离闸守护(2026-06-11)。

平台是多组织(成员跨 org/project)。org/project 隔离由 `require_project_access` 在 HTTP 层统一
把关(其跨 org 拒绝逻辑已被 test_session_project_access / test_web_projects / test_acl 覆盖)。
本文件守护**每个 graph/reports 端点都真挂了这道闸** —— 防新增图谱端点漏挂 Depends 造成
"org A 用户裸读 org B 项目图谱"的隔离漏洞(图谱端点缺成对正反向 e2e 用例的补强, 见审计盘点)。

graph store 本身只 project_id 隔离(无 org_id), org 隔离完全靠这道闸 → 这道闸必须无遗漏地
覆盖所有图谱端点, 故用结构性断言锁死(比单个 e2e 更全: 覆盖全部端点而非一个)。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from codev_platform.core.httpkit.permissions import require_project_access  # noqa: E402
from codev_platform.web.routes import graph as graph_routes  # noqa: E402
from codev_platform.web.routes import reports as reports_routes  # noqa: E402
from codev_platform.web.security.deps import require_platform_admin  # noqa: E402

# 两类合法租户闸: require_project_access = 项目级(org/project 隔离, 跨 org 拒绝);
# require_platform_admin = 平台管理员级(跨项目全局报表如 token/mcp 用量, 非 project-scoped, 更严)。
# 端点挂任一即合规; 都没挂 = 裸奔隔离漏洞。
_TENANCY_GATES = {require_project_access, require_platform_admin}


def _gate_in_dependants(route) -> bool:
    """递归 route 依赖树, 查是否挂了任一租户闸(Depends 链任意层)。"""
    seen: list = []

    def _walk(dep):
        for sub in getattr(dep, "dependencies", []):
            seen.append(getattr(sub, "call", None))
            _walk(sub)

    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    _walk(dependant)
    return any(g in seen for g in _TENANCY_GATES)


@pytest.mark.parametrize("router,label", [
    (graph_routes.router, "graph"),
    (reports_routes.router, "reports"),
])
def test_all_endpoints_have_tenancy_gate(router, label):
    """每个 graph/reports API 路由都必须挂租户闸(项目级 require_project_access 或管理员级
    require_platform_admin); 都没挂 = org/project 隔离裸奔漏洞。"""
    api_routes = [r for r in router.routes if getattr(r, "path", "").startswith("/api/")]
    assert api_routes, f"{label} router 没有 /api 路由(测试前提失效)"
    ungated = [r.path for r in api_routes if not _gate_in_dependants(r)]
    assert not ungated, (
        f"{label} 端点未挂任何租户闸(org/project 隔离漏洞, org A 用户可裸读 org B 项目): {ungated}")
