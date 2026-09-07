"""reindex worker —— 编排层。组合 JobQueue + runners registry:

  启动先 drain 一次 (catch 启动前堆积) → 之后 queue.watch() 有新活就 drain。
  drain = 串行跑完当前所有 pending (满足 SQLite 单写者); 每 job 跑完 complete()
  (dirty 则保留下轮重跑)。

只依赖 JobQueue 协议 + runners.get_runner —— 不碰存储细节 (file/PG) 也不碰命令细节 (各 runner)。
repo 路径从 config.projects.<pid>.repo_path 解析。
"""
from __future__ import annotations

import datetime
import inspect
import json
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from codev_platform.core.config import get as _cfg_get
from codev_platform.core.runtime_artifact_io import append_runtime_artifact_text
from codev_platform.reindex import runners as _runners
from codev_platform.reindex.attempt_inputs import (
    PrepareResult,
    PreparedRepo,
    _repo_for,
    prepare_project_repos,
)
from codev_platform.reindex.queue import FileSpoolQueue, Job, JobQueue

_RETRY_RC = 2          # reindex 返回 2 = 锁占用 / db busy (暂时性) → 保留重试, 不丢
_PERIODIC_SEC = 60.0   # 周期兜底再 drain (重试 rc=2 的 job + 补漏 watch 事件)
_RUNNER_RENEW_SEC = 30.0
REINDEX_DEPENDENCIES = {
    "ingest": ("codegraph",),
    "code_vec": ("codegraph",),
}

__all__ = [
    "PrepareResult",
    "PreparedRepo",
    "ReindexWorker",
    "_repo_for",
    "prepare_project_repos",
]

def _log_path() -> Path:
    # 落 data_root/logs (非 import 包目录: wheel/只读安装也可写, 见 core.paths.logs_dir)
    from codev_platform.core.paths import logs_dir
    return logs_dir() / "worker.log"


def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    append_runtime_artifact_text(_log_path(), line + "\n")
    print(line, file=sys.stderr, flush=True)


def _compact_note(note: str, limit: int = 500) -> str:
    text = " | ".join(line.strip() for line in note.splitlines() if line.strip())
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


class ReindexWorker:
    """单 worker, 串行消费队列。多机时仍单 worker 实例 (写串行); 扩展靠换 JobQueue 实现。"""

    def __init__(self, queue: JobQueue, cfg: dict,
                 on_heartbeat: Callable[[], None] | None = None,
                 on_job_event: Callable[[object | None, str], None] | None = None) -> None:
        self._q = queue
        self._cfg = cfg
        self._on_heartbeat = on_heartbeat
        self._on_job_event = on_job_event
        self._prepare_cache: dict[tuple[str, str, str], PrepareResult] = {}
        self._drain_results: dict[tuple[str, str, str], str] = {}
        self._deferred_retry_releases: list[Job] | None = None

    def _heartbeat(self) -> None:
        if self._on_heartbeat is None:
            return
        try:
            self._on_heartbeat()
        except Exception:  # noqa: BLE001 - lifecycle telemetry must not break work
            pass

    def _job_event(self, job: object | None, status: str) -> None:
        if self._on_job_event is None:
            return
        try:
            self._on_job_event(job, status)
        except Exception:  # noqa: BLE001 - lifecycle telemetry must not break work
            pass

    def _own_projects(self) -> set[str] | None:
        """本 worker 能处理的 project 集合 = config.projects 里配了(存在的)repo_path 的 project。

        多机/多 org 亲和: PG 后端传给 pending() 让 worker 只认领本机 project,
        不抢别机/别 org 的 job
        (否则认领后因本地无 repo_path 而 complete() 删掉它 = 吃掉别人的 reindex)。

        file 后端是单机本地 spool, 不该因为用户级 config.projects 缺本仓而孤儿化本仓 hook
        任务, 故始终返 None = 认领全部; 真正能否处理由 _repo_for(config/meta) 决定。

        PG 后端(多机/多 org)必须 fail-closed: 漏配 projects 时返空集, 宁可可见不工作,
        也不静默认领/删除别 org/别机 job。"""
        if isinstance(self._q, FileSpoolQueue):
            return None
        projects = _cfg_get(self._cfg, "projects")
        if isinstance(projects, dict) and projects:
            return {pid for pid in projects if _repo_for(self._cfg, pid) is not None}
        _log("WARN: PG 队列后端但未配 config.projects 白名单 → 为防认领/删除别 org/别机 job, "
             "本 worker 暂不认领任何 job; 请配 config.projects.<pid>.repo_path")
        return set()

    def drain_once(self) -> int:
        """跑完当前所有 pending (串行)。返回处理 job 数。

        跑完后按 project 刷一次 ai-health 快照 (与旧 hook 行为对齐: reindex 后快照新鲜)。
        """
        n = 0
        touched: dict[str, Path] = {}
        self._prepare_cache = {}
        self._drain_results = {}
        self._heartbeat()
        self._job_event(None, "queue_scan")
        projects = self._own_projects()
        if self._queue_supports_pending_limit():
            deferred_releases: list[Job] = []
            self._deferred_retry_releases = deferred_releases
            try:
                while True:
                    claimed = list(self._q.pending(projects, limit=1))
                    if not claimed:
                        break
                    for job in claimed:
                        repo = self._run_job(job)
                        if repo is not None:
                            touched[job.project_id] = repo
                        n += 1
                        self._heartbeat()
            finally:
                self._deferred_retry_releases = None
                self._flush_retry_releases(deferred_releases)
        else:
            for job in self._q.pending(projects):
                repo = self._run_job(job)
                if repo is not None:
                    touched[job.project_id] = repo
                n += 1
                self._heartbeat()
        for pid, repo in touched.items():
            self._job_event(None, "health_refresh")
            try:
                health_ok = self._refresh_health(pid, repo)
            except Exception:  # noqa: BLE001 - health telemetry must not break queue progress
                health_ok = False
            if not health_ok:
                self._job_event(None, "health_refresh_failed")
        self._heartbeat()
        self._job_event(None, "idle")
        return n

    def _release_for_retry(self, job: Job) -> None:
        if self._deferred_retry_releases is not None:
            self._deferred_retry_releases.append(job)
            return
        release = getattr(self._q, "release", None)
        if not callable(release):
            return
        try:
            release(job)
        except Exception as exc:  # noqa: BLE001 — 复位失败不阻断(最坏退化到等 lease 过期)
            _log(f"release {job.key} 失败 (不阻断, 退化到等 lease 过期): {exc!s}")

    def _flush_retry_releases(self, jobs: list[Job]) -> None:
        seen: set[str] = set()
        for job in jobs:
            if job.key in seen:
                continue
            seen.add(job.key)
            self._release_for_retry(job)

    def _queue_supports_pending_limit(self) -> bool:
        pending = getattr(self._q, "pending", None)
        if not callable(pending):
            return False
        try:
            params = inspect.signature(pending).parameters
        except (TypeError, ValueError):
            return False
        return "limit" in params

    def _start_lease_renewer(self, job: Job) -> tuple[threading.Event | None, threading.Thread | None]:
        renew = getattr(self._q, "renew", None)
        if not callable(renew) or not job.token:
            return None, None
        stop_event = threading.Event()

        def _loop() -> None:
            while not stop_event.wait(_RUNNER_RENEW_SEC):
                try:
                    renewed = bool(renew(job))
                except Exception as exc:  # noqa: BLE001 - 续租失败只记日志
                    _log(f"renew {job.key} 异常 (停止续租, 不中断 runner): {exc!s}")
                    return
                if not renewed:
                    _log(f"renew {job.key} 未命中 active lease (停止续租, 不中断 runner)")
                    return

        thread = threading.Thread(target=_loop, name=f"reindex-renew-{job.key}", daemon=True)
        thread.start()
        return stop_event, thread

    @staticmethod
    def _stop_lease_renewer(stop_event: threading.Event | None,
                            thread: threading.Thread | None) -> None:
        if stop_event is None or thread is None:
            return
        stop_event.set()
        thread.join(timeout=_RUNNER_RENEW_SEC + 1.0)

    def _prepare_once(self, job: Job, repo: Path) -> PrepareResult:
        key = (
            str(repo.resolve()),
            str(job.meta.target_commit or ""),
            str(job.meta.pull_policy or "never"),
        )
        cached = self._prepare_cache.get(key)
        if cached is not None:
            return cached
        prepared = prepare_project_repos(job, repo, self._cfg)
        self._prepare_cache[key] = prepared
        return prepared

    @staticmethod
    def _target_commit(job: Job, repo: Path) -> str:
        from codev_platform.index_manifest import git_head

        target = str(job.meta.target_commit or "").strip()
        if target and target != "HEAD":
            return target
        return str(git_head(repo) or "").strip()

    def _record_drain_result(self, project_id: str, kind: str, target_commit: str, status: str) -> None:
        self._drain_results[(project_id, kind, target_commit)] = status

    def _same_drain_dependency(self, project_id: str, dependency: str, target_commit: str) -> str | None:
        return self._drain_results.get((project_id, dependency, target_commit))

    @staticmethod
    def _manifest_dependency_state(project_id: str, dependency: str,
                                   target_commit: str, repo: Path) -> str:
        from codev_platform.index_manifest import _commit_covers, latest_build

        rec = latest_build(project_id, dependency)
        if rec is None:
            return "missing"
        if rec.status == "failed":
            return "failed"
        if rec.status != "ok":
            return "stale"
        if not target_commit:
            return "stale"
        if _commit_covers(repo, target_commit, rec.git_commit):
            return "ok"
        return "stale"

    def _dependency_blocker(self, job: Job, repo: Path, target_commit: str) -> tuple[str, str] | None:
        dependencies = REINDEX_DEPENDENCIES.get(job.kind, ())
        for dependency in dependencies:
            same_drain = self._same_drain_dependency(job.project_id, dependency, target_commit)
            if same_drain == "ok":
                continue
            if same_drain == "retry":
                return "retry", dependency
            if same_drain == "failed":
                return "failed", dependency
            state = self._manifest_dependency_state(job.project_id, dependency, target_commit, repo)
            if state == "ok":
                continue
            if state == "failed":
                return "failed", dependency
            return "retry", dependency
        return None

    def _complete_blocked_dependency(self, job: Job, repo: Path, started: float,
                                     dependency: str, target_commit: str) -> None:
        note = f"blocked by dependency: {dependency} failed"
        self._record_drain_result(job.project_id, job.kind, target_commit, "failed")
        self._job_event(job, "manifest")
        manifest_ok = self._record_manifest(job, repo, started, "failed", note=note)
        if not manifest_ok:
            self._job_event(job, "manifest_failed")
        self._job_event(job, "queue_complete")
        self._q.complete(job)
        self._job_event({
            "job_key": job.key,
            "project_id": job.project_id,
            "kind": job.kind,
            "status": "blocked",
            "note": note,
            "manifest_ok": manifest_ok,
        }, "result")

    def _run_job(self, job: Job) -> Path | None:
        """跑一个 job; 返回 repo (供 drain 收集刷 health), 跳过/丢弃返回 None。"""
        runner = _runners.get_runner(job.kind)
        if runner is None:
            _log(f"未知 kind '{job.kind}' ({job.key}) — 丢弃")
            self._job_event(job, "queue_complete")
            self._q.complete(job)
            self._job_event({
                "job_key": job.key,
                "project_id": job.project_id,
                "kind": job.kind,
                "status": "discarded",
                "note": "discarded_unknown",
            }, "result")
            return None
        repo = _repo_for(self._cfg, job.project_id)
        if repo is None:
            _log(f"project '{job.project_id}' 无 repo_path 或不存在 — 丢弃 {job.key}")
            self._job_event(job, "queue_complete")
            self._q.complete(job)
            self._job_event({
                "job_key": job.key,
                "project_id": job.project_id,
                "kind": job.kind,
                "status": "discarded",
                "note": "discarded_no_repo",
            }, "result")
            return None
        self._job_event(job, "git_sync")
        prepared = self._prepare_once(job, repo)
        for state in prepared.repos:
            pulled = "n/a" if state.pulled is None else str(state.pulled)
            head = state.head or "unknown"
            _log(f"reindex prepare {job.key}: repo={state.root} head={head} pulled={pulled} ({state.note})")
        if not prepared.can_run:
            target_commit = self._target_commit(job, repo)
            _log(f"reindex prepare 阻止运行 {job.key}: {prepared.note}")
            if prepared.retryable:
                self._record_drain_result(job.project_id, job.kind, target_commit, "retry")
                self._job_event(job, "queue_complete")
                self._release_for_retry(job)
                self._job_event({
                    "job_key": job.key,
                    "project_id": job.project_id,
                    "kind": job.kind,
                    "status": "retry",
                    "note": prepared.note,
                }, "result")
                return None
            self._record_drain_result(job.project_id, job.kind, target_commit, "failed")
            self._job_event(job, "queue_complete")
            self._q.complete(job)
            self._job_event({
                "job_key": job.key,
                "project_id": job.project_id,
                "kind": job.kind,
                "status": "discarded",
                "note": "discarded_prepare",
                "detail": prepared.note,
            }, "result")
            return None
        target_commit = self._target_commit(job, repo)
        blocker = self._dependency_blocker(job, repo, target_commit)
        if blocker is not None:
            status, dependency = blocker
            if status == "retry":
                _log(f"reindex 依赖未就绪 {job.key}: waiting for {dependency}@{target_commit or 'unknown'}")
                self._record_drain_result(job.project_id, job.kind, target_commit, "retry")
                self._job_event(job, "queue_complete")
                self._release_for_retry(job)
                self._job_event({
                    "job_key": job.key,
                    "project_id": job.project_id,
                    "kind": job.kind,
                    "status": "retry",
                    "note": f"waiting for dependency: {dependency}",
                }, "result")
                return None
            _log(f"reindex 依赖失败阻塞 {job.key}: blocked by {dependency}")
            self._complete_blocked_dependency(job, repo, time.time(), dependency, target_commit)
            return None
        _log(f"reindex 开始 {job.key} (repo={repo})")
        self._job_event(job, "runner")
        started = time.time()
        renew_stop, renew_thread = self._start_lease_renewer(job)
        try:
            rc = runner.run(job.project_id, repo, self._cfg)
        except Exception as exc:  # noqa: BLE001 — 单 job 失败不拖垮 worker
            self._stop_lease_renewer(renew_stop, renew_thread)
            _log(f"reindex 异常 {job.key}: {exc!s} — 丢弃避免死循环")
            note = str(exc)[:200]
            self._job_event(job, "manifest")
            manifest_ok = self._record_manifest(job, repo, started, "failed", note=note)
            if not manifest_ok:
                self._job_event(job, "manifest_failed")
            self._job_event(job, "queue_complete")
            self._q.complete(job)
            self._job_event({
                "job_key": job.key,
                "project_id": job.project_id,
                "kind": job.kind,
                "status": "exception",
                "note": note,
                "manifest_ok": manifest_ok,
            }, "result")
            return None
        self._stop_lease_renewer(renew_stop, renew_thread)
        # rc==2 = .reindex.lock 被占 / db busy (暂时性, 与 codegraph sync rc=2 同约定):
        # 不 complete, 保留 job 下轮重试 (不丢这次 reindex)。常态下 worker 是唯一写者,
        # 锁不会被占; 此路径仅兜底"误手动 reindex 撞 worker"的罕见并发。不记 manifest (非终态)。
        if rc == _RETRY_RC:
            _log(f"reindex {job.key} 锁占用/db busy (rc=2) — 保留重试, 不丢")
            self._record_drain_result(job.project_id, job.kind, target_commit, "retry")
            # 认领式队列后端若不显式 release, job 可能要等 lease 过期才会再次可见, 破坏"下轮重试"。
            # FileSpool v2 / PG 都可实现 release; 这里保留 getattr 守卫, 只要求遵守 JobQueue 协议。
            self._job_event(job, "queue_complete")
            self._release_for_retry(job)
            self._job_event({
                "job_key": job.key,
                "project_id": job.project_id,
                "kind": job.kind,
                "status": "retry",
                "note": "runner rc=2",
            }, "result")
            return None
        # 终态 (rc==0 成功 / 其它 rc 失败): 写统一 manifest (Phase 1, best-effort 不阻断)。
        runner_note = str(getattr(runner, "last_note", "") or "")
        final_status = "ok" if rc == 0 else "failed"
        self._record_drain_result(job.project_id, job.kind, target_commit, final_status)
        self._job_event(job, "manifest")
        manifest_ok = self._record_manifest(job, repo, started, final_status, note=runner_note)
        if not manifest_ok:
            self._job_event(job, "manifest_failed")
        # 其它 rc!=0 = 真失败: complete 丢弃避免死循环 (错误已在 reindex 日志, ai-health 可见)
        self._job_event(job, "queue_complete")
        dirty = not self._q.complete(job)
        self._job_event({
            "job_key": job.key,
            "project_id": job.project_id,
            "kind": job.kind,
            "status": final_status,
            "detail": runner_note or None,
            "manifest_ok": manifest_ok,
        }, "result")
        if rc != 0:
            detail = f" detail={_compact_note(runner_note)}" if runner_note else ""
            _log(f"reindex 失败 {job.key} rc={rc}{detail} — 丢弃避免死循环")
            return None
        _log(f"reindex 完成 {job.key} rc=0" + (" (运行期又有新触发, 已重排)" if dirty else ""))
        return repo

    def _record_manifest(self, job: Job, repo: Path, started: float,
                         status: str, note: str = "") -> bool:
        """写统一索引 manifest (Phase 1)。best-effort: 任何异常静默, 绝不阻断索引主流程。"""
        try:
            from codev_platform.index_manifest import BuildRecord, git_head, latest_build, record_build
            head = git_head(repo)
            target_commit = str(job.meta.target_commit or "").strip() or head
            repo_commits = {"main": head} if head else {}
            depends: list[dict[str, str | None]] = []
            if job.kind == "code_vec":
                dep = latest_build(job.project_id, "codegraph")
                depends.append({
                    "kind": "codegraph",
                    "status": dep.status if dep is not None else "missing",
                    "git_commit": dep.git_commit if dep is not None else None,
                    "target_commit": (
                        dep.target_commit if dep is not None and dep.target_commit else target_commit
                    ),
                })
            record_build(BuildRecord(
                project_id=job.project_id, kind=job.kind,
                git_commit=head, target_commit=target_commit,
                source=job.meta.source, pull_policy=job.meta.pull_policy,
                repo_commits_json=json.dumps(repo_commits, ensure_ascii=True, separators=(",", ":"))
                if repo_commits else None,
                depends_json=json.dumps(depends, ensure_ascii=True, separators=(",", ":"))
                if depends else None,
                started_at=started, finished_at=time.time(), status=status, note=note,
            ))
            return True
        except Exception as exc:  # noqa: BLE001
            _log(f"manifest 记录失败 {job.key} (不阻断): {exc!s}")
            return False

    def _refresh_health(self, project_id: str, repo: Path) -> bool:
        """best-effort: reindex 后刷该 project 的 ai-health light 快照 (失败静默)。"""
        import subprocess
        from codev_platform.core.process_tree import run_tree
        from codev_platform.mcp_serve import _platform_runtime_python
        try:
            completed = run_tree(
                [str(_platform_runtime_python()), "-I", "-m", "codev_platform.cli", "health",
                 "--mode", "light", "--repo", str(repo), "--json-out"],
                timeout=120,
                stdin=subprocess.DEVNULL,
                no_window=True,
            )
            return completed.returncode == 0
        except Exception:  # noqa: BLE001
            return False

    def _reclaim_stale_own(self) -> None:
        """崩溃恢复: 启动时把上一进程(本 worker 崩前)认领后卡在 running 的自己的行复位 pending,
        自己重启即接管, 不必干等 lease(1800s)过期。是否支持该恢复动作由具体队列实现决定,
        所以这里用 getattr 守卫; best-effort, 失败不阻断启动。"""
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

    async def run_until_idle(self, idle_exit_sec: float,
                             heartbeat_sec: float = 10.0) -> None:
        import asyncio
        idle_exit_sec = max(float(idle_exit_sec), 0.1)
        heartbeat_sec = max(float(heartbeat_sec), 0.1)
        _log(f"worker 启动, 短驻模式 idle_exit={int(idle_exit_sec)}s ({type(self._q).__name__})")
        self._reclaim_stale_own()
        idle_since = time.monotonic()
        while True:
            processed = self.drain_once()
            now = time.monotonic()
            if processed:
                idle_since = now
            remaining = idle_exit_sec - (now - idle_since)
            if remaining <= 0:
                _log(f"worker idle {int(idle_exit_sec)}s, 退出")
                return
            await asyncio.sleep(min(heartbeat_sec, remaining))
