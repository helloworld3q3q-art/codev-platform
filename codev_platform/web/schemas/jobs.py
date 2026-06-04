"""Jobs / Indexes 请求 + 响应模型 (plan §十五)。

字段对齐 plan §七 统一命名 (camelCase, createdAt/updatedAt)。JobDTO 是 Job 领域对象的
对外投影 (status 值 = JobStatusEnum)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from codev_platform.web.domain.job import Job


class JobDTO(BaseModel):
    """Job 对外视图 (plan §七 字段规范)。"""

    jobId: str = Field(..., description="任务 ID")
    projectId: str = Field(..., description="项目 ID")
    jobType: str = Field(..., description="任务类型, 如 index_rebuild:chroma")
    status: str = Field(..., description="任务状态 (JobStatusEnum)")
    error: str | None = Field(None, description="失败原因 (仅 Failed 时)")
    createdAt: float = Field(..., description="创建时间 (epoch 秒)")
    updatedAt: float = Field(..., description="更新时间 (epoch 秒)")

    @classmethod
    def of(cls, job: Job) -> JobDTO:
        return cls(
            jobId=job.job_id,
            projectId=job.project_id,
            jobType=job.job_type,
            status=job.status,
            error=job.error,
            createdAt=job.created_at,
            updatedAt=job.updated_at,
        )


class JobIdData(BaseModel):
    """提交类接口返回体: 只回 jobId (不阻塞, 前端凭此轮询 detail)。"""

    jobId: str = Field(..., description="新建任务 ID")


class IndexRebuildRequest(BaseModel):
    """POST /api/v1/indexes/rebuild 请求体。"""

    indexKind: str | None = Field(
        None, description="索引类型: all / chroma / codegraph; 不传默认 all"
    )


class JobCancelRequest(BaseModel):
    """POST /api/v1/jobs/cancel 请求体。"""

    jobId: str = Field(..., min_length=1, description="待取消任务 ID")
