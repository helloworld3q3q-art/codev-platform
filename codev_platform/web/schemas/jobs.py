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


class IndexStatusItem(BaseModel):
    """单类索引的新鲜度 (来自统一 IndexManifest, Phase 1)。"""

    kind: str | None = Field(None, description="索引类型 chroma/codegraph/graph/docs")
    status: str | None = Field(None, description="上次构建状态 ok/failed")
    gitCommit: str | None = Field(None, description="构建时仓库 HEAD commit")
    fresh: bool | None = Field(None, description="是否对齐当前 HEAD: true 对齐/false 落后/null 未知")
    reason: str | None = Field(None, description="新鲜度判定说明")
    finishedAt: float | None = Field(None, description="上次构建完成时间 epoch 秒")
    elapsedSec: float | None = Field(None, description="上次构建耗时秒")


class IndexStatusResponse(BaseModel):
    """GET 索引状态: 各类索引相对当前 HEAD 的新鲜度 (Phase 1 freshness)。"""

    headCommit: str | None = Field(None, description="项目仓库当前 HEAD commit")
    items: list[IndexStatusItem] = Field(default_factory=list, description="各类索引新鲜度")
