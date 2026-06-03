"""codev-platform agent — 只读代码理解 HTTP 服务(P0).

分层:
    service.py   FastAPI 暴露层
    loop.py      AgentLoop 循环引擎
    brain/       LLMProvider 抽象 + 各厂商适配器(可插拔)
    tools/       Tool 抽象 + 平台能力封装(impact / codegraph / search_docs)
    session/trace/config/schemas  支撑

核心不依赖具体厂商:loop 只认 brain.base 的中性类型 + tools.base 的 Tool 抽象。
换模型只换 registry 返回的 provider 实例,loop/tools/service 零改。
"""
