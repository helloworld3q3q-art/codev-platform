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
import time
from pathlib import Path

from codev_platform.core.config import get as _cfg_get
from codev_platform.reindex import runners as _runners
from codev_platform.reindex.queue import Job, JobQueue

_RETRY_RC = 2          # reindex 返回 2 = 锁占用 / db busy (暂时性) → 保留重试, 不丢
_PERIODIC_SEC = 60.0   # 周期兜底再 drain (重试 rc=2 的 job + 补漏 watch 事件)


def _log_path() -> Path:
    # 落 data_root/logs (非 import 包目录: wheel/只读安装也可写, 见 core.paths.logs_dir)
    from codev_platform.core.paths import logs_dir
    return logs_dir() / "worker.log"


def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with _log_path().open("a", encoding="utf-8") as f:
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

    def _own_projects(self) -> set[str] | None:
        """本 worker 能处理的 project 集合 = config.projects 里配了(存在的)repo_path 的 project。

        多机/多 org 亲和: 传给 pending() 让 PG 后端只认领本机 project, 不抢别机/别 org 的 job
        (否则认领后因本地无 repo_path 而 complete() 删掉它 = 吃掉别人的 reindex)。

        **未配 projects 段时按后端 fail-open / fail-closed 分流**(审计 risk #3):
        - file 后端(单机)→ 返 None = 认领全部(单机常态, 唯一 worker, 安全)。
        - PG 后端(多机/多 org)→ 返 **空集 = 不认领任何 job** + WARN。fail-closed: 多机下漏配
          projects 若退回"认领全部", 会吃掉别 org/别机 job(本 feature 要修的原始 bug)。宁可这台
          worker 暂不干活(可见告警)也不静默删别人的 reindex。"""
        projects = _cfg_get(self._cfg, "projects")
        if isinstance(projects, dict) and projects:
            return {pid for pid in projects if _repo_for(self._cfg, pid) is not None}
        # 无 projects 配置: PG 后端(有 reclaim_stale_own = 多机语境)fail-closed, file 后端 None=全部。
        if hasattr(self._q, "reclaim_stale_own"):
            _log("WARN: PG 队列后端但未配 config.projects 白名单 → 为防认领/删除别 org/别机 job, "
                 "本 worker 暂不认领任何 job; 请配 config.projects.<pid>.repo_path")
            return set()
        return None

    def drain_once(self) -> int:
        """跑完当前所有 pending (串行)。返回处理 job 数。

        跑完后按 project 刷一次 ai-health 快照 (与旧 hook 行为对齐: reindex 后快照新鲜)。
        """
        n = 0
        touched: dict[str, Path] = {}
        for job in self._q.pending(self._own_projects()):
            repo = self._run_job(job)
            if repo is not None:
                touched[job.project_id] = repo
            n += 1
        for pid, repo in touched.items():
            self._refresh_health(pid, repo)
        return n

    def _run_job(self, job: Job) -> Path | None:
        """跑一个 job; 返回 repo (供 drain 收集刷 health), 跳过/丢弃返回 None。"""
        runner = _runners.get_runner(job.kind)
        if runner is None:
            _log(f"未知 kind '{job.kind}' ({job.key}) — 丢弃")
            self._q.complete(job)
            return None
        repo = _repo_for(self._cfg, job.project_id)
        if repo is None:
            _log(f"project '{job.project_id}' 无 repo_path 或不存在 — 丢弃 {job.key}")
            self._q.complete(job)
            return None
        # 索引前先把工作树追到远端 (webhook 模型下服务器 clone 常落后于 push):
        # ff-only, 降级安全, 失败不阻断 —— pulled=False 照常索引当前工作树。
        from codev_platform.reindex.git_sync import sync_repo_to_remote
        sync = sync_repo_to_remote(repo)
        _log(f"reindex git-sync {job.key}: pulled={sync['pulled']} ({sync['note']})")
        _log(f"reindex 开始 {job.key} (repo={repo})")
        started = time.time()
        try:
            rc = runner.run(job.project_id, repo, self._cfg)
        except Exception as exc:  # noqa: BLE001 — 单 job 失败不拖垮 worker
            _log(f"reindex 异常 {job.key}: {exc!s} — 丢弃避免死循环")
            self._record_manifest(job, repo, started, "failed", note=str(exc)[:200])
            self._q.complete(job)
            return None
        # rc==2 = .reindex.lock 被占 / db busy (暂时性, 与 codegraph sync rc=2 同约定):
        # 不 complete, 保留 job 下轮重试 (不丢这次 reindex)。常态下 worker 是唯一写者,
        # 锁不会被占; 此路径仅兜底"误手动 reindex 撞 worker"的罕见并发。不记 manifest (非终态)。
        if rc == _RETRY_RC:
            _log(f"reindex {job.key} 锁占用/db busy (rc=2) — 保留重试, 不丢")
            # PG 后端: pending() 认领即打 1800s lease, 不复位则 job 隐身到 lease 过期才重领(破坏
            # "下轮重试")。显式 release 立即复位 pending → 下轮 drain 即重领。file 后端无 claim 状态
            # (留着 spool 文件下轮自然重列), 无 release 方法 → getattr 守卫跳过, 语义不变。
            release = getattr(self._q, "release", None)
            if callable(release):
                try:
                    release(job)
                except Exception as exc:  # noqa: BLE001 — 复位失败不阻断(最坏退化到等 lease 过期)
                    _log(f"release {job.key} 失败 (不阻断, 退化到等 lease 过期): {exc!s}")
            return None
        # 终态 (rc==0 成功 / 其它 rc 失败): 写统一 manifest (Phase 1, best-effort 不阻断)。
        self._record_manifest(job, repo, started, "ok" if rc == 0 else "failed")
        # 其它 rc!=0 = 真失败: complete 丢弃避免死循环 (错误已在 reindex 日志, ai-health 可见)
        dirty = not self._q.complete(job)
        if rc != 0:
            _log(f"reindex 失败 {job.key} rc={rc} — 丢弃避免死循环 (查 reindex.log)")
            return None
        _log(f"reindex 完成 {job.key} rc=0" + (" (运行期又有新触发, 已重排)" if dirty else ""))
        return repo

    def _record_manifest(self, job: Job, repo: Path, started: float,
                         status: str, note: str = "") -> None:
        """写统一索引 manifest (Phase 1)。best-effort: 任何异常静默, 绝不阻断索引主流程。"""
        try:
            from codev_platform.index_manifest import BuildRecord, git_head, record_build
            record_build(BuildRecord(
                project_id=job.project_id, kind=job.kind,
                git_commit=git_head(repo), started_at=started,
                finished_at=time.time(), status=status, note=note,
            ))
        except Exception as exc:  # noqa: BLE001
            _log(f"manifest 记录失败 {job.key} (不阻断): {exc!s}")

    def _refresh_health(self, project_id: str, repo: Path) -> None:
        """best-effort: reindex 后刷该 project 的 ai-health light 快照 (失败静默)。"""
        import subprocess
        from codev_platform.mcp_serve import _venv_python
        try:
            subprocess.run(
                [str(_venv_python(self._cfg)), "-m", "codev_platform.cli", "health",
                 "--mode", "light", "--repo", str(repo), "--json-out"],
                timeout=120,
            )
        except Exception:  # noqa: BLE001
            pass

    def _reclaim_stale_own(self) -> None:
        """崩溃恢复: 启动时把上一进程(本 worker 崩前)认领后卡在 running 的自己的行复位 pending,
        自己重启即接管, 不必干等 lease(1800s)过期。仅 PG 后端有此方法(file 后端无 lease 概念,
        重启即重列, 无需复位)→ hasattr 守卫, 不动 file 语义。best-effort, 失败不阻断启动。"""
        reclaim = getattr(self._q, "reclaim_stale_own", None)
        if not callable(reclaim):
            return
        try:
            n = reclaim()
            if n:
                _log(f"崩溃恢复: 复位 {n} 个本 worker 上次崩前卡 running 的 job → pending")
        except Exception as exc:  # noqa: BLE001 — 复位失败不阻断启动(最坏退化到等 lease 过期)
            _log(f"reclaim_stale_own 失败 (不阻断, 退化到等 lease 过期): {exc!s}")

    async def run_forever(self) -> None:
        import asyncio
        _log(f"worker 启动, 监视队列 ({type(self._q).__name__})")
        self._reclaim_stale_own()
        self.drain_once()

        async def _periodic() -> None:
            # 周期兜底: 重试 rc=2 保留的 job + 补漏 watch 事件 (drain_once 同步阻塞,
            # 与 watch 触发的 drain 天然串行, 单线程不会重入)。
            while True:
                await asyncio.sleep(_PERIODIC_SEC)
                self.drain_once()

        asyncio.create_task(_periodic())
        async for _ in self._q.watch():
            self.drain_once()
