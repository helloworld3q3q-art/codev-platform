"""记忆生命周期维护(memory M4):TTL 自动归档 + 压缩摘要融合。

定位:周期 job(手动 / cron / Task Scheduler 调 `scripts/run_memory_maintenance.py`)。
- **TTL 归档**:`archive_expired` 把过期(ttl_at 已过)的 active 记忆置 archived(留痕,不物删)。
  注:`list_scope` 已防御性排除过期记忆,故召回正确性不依赖本 job 及时跑;本 job 只做"落地清理"。
- **压缩融合**:同 (scope, scope_ref, topic_key) 的多条 active 记忆 → LLM 融合成 1 条 summary,
  原条 archived(可回溯,plan 难点 #2)。redline 逐条保留不参与融合(组织硬约束不可被稀释)。

LLM 融合走**注入式 `fuse_fn`**(`list[str] -> str`):编排逻辑(取组/写 summary/归档原条)与 LLM
调用解耦 —— 编排可用 fake fuse_fn 单测,真 LLM 是 `make_llm_fuse(provider)` 薄适配器。
"""
from __future__ import annotations

from typing import Callable

from codev_platform.agent.memory_store import MemoryEntry, MemoryStore

FuseFn = Callable[[list[str]], str]


class MemoryMaintenance:
    """记忆维护编排。依赖 MemoryStore 抽象,不含 LLM / DB 细节。"""

    def __init__(self, store: MemoryStore, *, min_entries: int = 3) -> None:
        self._store = store
        self._min = min_entries  # 一个 topic 攒到这么多条才触发压缩(少了不值得融合)

    def archive_expired(self, org_id: str | None = None) -> int:
        """TTL 到期批量归档,返回归档条数。"""
        return self._store.archive_expired(org_id)

    def compress_topic(self, scope: str, scope_ref: str, topic_key: str, fuse_fn: FuseFn,
                       org_id: str = "default", min_entries: int | None = None) -> str | None:
        """融合某 (scope, scope_ref, topic_key) 的多条 active 记忆为 1 条 summary。

        不足阈值 / 融合空串 → 返回 None 不动数据。redline 不参与融合(逐条保留)。
        成功:写 summary(kind='summary',extra.fused_from=原 id 列表)+ 原条 archived,返回新 id。
        """
        min_n = min_entries if min_entries is not None else self._min
        entries = [e for e in self._store.list_scope(scope, scope_ref, org_id=org_id, limit=500)
                   if e.topic_key == topic_key and not e.is_redline]
        if len(entries) < min_n:
            return None
        fused = fuse_fn([e.content for e in entries])
        if not fused or not fused.strip():
            return None
        summary = MemoryEntry(
            id="", scope=scope, scope_ref=scope_ref, owner_user_id=entries[0].owner_user_id,
            content=fused.strip(), org_id=org_id, kind="summary", topic_key=topic_key,
            extra={"fused_from": [e.id for e in entries]},
        )
        new_id = self._store.write(summary)
        for e in entries:
            self._store.archive(e.id)
        return new_id

    def compress_scope(self, scope: str, scope_ref: str, fuse_fn: FuseFn,
                       org_id: str = "default", min_entries: int | None = None) -> dict[str, str]:
        """对某作用域下每个有多条记忆的 topic 逐组压缩。返回 {topic_key: 新 summary id}。"""
        entries = self._store.list_scope(scope, scope_ref, org_id=org_id, limit=500)
        topics = {e.topic_key for e in entries if e.topic_key and not e.is_redline}
        out: dict[str, str] = {}
        for tk in topics:
            nid = self.compress_topic(scope, scope_ref, tk, fuse_fn, org_id=org_id, min_entries=min_entries)
            if nid:
                out[tk] = nid
        return out


def make_llm_fuse(provider) -> FuseFn:
    """把 LLMProvider 包成 fuse_fn(list[str]->str)。真 LLM 融合的薄适配器。"""
    from codev_platform.agent.brain import Message
    from codev_platform.agent.prompts import MEMORY_FUSION_SYSTEM

    def fuse(contents: list[str]) -> str:
        body = "\n".join(f"- {c}" for c in contents)
        turn = provider.chat(MEMORY_FUSION_SYSTEM, [Message(role="user", content=body)], [])
        return (turn.text or "").strip()

    return fuse
