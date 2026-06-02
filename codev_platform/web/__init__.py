"""codev_platform.web —— Web Backend (管理后端) 独立服务 (plan D1)。

独立 uvicorn 进程/端口, 与 agent 服务平级。复用 gateway/core.* 共享库, 不重造中间件/鉴权。
分层: routes / services / repositories / schemas / domain / integrations (纯业务);
HTTP 骨架 (envelope/分页/权限/app 工厂/枚举机制) 复用 core.httpkit。
"""
