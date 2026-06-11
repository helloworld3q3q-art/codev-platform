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
    """一个 reindex 任务。enqueued_at = 认领时观察到的入队时刻 (mtime), 供 complete 判 dirty。

    token: 本次认领的唯一戳(PgJobQueue 跨机租约用 —— complete 按 token 精确匹配自己那次认领,
    防 lease 接管后旧 worker 误删新 worker 在跑的行)。FileSpoolQueue 单机无需, 留 None。
    """
    project_id: str
    kind: str
    enqueued_at: float
    token: str | None = None

    @property
    def key(self) -> str:
        return f"{self.project_id}{_SEP}{self.kind}"


@runtime_checkable
class JobQueue(Protocol):
    """队列抽象。生产者只调 enqueue; worker 调 pending / complete / watch。"""

    def enqueue(self, project_id: str, kind: str) -> None:
        """登记一个 (project, kind) reindex 需求 (幂等合并同 key)。"""
        ...

    def pending(self, projects: set[str] | None = None) -> list[Job]:
        """认领并返回待办 (worker 专用)。**可能有副作用**: PG 实现会原子认领 + 打租约,
        故只 worker 调; 仅"查看"用 peek()。

        projects: None=认领全部 (兼容现状); 传集合=只认领其中 project 的 job
        (多机/多 org 亲和: worker 不抢自己 config 无 repo_path 的别机 project, 见 worker.drain_once)。"""
        ...

    def peek(self) -> list[Job]:
        """只读列出待办 (无副作用, 不认领)。status / 诊断用, 绝不锁 job。"""
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

    def pending(self, projects: set[str] | None = None) -> list[Job]:
        # projects: None=全部 (单机默认无需过滤); 传集合则只返回其中 project (保 Protocol 一致,
        # 单机 file 后端正常不传, 行为不变)。
        # 排序 = (mtime_ns, kind 依赖序, name)。**不能用文件名做 tie-break**: dispatch/webhook 在同一
        # loop 里连续 touch codegraph/ingest/code_vec, 在 ext4(reindex worker 实际运行处)三者 mtime
        # **完全相同**, 而 'code_vec' < 'codegraph'(_ 0x5F < g 0x67)字母序会让 code_vec 先于 codegraph
        # 跑 → code_vec 读陈旧 codegraph.db, vector 索引滞后一个 commit(2026-06-11 全流程审计实证 ~99%)。
        # tie-break 改 **runner 注册序 = 依赖序**(codegraph 先于其下游 ingest/code_vec), 数据驱动。
        from codev_platform.reindex.runners import kinds as _kinds
        rank = {k: i for i, k in enumerate(_kinds())}
        unknown = len(rank)   # 未注册 kind 排最后
        jobs: list[Job] = []
        if not self._dir.is_dir():
            return jobs
        entries = [f for f in self._dir.iterdir() if f.is_file() and _SEP in f.name]

        def _key(f: Path):
            kind = f.name.partition(_SEP)[2]
            return (f.stat().st_mtime_ns, rank.get(kind, unknown), f.name)

        for f in sorted(entries, key=_key):
            pid, _, kind = f.name.partition(_SEP)
            if pid and kind and (projects is None or pid in projects):
                jobs.append(Job(pid, kind, f.stat().st_mtime))
        return jobs

    def peek(self) -> list[Job]:
        """只读列出待办。FileSpool 的 pending() 本就无副作用(只列目录), 故 peek == pending。"""
        return self.pending()

    def complete(self, job: Job) -> bool:
        # 不重做 runner 白名单校验 —— job 已从 spool 读出, worker 丢弃未知 kind 时也要能删掉它,
        # 不能因 _path→_validate 的白名单 ValueError 崩 drain(坏 kind / 旧 spool)。
        pid, kind = str(job.project_id), str(job.kind)
        p = self._dir / f"{pid}{_SEP}{kind}"
        # 路径穿越防御 + **坏文件能删**(安全审计 P2#4): resolved 仍在 spool 目录内才碰它。
        # literal '..' 名的坏 spool 文件(pending 读得到、原逻辑只 return True 不删 → 永久重处理)
        # 其 resolved 仍在 spool 内 → 现在会被删; 真正穿出 spool 的(synthetic 坏 kind)才放过。
        try:
            in_spool = p.resolve().parent == self._dir.resolve()
        except (OSError, ValueError):
            in_spool = False
        if not in_spool:
            return True  # 无法安全定位(路径穿越)→ 当已完成, 不删不崩(极罕见)
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
