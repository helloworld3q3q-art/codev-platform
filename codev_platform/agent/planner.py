"""QueryPlanner — 按问题类型生成短计划(query_type + 工具预算 + 优先工具 + 停止条件).

roadmap-2026-06-07 Phase 7 最小版。Web agent 此前只靠 loop guard **反应式**防空转(thrash
后才拦);planner 是**前摄式**:动手前按问题类型给一个计划 —— 该走哪几类工具、最多调几次、
满足什么条件就收尾。让"先规划再执行"从模型自由发挥变成可控、可观测、可评测。

**确定性优先**(对齐 .claude/rules/agent-provider §4 + a2 设计哲学):分类用关键词打分规则,
**不调 LLM**。分类对不对可用 golden set 回归(eval `planner` suite),据此再决定 prompt/预算调参。
LLM planner 是后续可选增强,不是 MVP —— MVP 先证明确定性分类够用。

集成在 loop.py(planner_enabled 开关, 默认关, 不破存量行为):计划注入 system prompt 作引导
+ 工具预算作**软**停止条件(达预算回灌"收尾"提示, 非硬断)。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# 问题类型(闭集; general = 兜底, 不施加额外约束)。
class QueryType:
    OVERVIEW = "overview"        # 项目概览: 这项目干啥 / 整体架构
    IMPACT = "impact"            # 修改面分析: 改 X 影响谁 / 跨层链路 / 谁用了某表/端点
    SYMBOL = "symbol"            # 符号链路: 某函数/类在哪、谁调它、它调谁
    DOC_RULE = "doc_rule"        # 规则/文档: 该遵守什么规则 / 为什么这么设计 / 操作手册
    GENERAL = "general"          # 兜底: 无明显类型, 不额外约束


# 每类的关键词表(命中即加分; 中英混合, 覆盖中文问法)。词表是**数据**, 改词不改代码。
_KEYWORDS: dict[str, tuple[str, ...]] = {
    QueryType.OVERVIEW: (
        "概览", "总览", "整体", "干啥", "干什么", "是什么", "做什么", "用来做",
        "介绍", "项目结构", "目录结构", "技术栈", "有什么用", "这个项目", "这个仓",
        "overview", "what does this", "what is this", "purpose of", "high level", "high-level",
    ),
    QueryType.IMPACT: (
        "影响", "波及", "牵连", "牵一发", "改动", "改了", "要改", "改哪", "改什么",
        "依赖谁", "谁依赖", "谁用了", "谁调用", "被谁", "调用方", "消费方", "影响面",
        "前后端", "跨层", "链路", "改这张表", "改这个表", "改接口", "改端点",
        # 使用方/消费方查询(table_usage / api_callers / page_dependencies lane 的自然问法)
        "被哪些", "哪些函数", "哪些端点", "哪些接口", "哪些页面", "哪些前端",
        "读写", "读取", "写入", "消费", "使用了",
        "impact", "affect", "blast radius", "who uses", "who calls", "depend on", "break",
    ),
    QueryType.SYMBOL: (
        "函数", "方法", "类", "定义", "签名", "实现", "在哪", "在哪里", "哪个文件",
        "调用了谁", "调了谁", "内部调用", "callees", "callers",
        "where is", "definition", "signature", "implemented", "calls what", "called by",
    ),
    QueryType.DOC_RULE: (
        "规则", "约定", "为什么", "为啥", "设计", "原因", "文档", "事故", "复盘",
        "操作", "手册", "怎么做", "如何", "应该遵守", "最佳实践", "规范",
        "rule", "why", "design", "convention", "should i", "how to", "best practice", "guideline",
    ),
}

# 类型 → 工具预算(工具调用次数上限; clamp 到 max_steps)。源自 plan §407-411 的**任务复杂度**区间,
# 是**跨模型共享基线**(按问题类型该花多少工具来定, 不按某一个模型的 A/B 数字 overfit)。
# 某模型若需更松/更紧, 走 per-model 覆盖(未来可加 LoopPolicy budget scale), 不改这里的基线。
_BUDGET: dict[str, int] = {
    QueryType.OVERVIEW: 5,
    QueryType.IMPACT: 8,    # plan §409 "修改面分析 8-12 次"取下界基线(原 12 偏松, 链路覆盖到即收尾)
    QueryType.SYMBOL: 6,
    QueryType.DOC_RULE: 4,
    QueryType.GENERAL: 0,   # 0 = 不额外约束(loop 会用 max_steps 兜)
}

# 类型 → 优先工具(lane 提示; 注入前会按实际可用工具过滤)。
_LANES: dict[str, tuple[str, ...]] = {
    QueryType.OVERVIEW: ("list_dir", "search_docs", "read_file"),
    QueryType.IMPACT: ("impact_analysis", "table_usage", "api_callers",
                       "page_dependencies", "codegraph_callers"),
    QueryType.SYMBOL: ("codegraph_search", "codegraph_callers", "codegraph_callees", "read_file"),
    QueryType.DOC_RULE: ("search_docs", "read_file"),
    QueryType.GENERAL: (),
}

# 类型 → 停止条件文案(满足即收尾, 防为单点查到耗尽预算)。
_STOP_HINT: dict[str, str] = {
    QueryType.OVERVIEW: "拿到项目用途 + 主要模块/目录结构即可收尾, 不必逐文件细读。",
    QueryType.IMPACT: "正向(它依赖什么)与反向(谁依赖它)链路都覆盖到、关键受影响层已列出即收尾。",
    QueryType.SYMBOL: "定位到定义 + 调用方/被调用方, 必要时读一处实现即收尾, 不要反复换词重搜。",
    QueryType.DOC_RULE: "search_docs 命中相关规则/设计后读原文佐证即收尾, 一般 2-4 次工具足够。",
    QueryType.GENERAL: "拿到足够证据即给最终答案, 不做无谓多轮。",
}

# 类型优先级(打分平手时取靠前者; 更具体的类型优先于宽泛的)。
_PRIORITY: tuple[str, ...] = (
    QueryType.IMPACT, QueryType.SYMBOL, QueryType.DOC_RULE, QueryType.OVERVIEW,
)


@dataclass
class QueryPlan:
    query_type: str
    tool_budget: int                       # clamp 后的工具调用预算 (== max_steps 表示不额外约束)
    preferred_lanes: list[str] = field(default_factory=list)  # 过滤到可用工具后的优先工具
    stop_hint: str = ""
    matched: dict[str, int] = field(default_factory=dict)     # 各类型命中关键词数(可观测/调试)


def classify_query(question: str) -> str:
    """关键词打分分类。命中数最高者胜, 平手按 _PRIORITY, 零命中 → general。"""
    q = (question or "").lower()
    scores = {
        qt: sum(1 for kw in kws if kw.lower() in q)
        for qt, kws in _KEYWORDS.items()
    }
    best = max(scores.values()) if scores else 0
    if best == 0:
        return QueryType.GENERAL
    # 平手取优先级靠前者
    for qt in _PRIORITY:
        if scores.get(qt, 0) == best:
            return qt
    return QueryType.GENERAL


def _query_scores(question: str) -> dict[str, int]:
    q = (question or "").lower()
    return {qt: sum(1 for kw in kws if kw.lower() in q) for qt, kws in _KEYWORDS.items()}


def plan_query(question: str, *, max_steps: int, available_tools: list[str] | None = None) -> QueryPlan:
    """生成 QueryPlan。budget clamp 到 max_steps; lanes 过滤到实际可用工具。

    general / budget 0 → tool_budget = max_steps(等价不额外约束, 软停止不会先于 max_steps 触发)。
    """
    qt = classify_query(question)
    avail = set(available_tools or [])
    raw_budget = _BUDGET.get(qt, 0)
    budget = max_steps if raw_budget <= 0 else min(raw_budget, max_steps)
    lanes = [t for t in _LANES.get(qt, ()) if not avail or t in avail]
    return QueryPlan(
        query_type=qt,
        tool_budget=budget,
        preferred_lanes=lanes,
        stop_hint=_STOP_HINT.get(qt, ""),
        matched=_query_scores(question),
    )


def render_plan_preamble(plan: QueryPlan) -> str:
    """把计划渲染成注入 system prompt 的引导段(简洁; 不喧宾夺主)。"""
    lines = [
        "【查询计划(本轮按此规划工具, 前摄式)】",
        f"- 问题类型: {plan.query_type}",
    ]
    if plan.preferred_lanes:
        lines.append(f"- 优先工具: {', '.join(plan.preferred_lanes)}(先从这几类入手, 再按需扩展)")
    lines.append(f"- 工具预算: 约 {plan.tool_budget} 次工具调用为宜, 超出请基于现有证据收尾。")
    if plan.stop_hint:
        lines.append(f"- 停止条件: {plan.stop_hint}")
    lines.append("计划是引导不是枷锁: 证据够了就提前收尾; 确有必要可超预算, 但要说明残余不确定。")
    return "\n".join(lines)
