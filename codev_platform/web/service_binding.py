"""rebind_web_services —— create_app(cfg) 启动时把 import 期绑了 `load_config()` 的 web 单例统一按 cfg 重绑。

独立于 app.py: app.py 模块级有 `app = create_app()`, import 它会触发装机副作用
(bind_account_stores / ensure_seed_admin / set_session_access_checker)。本逻辑单拎出来,
单测可直接 import 调用而不触发那些副作用(audit #7 config DI)。

背景: session_store / job_service / index_service / agent_client 在各自模块 import 时就
`bind_*(load_config())` 绑死; create_app(cfg) 的显式 cfg 原只作用 app factory + authenticator
→ 测试注入 cfg / 多实例时 authenticator 用一份 cfg、而 session/job 仍用 import 期的 load_config()。
本函数把这几处收进一处统一按 cfg 重绑。重赋模块属性(非 from-import 捕获): 同模块 handler 调用期
查全局即见新值; 跨模块 session 消费方走 `get_session_store()` 见新值(复刻 account_store getter 范式)。
"""
from __future__ import annotations


def rebind_web_services(cfg: dict) -> None:
    """按显式 cfg 重绑 session / job / index / agent_client 单例(仅 create_app 显式传 cfg 时调用)。"""
    from codev_platform.web.integrations.agent_client import AgentClient
    from codev_platform.web.routes import agent, indexes, jobs, memory
    from codev_platform.web.security import sessions
    from codev_platform.web.security.sessions import bind_session_store
    from codev_platform.web.services.index_service import (
        IndexService,
        make_reindex_dispatch_trigger,
    )
    from codev_platform.web.services.job_service import bind_job_service

    sessions.session_store = bind_session_store(cfg)
    jobs.job_service = bind_job_service(cfg, trigger=make_reindex_dispatch_trigger())
    indexes.index_service = IndexService(jobs.job_service)
    agent.agent_client = AgentClient(cfg)
    memory.agent_client = AgentClient(cfg)
