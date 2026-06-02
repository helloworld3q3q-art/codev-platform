"""进程内项目级互斥锁注册表 (plan §十二 + §12.1 写侧串行)。

同一 (project_id, job_type) 默认互斥: 防同项目重复重建索引互相覆盖
(codegraph/chroma 状态写坏)。本片是进程内 in-memory 注册表 —— 单进程够用;
跨进程/多副本升级见 plan §十二二阶段 (Redis lock) 或复用 core.spawn_lock 文件锁。

真正的重活仍提交给平台既有写锁通道 (reindex worker 串行), 本注册表只挡
"同进程同项目同类型 job 并发提交"。锁随 job 生命周期持有: acquire 在提交时,
release 在 job 进入终态时。
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
