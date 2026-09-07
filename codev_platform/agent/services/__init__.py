"""application 层 — 业务编排. 路由(传输层)只调本层,不直接拼 loop/session.

分层:
    routes/(传输:HTTP DTO <-> domain)
      -> services/(编排:会话 + provider + loop,可被 HTTP / 流式 / CLI 复用)
        -> loop / deps / brain / tools(domain + infra)
"""
