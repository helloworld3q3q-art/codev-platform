"""ReindexWorker 多 org 亲和 + 崩溃恢复接线(无需 PG)。

- _own_projects(): 只圈出 config.projects 里配了存在 repo_path 的 project(传给 pending 做白名单)。
- drain_once(): 把白名单传给 queue.pending(projects=...)。
- _reclaim_stale_own(): 仅当 queue 有 reclaim_stale_own 方法时调(file 后端无 → 跳过, 不崩)。
"""
from __future__ import annotations

import asyncio
import time

import codev_platform.core.repos as repos
from codev_platform.reindex.queue import FileSpoolQueue, Job
from codev_platform.reindex import worker as worker_mod
from codev_platform.reindex.worker import ReindexWorker, _repo_for


def test_own_projects_none_when_no_projects_cfg(tmp_path):
    # config 无 projects 段 + file 后端(无 reclaim_stale_own) → None(认领全部, 单机 file 常态)。
    w = ReindexWorker(FileSpoolQueue(tmp_path), {})
    assert w._own_projects() is None


def test_own_projects_failclosed_on_pg_backend_without_cfg():
    # 审计 risk #3: PG 后端(多机)+ 无 projects 配置 → 不认领任何 job(空集 fail-closed),
    # 而非 None=认领全部(否则吃别 org/别机 job)。PG 信号 = 队列有 reclaim_stale_own 方法。
    class _PgLikeQueue:
        def reclaim_stale_own(self):   # 有此方法 = PG 后端语境
            return 0
    w = ReindexWorker(_PgLikeQueue(), {})        # 无 projects 配置
    assert w._own_projects() == set()            # fail-closed: 不认领, 不退回"全部"


def test_pg_own_projects_only_those_with_existing_repo(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    cfg = {"projects": {
        "has-repo": {"repo_path": str(repo)},      # 有且存在 → 入集合
        "no-path": {"some": "x"},                  # 无 repo_path → 排除
        "missing": {"repo_path": str(tmp_path / "nope")},  # 路径不存在 → 排除
    }}
    class _PgLikeQueue:
        def pending(self, projects=None):
            return []

    w = ReindexWorker(_PgLikeQueue(), cfg)
    assert w._own_projects() == {"has-repo"}


def test_file_queue_does_not_orphan_jobs_when_projects_cfg_omits_repo(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    cfg = {"projects": {"other": {"repo_path": str(repo)}}}

    w = ReindexWorker(FileSpoolQueue(tmp_path), cfg)

    assert w._own_projects() is None


def test_repo_for_falls_back_to_project_repo_specs(monkeypatch, tmp_path):
    repo = tmp_path / "meta-repo"
    repo.mkdir()
    spec = repos.RepoSpec(root=repo, tag="", is_main=True, source_project_id="demo")
    monkeypatch.setattr(repos, "project_repo_specs", lambda project_id, cfg=None: [spec])

    assert _repo_for({"projects": {"other": {"repo_path": str(tmp_path)}}}, "demo") == repo.resolve()


def test_drain_once_passes_whitelist_to_pending(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    cfg = {"projects": {"keep": {"repo_path": str(repo)}}}

    seen = {}

    class _SpyQueue:
        def pending(self, projects=None):
            seen["projects"] = projects
            return []
        def complete(self, job):  # pragma: no cover - 不触发(pending 返空)
            return True

    w = ReindexWorker(_SpyQueue(), cfg)
    w.drain_once()
    assert seen["projects"] == {"keep"}


def test_reclaim_stale_own_skipped_for_file_backend(tmp_path):
    # file 后端无 reclaim_stale_own 方法 → _reclaim_stale_own 安全跳过, 不抛。
    w = ReindexWorker(FileSpoolQueue(tmp_path), {})
    w._reclaim_stale_own()   # 不应抛异常


def test_reclaim_stale_own_called_when_present(tmp_path):
    calls = {"n": 0}

    class _Q:
        def reclaim_stale_own(self):
            calls["n"] += 1
            return 2
        def pending(self, projects=None):
            return []

    w = ReindexWorker(_Q(), {})
    w._reclaim_stale_own()
    assert calls["n"] == 1


def test_reclaim_stale_own_swallows_errors(tmp_path):
    class _Q:
        def reclaim_stale_own(self):
            raise RuntimeError("db down")

    w = ReindexWorker(_Q(), {})
    w._reclaim_stale_own()   # 失败不阻断启动


def test_run_until_idle_exits_after_empty_queue(tmp_path):
    class _Q:
        def pending(self, projects=None):
            return []

    started = time.monotonic()
    asyncio.run(ReindexWorker(_Q(), {}).run_until_idle(0.1, 0.1))
    assert time.monotonic() - started < 1.0


def test_run_job_records_runner_failure_note(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "chroma", time.time())
    completed: list[str] = []
    records: list[object] = []

    class _Q:
        def complete(self, done):
            completed.append(done.key)
            return True

    class _Runner:
        last_note = "chroma rc=1\nFAIL: chroma reindex exit=1"

        def run(self, project_id, repo_path, cfg):
            return 1

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr(
        "codev_platform.reindex.git_sync.sync_repo_to_remote",
        lambda repo_path: {"pulled": True, "note": "ok"},
    )
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc")
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: records.append(rec))

    assert ReindexWorker(_Q(), {})._run_job(job) is None

    assert completed == ["demo__chroma"]
    assert records
    assert records[0].status == "failed"
    assert "FAIL: chroma reindex exit=1" in records[0].note
