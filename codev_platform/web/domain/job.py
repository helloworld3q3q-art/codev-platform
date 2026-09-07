"""Job 领域模型 + 状态机 (plan §十二)。

一个 Job = 一次长任务 (本片只有 index rebuild)。状态机:

    Pending ──start──> Running ──> Succeeded
       │                  │   └────> Failed
       └──cancel──┐       └──cancel──> Cancelled
                  └──────────────────> Cancelled

值对齐 web.domain.enums.JobStatusEnum (单一真值源, 前端 useModel('enum') 复用)。
终态 (Succeeded/Failed/Cancelled) 不可再流转; cancel 只对 Pending/Running 生效。
本模块纯领域 (无 IO / 无 FastAPI), repo 负责持久, service 负责编排。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace

from codev_platform.web.domain.enums import JobStatusEnum

# 终态集合: 不可再流转。
_TERMINAL = frozenset(
    {JobStatusEnum.SUCCEEDED.value, JobStatusEnum.FAILED.value, JobStatusEnum.CANCELLED.value}
)
# 可取消的活跃态。
_CANCELLABLE = frozenset({JobStatusEnum.PENDING.value, JobStatusEnum.RUNNING.value})


def new_job_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class Job:
    """不可变 job 快照。状态流转返回新实例 (repo 用它替换存储里的旧值)。"""

    job_id: str
    project_id: str
    job_type: str
    status: str = JobStatusEnum.PENDING.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    @property
    def is_cancellable(self) -> bool:
        return self.status in _CANCELLABLE

    def _to(self, status: str, *, error: str | None = None) -> Job:
        return replace(self, status=status, updated_at=time.time(), error=error)

    def to_running(self) -> Job:
        return self._to(JobStatusEnum.RUNNING.value)

    def to_succeeded(self) -> Job:
        return self._to(JobStatusEnum.SUCCEEDED.value)

    def to_failed(self, error: str) -> Job:
        return self._to(JobStatusEnum.FAILED.value, error=error)

    def to_cancelled(self) -> Job:
        return self._to(JobStatusEnum.CANCELLED.value)


def create_job(project_id: str, job_type: str) -> Job:
    """新建 Pending job。"""
    return Job(job_id=new_job_id(), project_id=project_id, job_type=job_type)
