"""reindex worker —— 编排层。组合 JobQueue + runners registry:

  启动先 drain 一次 (catch 启动前堆积) → 之后 queue.watch() 有新活就 drain。
  drain = 串行跑完当前所有 pending (满足 SQLite 单写者); 每 job 跑完 complete()
  (dirty 则保留下轮重跑)。

只依赖 JobQueue 协议 + runners.get_runner —— 不碰存储细节 (file/PG) 也不碰命令细节 (各 runner)。
repo 路径从 config.projects.<pid>.repo_path 解析。
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

from codev_platform.core.config import get as _cfg_get
from codev_platform.reindex import runners as _runners
from codev_platform.reindex.queue import Job, JobQueue

_LOG_FILE = Path(__file__).resolve().parent / "worker.log"


def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, file=sys.stderr, flush=True)


def _repo_for(cfg: dict, project_id: str) -> Path | None:
    projects = _cfg_get(cfg, "projects") or {}
    pc = projects.get(project_id)
    if not isinstance(pc, dict):
        return None
    repo = pc.get("repo_path")
    if not repo:
        return None
    p = Path(repo).expanduser()
    return p if p.exists() else None


class ReindexWorker:
    """单 worker, 串行消费队列。多机时仍单 worker 实例 (写串行); 扩展靠换 JobQueue 实现。"""

    def __init__(self, queue: JobQueue, cfg: dict) -> None:
        self._q = queue
        self._cfg = cfg

    def drain_once(self) -> int:
        """跑完当前所有 pending (串行)。返回处理 job 数。"""
        n = 0
        for job in self._q.pending():
            self._run_job(job)
            n += 1
        return n

    def _run_job(self, job: Job) -> None:
        runner = _runners.get_runner(job.kind)
        if runner is None:
            _log(f"未知 kind '{job.kind}' ({job.key}) — 丢弃")
            self._q.complete(job)
            return
        repo = _repo_for(self._cfg, job.project_id)
        if repo is None:
            _log(f"project '{job.project_id}' 无 repo_path 或不存在 — 丢弃 {job.key}")
            self._q.complete(job)
            return
        _log(f"reindex 开始 {job.key} (repo={repo})")
        try:
            rc = runner.run(job.project_id, repo, self._cfg)
        except Exception as exc:  # noqa: BLE001 — 单 job 失败不拖垮 worker
            _log(f"reindex 异常 {job.key}: {exc!s} — 丢弃避免死循环")
            self._q.complete(job)
            return
        dirty = not self._q.complete(job)
        _log(f"reindex 完成 {job.key} rc={rc}" + (" (运行期又有新触发, 已重排)" if dirty else ""))

    async def run_forever(self) -> None:
        _log(f"worker 启动, 监视队列 ({type(self._q).__name__})")
        self.drain_once()
        async for _ in self._q.watch():
            self.drain_once()
