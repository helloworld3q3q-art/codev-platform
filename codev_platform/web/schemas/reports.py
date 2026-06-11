"""Reports 组 schema (Track A5) —— 影响分析报告 + 跨层查询的请求/响应。

影响分析结果天然嵌套 (按层分组 + 变长层),故响应用 dict 透传 (与 graph 组 force-graph
data 同范式),不为每层硬编字段。请求是简单的节点引用 (id 或 name)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ImpactRequest(BaseModel):
    nodeRef: str  # 节点 id 或 name (表名 / 端点名 / 函数名 / 前端节点)


class TableUsageRequest(BaseModel):
    table: str  # 表名 (大小写不敏感)


class PageDepsRequest(BaseModel):
    pageRef: str  # 前端节点 id 或 name


class ApiCallersRequest(BaseModel):
    endpointRef: str  # 端点 id 或 name


class ImpactReportResponse(BaseModel):
    found: bool = False
    target: dict | None = None
    impact: dict | None = None          # {byLayer, counts, total}
    risk: str | None = None             # high / medium / low
    layersAffected: list[str] = Field(default_factory=list)
    total: int = 0
    summary: str = ""                   # 人类可读 markdown
    ambiguous: list[dict] = Field(default_factory=list)  # 名字歧义时的候选 (用 id 消歧)


class GraphQueryResponse(BaseModel):
    """table-usage / page-deps / api-callers 通用容器 (结构随查询不同, data 透传)。"""

    found: bool = False
    data: dict | None = None


# ---- MCP 调用分析 (platform-admin 仪表盘) ----

class McpChromaUsage(BaseModel):
    agentCalls: int = 0   # web 端 agent(chat)调用数
    devCalls: int = 0     # 开发端 Claude Code / Codex 直调数
    agentHits: int = 0    # agent 命中(返回 >0 结果)次数
    devHits: int = 0      # dev 命中次数


class McpCallUsage(BaseModel):
    calls: int = 0        # codegraph: 纯开发端调用(agent 不走此 MCP)


class McpModelUsage(BaseModel):
    # 自部署 Qwen embedding/reranker 推理次数, 按来源拆 (容量归因: agent 产品 vs 开发者)
    agentEmbed: int = 0
    devEmbed: int = 0
    agentRerank: int = 0
    devRerank: int = 0


class McpGraphUsage(BaseModel):
    # 统一图谱(graph)调用, 按来源分桶 (无命中率概念 —— 图查询非检索)
    agentCalls: int = 0   # web 端 agent 调用
    devCalls: int = 0     # 开发端 Claude Code / Codex 直调


class McpUsageMetrics(BaseModel):
    chroma: McpChromaUsage = Field(default_factory=McpChromaUsage)
    codegraph: McpCallUsage = Field(default_factory=McpCallUsage)
    graph: McpGraphUsage = Field(default_factory=McpGraphUsage)
    model: McpModelUsage = Field(default_factory=McpModelUsage)


class McpProjectUsage(McpUsageMetrics):
    projectId: str = ""


class McpUsageWindow(BaseModel):
    projects: list[McpProjectUsage] = Field(default_factory=list)
    total: McpUsageMetrics = Field(default_factory=McpUsageMetrics)


class McpUsageReportResponse(BaseModel):
    last7d: McpUsageWindow = Field(default_factory=McpUsageWindow)    # 近 7 天
    allTime: McpUsageWindow = Field(default_factory=McpUsageWindow)   # 全时段累计


# ---- agent token 用量(Phase 8 计量看板 + system 审计列表)----

class AgentUsageEntry(BaseModel):
    """单次 agent 查询的 token 用量明细(审计列表行)。"""

    ts: float = 0.0
    sessionId: str = ""
    model: str = ""
    steps: int = 0
    stopReason: str = ""
    inputTokens: int = 0
    outputTokens: int = 0
    cacheHitTokens: int = 0
    cacheMissTokens: int = 0
    costUsd: float = 0.0


class AgentUsageModel(BaseModel):
    """按模型聚合一行(token + 估算成本 + 缓存命中率)。"""

    model: str = ""
    queries: int = 0
    inputTokens: int = 0
    outputTokens: int = 0
    cacheHitTokens: int = 0
    cacheMissTokens: int = 0
    cacheHitRate: float = 0.0
    costUsd: float = 0.0


class AgentUsageWindow(BaseModel):
    """一个时间窗的总量 + 按模型 + 最近明细。"""

    queries: int = 0
    inputTokens: int = 0
    outputTokens: int = 0
    cacheHitTokens: int = 0
    cacheMissTokens: int = 0
    cacheHitRate: float = 0.0
    costUsd: float = 0.0
    byModel: list[AgentUsageModel] = Field(default_factory=list)
    recent: list[AgentUsageEntry] = Field(default_factory=list)


class AgentUsageReportResponse(BaseModel):
    last7d: AgentUsageWindow = Field(default_factory=AgentUsageWindow)    # 近 7 天
    allTime: AgentUsageWindow = Field(default_factory=AgentUsageWindow)   # 全时段累计
