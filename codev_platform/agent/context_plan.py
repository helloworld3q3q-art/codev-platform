"""Context Engineering(M2):召回记忆 → 分组 + budget 裁剪 → ContextPlan(可调试)。

把 RecallService 召回的扁平 MemoryEntry list 按层分组(redline / task / project /
personal / org),按组优先级做 budget 裁剪(超量丢低优先级组的尾部),产出 ContextPlan ——
既供 prompt 分组格式化, 又留可调试记录(每组几条 / 裁了几条 / 为什么)。

设计:
- **纯函数 + 数据类**, 不碰 DB / LLM / prompt 文案, 只做"召回结果 → 结构化计划"的变换,
  便于单测与复用(同 recall_service 的接缝思路)。
- **不依赖向量**:RRF / 语义重排是 M3 vector backend 的事;本模块只按已知优先级 + 召回序
  组织, 记忆量大到需语义排序时, 召回侧换 VectorRecallService 即可, 本模块零改。
- 分组优先级(高→低):redline(组织硬约束必须看到)> task(当前任务上下文)> project >
  personal > org。budget 从低优先级尾部裁剪, redline 永不裁。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from codev_platform.agent.memory_store import MemoryEntry

# 分组顺序 = 优先级(高→低)。budget 裁剪从尾部(低优先级)起, redline 永不裁。
GROUP_ORDER: tuple[str, ...] = ("redline", "task", "project", "personal", "org")
_DEFAULT_BUDGET = 8


def _group_of(e: MemoryEntry, task_id: str | None) -> str:
    """单条记忆归到哪个分组(redline 最高, 其次当前 task, 再按 scope)。"""
    if e.is_redline:
        return "redline"
    if task_id and getattr(e, "task_id", None) == task_id:
        return "task"
    if e.scope in ("project", "personal", "org"):
        return e.scope
    return "org"  # team / 未知归 org(组织级兜底)


@dataclass
class ContextPlan:
    """分组 + budget 裁剪后的注入计划(供 prompt 格式化 + 调试)。"""
    groups: dict[str, list[MemoryEntry]] = field(default_factory=dict)  # group -> 保留的条目(有序)
    total_recalled: int = 0      # 召回总数(裁剪前)
    kept: int = 0                # 裁剪后保留数
    dropped: int = 0             # 被 budget 裁掉的数
    budget_max: int = _DEFAULT_BUDGET

    def is_empty(self) -> bool:
        return self.kept == 0

    def iter_kept(self):
        """按组优先级顺序产出保留的条目(redline → task → ... )。"""
        for g in GROUP_ORDER:
            yield from self.groups.get(g, ())

    def explain(self) -> str:
        """可调试:为什么 prompt 里是这些记忆(每组几条 + 裁剪情况)。"""
        parts = [f"{g}={len(self.groups[g])}" for g in GROUP_ORDER if self.groups.get(g)]
        tail = f"; dropped={self.dropped}(budget={self.budget_max})" if self.dropped else ""
        return f"context_plan: recalled={self.total_recalled} kept={self.kept} [{', '.join(parts)}]{tail}"


def build_context_plan(memories: list[MemoryEntry], *, task_id: str | None = None,
                       budget_max: int = _DEFAULT_BUDGET) -> ContextPlan:
    """召回的 MemoryEntry list → 分组 + budget 裁剪 → ContextPlan。

    - 分组:按 _group_of(redline/task/project/personal/org), 组内保持召回序(已含 recall 的
      redline>task>query 排序)。
    - budget:保留总数 ≤ budget_max。从低优先级组的尾部裁剪;**redline 永不裁**(组织硬约束
      必须让模型看到, 即便它本身就超 budget 也全留)。
    """
    total = len(memories)
    grouped: dict[str, list[MemoryEntry]] = {g: [] for g in GROUP_ORDER}
    for e in memories:
        grouped[_group_of(e, task_id)].append(e)

    # budget 裁剪:redline 全留(组织硬约束, 即使超 budget), 其余按优先级高→低填到 budget_max。
    kept_groups: dict[str, list[MemoryEntry]] = {}
    redlines = grouped.get("redline", [])
    if redlines:
        kept_groups["redline"] = list(redlines)
    remaining = max(0, budget_max - len(redlines))  # redline 占满后非红线名额可能为 0
    for g in GROUP_ORDER:
        if g == "redline":
            continue
        items = grouped.get(g, [])
        if not items or remaining <= 0:
            continue
        take = items[:remaining]
        kept_groups[g] = take
        remaining -= len(take)

    kept = sum(len(v) for v in kept_groups.values())
    return ContextPlan(
        groups=kept_groups, total_recalled=total, kept=kept,
        dropped=total - kept, budget_max=budget_max,
    )
