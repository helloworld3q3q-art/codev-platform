"""进程内项目级互斥锁注册表 (plan §十二 + §12.1 写侧串行)。

同一 (project_id, job_type) 在**提交瞬间**互斥: 防同一刻并发提交。本片是进程内
in-memory 注册表。

**正确性边界 (2026-06-08 审计澄清)**: 本锁**不是**"防重复重建"的正确性屏障 —— 真正的
不重复跑由下游三层保证: FileSpoolQueue 同 key(`project__kind`)天然合并 + ReindexWorker
单 worker 串行消费 + runner 自带 `.reindex.lock`。本锁只做**提交期早拒**(UX: 同一瞬间双击
不各建一个 job)。锁在 `submit` 成功完成时即释放(不跨 submit 持有) —— 因为 reindex worker
是独立进程、不回写 job 终态, 若跨 submit 持有, 成功路径无终态回调会让锁永久悬挂(审计事实A:
同 project 同 job_type 第二次永久 RATE_LIMITED, 须 cancel/重启才解)。

**多实例 (>1 web worker)**: 本进程内锁失效, 但因正确性靠下游三层(非本锁), 真实后果只是
"两用户各拿一个 jobId 而非一个 RATE_LIMITED"(UX 瑕疵, 非索引损坏)。**故不做 PG 锁化**(YAGNI;
若将来确需严格单活跃 job 语义, 健壮方案需 PG locks 表 + 条件 upsert 抢占 + TTL/job 状态对账
sweeper, 见审计讨论)。
"""
from __future__ import annotations

import threading


class ProjectLockRegistry:
    """(project_id, job_type) → 占用标志。线程安全 (FastAPI 多线程/asyncio 混跑兜底)。"""

    def __init__(self) -> None:
        self._held: set[tuple[str, str]] = set()
        self._guard = threading.Lock()

    def _key(self, project_id: str, job_type: str) -> tuple[str, str]:
        return (project_id, job_type)

    def try_acquire(self, project_id: str, job_type: str) -> bool:
        """尝试占用。返回 True=拿到锁; False=已被占 (调用方应拒绝并报互斥)。"""
        key = self._key(project_id, job_type)
        with self._guard:
            if key in self._held:
                return False
            self._held.add(key)
            return True

    def is_held(self, project_id: str, job_type: str) -> bool:
        with self._guard:
            return self._key(project_id, job_type) in self._held

    def release(self, project_id: str, job_type: str) -> None:
        """释放 (幂等: 未持有也不报错)。"""
        with self._guard:
            self._held.discard(self._key(project_id, job_type))


# 模块级单例 (进程内全局, service 共用)。
registry = ProjectLockRegistry()
