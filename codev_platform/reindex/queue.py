"""reindex 任务队列 —— 持久层 (协议 + file spool 实现)。

JobQueue 协议把"存什么 / 怎么取 / 怎么等新活"抽象掉; worker 只依赖协议, 以后多机共享
换 PgQueue / RedisQueue 不动 worker / runners。FileSpoolQueue 是单机默认实现:

  <spool_dir>/<project_id>__<kind>   标记文件, mtime = 入队时刻

- 同 (project, kind) 复用一个文件 → 天然合并 (密集触发只跑一次最新态)
- 落盘持久 → worker 重启不丢
- complete() 用 mtime 判 "运行期是否被重新触发" (dirty 重入: 跑中来的新提交不漏)

本层不 import runners / worker (单向依赖, 解耦)。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from collections.abc import AsyncIterator

_SEP = "__"


@dataclass(frozen=True)
class Job:
    """一个 reindex 任务。enqueued_at = 认领时观察到的入队时刻 (mtime), 供 complete 判 dirty。"""
    project_id: str
    kind: str
    enqueued_at: float

    @property
    def key(self) -> str:
        return f"{self.project_id}{_SEP}{self.kind}"


@runtime_checkable
class JobQueue(Protocol):
    """队列抽象。生产者只调 enqueue; worker 调 pending / complete / watch。"""

    def enqueue(self, project_id: str, kind: str) -> None:
        """登记一个 (project, kind) reindex 需求 (幂等合并同 key)。"""
        ...

    def pending(self) -> list[Job]:
        """当前待办 (已按 key 合并)。"""
        ...

    def complete(self, job: Job) -> bool:
        """标记完成。返回 True=已移除; False=运行期被重新触发 (dirty), 已保留待重跑。"""
        ...

    def watch(self) -> AsyncIterator[None]:
        """async 迭代器: 队列可能有新活时 yield 一次 (storage 自决怎么等)。"""
        ...


class FileSpoolQueue:
    """标记文件实现 (单机默认)。spool_dir 下每 (project, kind) 一个文件。"""

    def __init__(self, spool_dir: Path) -> None:
        self._dir = Path(spool_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def location(self) -> Path:
        return self._dir

    @staticmethod
    def _validate(project_id: str, kind: str) -> tuple[str, str]:
        """底层兜底校验: project_id 强制 slug, kind 限定已知集合。

        防上层入口出脏数据 / 路径穿越 (project_id 含 / 或 .. → 写到 spool 外)。
        runners import 放函数内, 避免 import 环 (runners → mcp_serve → ...)。
        非法: project_id 抛 ProjectIdError, kind 抛 ValueError。
        """
        from codev_platform.core.project_id import validate as _validate_pid

        pid = _validate_pid(project_id)

        from codev_platform.reindex.runners import kinds as _kinds

        known = _kinds()
        if known and kind not in known:
            raise ValueError(
                f"未知 reindex kind: {kind!r} (允许: {sorted(known)})"
            )
        # known 为空 (runner 未注册 / 极端情况) 时仍兜底挡路径穿越
        if any(sep in str(kind) for sep in ("/", "\\", "..", _SEP)):
            raise ValueError(f"非法 reindex kind: {kind!r} (含路径分隔符)")
        return pid, kind

    def _path(self, project_id: str, kind: str) -> Path:
        pid, kind = self._validate(project_id, kind)
        return self._dir / f"{pid}{_SEP}{kind}"

    def enqueue(self, project_id: str, kind: str) -> None:
        # touch: 不存在则建, 存在则刷新 mtime (= 重新触发, 供 dirty 重入)
        self._path(project_id, kind).touch()

    def pending(self) -> list[Job]:
        jobs: list[Job] = []
        if not self._dir.is_dir():
            return jobs
        for f in sorted(self._dir.iterdir()):
            if not f.is_file() or _SEP not in f.name:
                continue
            pid, _, kind = f.name.partition(_SEP)
            if pid and kind:
                jobs.append(Job(pid, kind, f.stat().st_mtime))
        return jobs

    def complete(self, job: Job) -> bool:
        # 不重做 runner 白名单校验 —— job 已从 spool 读出, worker 丢弃未知 kind 时也要能删掉它,
        # 不能因 _path→_validate 的白名单 ValueError 崩 drain(codex P2 #4: 旧 spool / 坏文件)。
        # 仍挡路径穿越(防坏 kind 删到 spool 外)。
        pid, kind = str(job.project_id), str(job.kind)
        if any(sep in pid or sep in kind for sep in ("/", "\\", "..")):
            return True  # 非法路径片段(不该入 spool): 当已完成, 不删不崩
        p = self._dir / f"{pid}{_SEP}{kind}"
        if not p.exists():
            return True
        # 运行期被重新 touch (mtime 比认领时新) → 保留, 下轮重跑 (dirty, 不丢尾部提交)
        if p.stat().st_mtime > job.enqueued_at:
            return False
        p.unlink(missing_ok=True)
        return True

    async def watch(self) -> AsyncIterator[None]:
        from watchfiles import awatch
        async for _changes in awatch(str(self._dir)):
            yield None
