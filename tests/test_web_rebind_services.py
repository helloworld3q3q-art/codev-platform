"""rebind_web_services —— create_app(cfg) 统一重绑 web 单例(audit #7 config DI)。

钉死: ①显式 cfg 重绑 session/job/index/agent_client 且彼此一致 ②get_session_store() 跨模块消费方
能看到重绑（修复前 from-import 捕获旧对象的 bug）。需 fastapi（route 模块依赖）→ WSL 跑；
autouse fixture 快照/还原模块全局防测试污染。这里直接测试 service_binding 的单一职责，
完整应用工厂契约由 web foundation 与运行身份状态面测试覆盖。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from codev_platform.web.integrations.agent_client import AgentClient  # noqa: E402
from codev_platform.web.routes import agent, indexes, jobs, memory  # noqa: E402
from codev_platform.web.security import sessions  # noqa: E402
from codev_platform.web.security.sessions import get_session_store  # noqa: E402
from codev_platform.web.service_binding import rebind_web_services  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_singletons():
    # 快照 5 个会被重绑的模块全局, 测试后还原(否则污染共享同进程的其余 web 测试)。
    snap = (sessions.session_store, jobs.job_service, indexes.index_service,
            agent.agent_client, memory.agent_client)
    yield
    (sessions.session_store, jobs.job_service, indexes.index_service,
     agent.agent_client, memory.agent_client) = snap


def test_rebind_swaps_session_store_and_getter_follows():
    old = sessions.session_store
    rebind_web_services({"agent": {"base_url": "http://x:1"}})
    assert sessions.session_store is not old          # 重绑换了新实例
    assert get_session_store() is sessions.session_store  # 跨模块消费方经 getter 看到新值(核心修复)


def test_rebind_is_consistent_across_singletons():
    cfg = {"agent": {"base_url": "http://test-rebind:9999"}}
    rebind_web_services(cfg)
    # agent_client 两处路由都按同一 cfg 重绑(base_url 取自 cfg)
    assert "test-rebind:9999" in agent.agent_client._base_url
    assert "test-rebind:9999" in memory.agent_client._base_url
    assert isinstance(agent.agent_client, AgentClient)
    # index_service 包的就是当前活动 job_service(不是旧捕获)
    assert indexes.index_service._jobs is jobs.job_service


def test_rebind_is_idempotent_swap():
    # 重绑两次各换新实例(幂等可重复), 末次为活动值。
    rebind_web_services({"agent": {"base_url": "http://a:1"}})
    first = sessions.session_store
    rebind_web_services({"agent": {"base_url": "http://b:2"}})
    assert sessions.session_store is not first
    assert "b:2" in agent.agent_client._base_url


# 注：本文件只验证重绑器；create_app 的“显式 cfg 才重绑”门控由完整应用测试覆盖。
