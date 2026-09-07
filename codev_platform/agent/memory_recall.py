"""记忆冲突消解(memory M3 的确定性核心,纯逻辑)。

同一 topic_key 的多条记忆冲突时,按优先级取一条胜出,其余视为被压。
优先级可配(plan §3.5):
  - is_redline(org 硬约束)永远最高 —— 不可配(合规底线)
  - 非红线按 policy:
      "personal_first"(默认): personal > project > team > org(个人偏好盖团队默认)
      "org_first":             org > project > team > personal(组织规则盖个人)

纯函数,不依赖 DB / chroma —— policy 来源(orgs.conflict_policy / 个人覆盖)是 M5,
那时从 PG 读出 policy 字符串传进来即可,本逻辑不变。向量召回(chroma)是 M3 另一块。
"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry

# 非红线时各作用域的"基础优先级序"(数字小 = 高)。两种 policy 各一份。
_NONREDLINE_ORDER = {
    "personal_first": {"personal": 0, "project": 1, "team": 2, "org": 3},
    "org_first": {"org": 0, "project": 1, "team": 2, "personal": 3},
}
# 红线之间固定按 org 治理序裁决(org 最高),不受 policy 影响 —— plan §3.5
# "is_redline 永远最高不可配"。否则 personal_first 会让个人 redline 压过 org 合规红线。
_REDLINE_ORDER = {"org": 0, "project": 1, "team": 2, "personal": 3}
DEFAULT_POLICY = "personal_first"


def _rank(entry: MemoryEntry, policy: str) -> tuple[int, int]:
    """排序键(越小越优先)。
    第 1 维:红线 0 / 非红线 1(红线永远压非红线,不论 policy)。
    第 2 维:红线之间按 org 治理序(不可配,合规底线);非红线按 policy 的作用域序。
    """
    if entry.is_redline:
        return (0, _REDLINE_ORDER.get(entry.scope, 99))
    order = _NONREDLINE_ORDER.get(policy, _NONREDLINE_ORDER[DEFAULT_POLICY])
    return (1, order.get(entry.scope, 99))


def resolve_conflicts(entries: list[MemoryEntry], policy: str = DEFAULT_POLICY) -> list[MemoryEntry]:
    """按 topic_key 分组,同组取最高优先级的一条;无 topic_key 的条目原样保留(视为不冲突)。

    返回:去冲突后的记忆列表(保持稳定:无 topic_key 的全留,有 topic_key 的每组留 1)。
    """
    winners: dict[str, MemoryEntry] = {}   # topic_key -> 当前胜出
    passthrough: list[MemoryEntry] = []    # 无 topic_key,不参与冲突
    for e in entries:
        if not e.topic_key:
            passthrough.append(e)
            continue
        cur = winners.get(e.topic_key)
        if cur is None or _rank(e, policy) < _rank(cur, policy):
            winners[e.topic_key] = e
    return passthrough + list(winners.values())
