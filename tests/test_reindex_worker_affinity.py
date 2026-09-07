"""ReindexWorker 多 org 亲和 + 崩溃恢复接线(无需 PG)。

- _own_projects(): 只圈出 config.projects 里配了存在 repo_path 的 project(传给 pending 做白名单)。
- drain_once(): 把白名单传给 queue.pending(projects=...)。
- _reclaim_stale_own(): 仅当 queue 有 reclaim_stale_own 方法时调(file 后端无 → 跳过, 不崩)。
"""
from __future__ import annotations

import asyncio
import subprocess
import time

import codev_platform.core.repos as repos
from codev_platform.reindex import supervisor
from codev_platform.reindex.queue import FileSpoolQueue, Job, JobMeta
from codev_platform.reindex import worker as worker_mod
from codev_platform.reindex.worker import ReindexWorker, _repo_for
from codev_platform.index_manifest import BuildRecord


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


def test_drain_once_claims_limit_one_when_queue_supports_it(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    jobs = [
        Job("demo", "codegraph", 1.0, token="cg"),
        Job("demo", "ingest", 2.0, token="ig"),
    ]
    seen_limits: list[tuple[set[str] | None, int | None]] = []
    completed: list[str] = []
    runner_calls: list[str] = []

    class _Q:
        def __init__(self):
            self._jobs = list(jobs)

        def pending(self, projects=None, limit=None):
            seen_limits.append((projects, limit))
            if not self._jobs:
                return []
            batch = self._jobs[:limit] if limit is not None else list(self._jobs)
            self._jobs = self._jobs[len(batch):]
            return batch

        def complete(self, done):
            completed.append(done.key)
            return True

    class _Runner:
        last_note = ""

        def __init__(self, kind):
            self._kind = kind

        def run(self, project_id, repo_path, cfg):
            runner_calls.append(self._kind)
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner(kind))
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: True)

    assert worker.drain_once() == 2
    assert runner_calls == ["codegraph", "ingest"]
    assert completed == ["demo__codegraph", "demo__ingest"]
    assert [limit for _projects, limit in seen_limits] == [1, 1, 1]


def test_runner_execution_renews_lease_and_stops_after_finish(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "chroma", time.time(), token="claim-1")
    renew_calls: list[float] = []
    completed: list[str] = []

    class _Q:
        def complete(self, done):
            completed.append(done.key)
            return True

        def renew(self, done):
            renew_calls.append(time.monotonic())
            return True

    class _Runner:
        last_note = ""

        def run(self, project_id, repo_path, cfg):
            time.sleep(0.12)
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr(worker_mod, "_RUNNER_RENEW_SEC", 0.02, raising=False)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    worker = ReindexWorker(_Q(), {})

    assert worker._run_job(job) == repo
    assert completed == ["demo__chroma"]
    assert renew_calls
    calls_after_finish = len(renew_calls)
    time.sleep(0.08)
    assert len(renew_calls) == calls_after_finish


def test_reclaim_stale_own_restores_file_backend_pending(tmp_path):
    q = FileSpoolQueue(tmp_path, owner_token="worker-1")
    q.enqueue("demo-proj", "chroma")
    assert q.pending()

    w = ReindexWorker(q, {})
    w._reclaim_stale_own()

    assert [job.key for job in q.peek()] == ["demo-proj__chroma"]


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


def test_run_job_pull_policy_never_skips_sync(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "chroma", time.time(), meta=JobMeta(source="post-commit", pull_policy="never"))
    sync_calls: list[object] = []
    runner_calls: list[tuple[str, object]] = []

    class _Q:
        def complete(self, done):
            return True

    class _Runner:
        last_note = ""

        def run(self, project_id, repo_path, cfg):
            runner_calls.append((project_id, repo_path))
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr(
        "codev_platform.reindex.git_sync.sync_repo_to_remote",
        lambda repo_path: sync_calls.append(repo_path) or {"pulled": True, "note": "unexpected"},
    )
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc")
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    assert ReindexWorker(_Q(), {})._run_job(job) == repo
    assert runner_calls == [("demo", repo)]
    assert sync_calls == []


def test_run_job_ff_only_target_commit_missing_retries_without_runner(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job(
        "demo",
        "codegraph",
        time.time(),
        token="claim-1",
        meta=JobMeta(source="webhook", pull_policy="ff_only", target_commit="deadbeef"),
    )
    released: list[str] = []
    completed: list[str] = []
    runner_calls: list[tuple[str, object]] = []

    class _Q:
        def complete(self, done):
            completed.append(done.key)
            return True

        def release(self, done):
            released.append(done.key)
            return True

    class _Runner:
        last_note = ""

        def run(self, project_id, repo_path, cfg):
            runner_calls.append((project_id, repo_path))
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr(
        "codev_platform.reindex.git_sync.sync_repo_to_remote",
        lambda repo_path: {"pulled": True, "note": "ok"},
    )
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit",
                        lambda repo_path, commit, timeout_sec=10: False, raising=False)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    assert ReindexWorker(_Q(), {})._run_job(job) is None
    assert released == ["demo__codegraph"]
    assert completed == []
    assert runner_calls == []


def test_drain_once_reuses_prepare_for_same_repo_triplet(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    jobs = [
        Job("demo", "codegraph", 1.0, meta=JobMeta(source="webhook", pull_policy="ff_only", target_commit="abc")),
        Job("demo", "ingest", 2.0, meta=JobMeta(source="webhook", pull_policy="ff_only", target_commit="abc")),
        Job("demo", "code_vec", 3.0, meta=JobMeta(source="webhook", pull_policy="ff_only", target_commit="abc")),
    ]
    sync_calls: list[object] = []
    runner_calls: list[str] = []

    class _Q:
        def pending(self, projects=None):
            return list(jobs)

        def complete(self, done):
            return True

    class _Runner:
        last_note = ""

        def __init__(self, kind):
            self._kind = kind

        def run(self, project_id, repo_path, cfg):
            runner_calls.append(self._kind)
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner(kind))
    monkeypatch.setattr(
        "codev_platform.reindex.git_sync.sync_repo_to_remote",
        lambda repo_path: sync_calls.append(repo_path) or {"pulled": True, "note": "ok"},
    )
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit",
                        lambda repo_path, commit, timeout_sec=10: True, raising=False)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc")
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: True)

    assert worker.drain_once() == 3
    assert runner_calls == ["codegraph", "ingest", "code_vec"]
    assert sync_calls == [repo]


def test_codegraph_retry_blocks_same_drain_code_vec(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    jobs = [
        Job("demo", "codegraph", 1.0, token="cg"),
        Job("demo", "code_vec", 2.0, token="cv"),
    ]
    released: list[str] = []
    runner_calls: list[str] = []

    class _Q:
        def pending(self, projects=None):
            return list(jobs)

        def complete(self, done):
            raise AssertionError("retry job 不应 complete")

        def release(self, done):
            released.append(done.key)
            return True

    class _Runner:
        last_note = ""

        def __init__(self, kind):
            self._kind = kind

        def run(self, project_id, repo_path, cfg):
            runner_calls.append(self._kind)
            return 2 if self._kind == "codegraph" else 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner(kind))
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc123", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.read_manifest", lambda *a, **k: [])

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: True)

    assert worker.drain_once() == 2
    assert runner_calls == ["codegraph"]
    assert released == ["demo__codegraph", "demo__code_vec"]


def test_limit_queue_defers_retry_release_until_scan_finishes(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    pending = [
        Job("demo", "codegraph", 1.0, token="cg"),
        Job("demo", "code_vec", 2.0, token="cv"),
    ]
    active: list[Job] = []
    released: list[str] = []
    runner_calls: list[str] = []
    pending_calls = {"n": 0}

    class _Q:
        def pending(self, projects=None, limit=None):
            pending_calls["n"] += 1
            if pending_calls["n"] > 3:
                raise AssertionError("retry job 在同一次 drain 内被重复认领")
            batch = pending[:limit] if limit is not None else list(pending)
            del pending[:len(batch)]
            active.extend(batch)
            return batch

        def complete(self, done):
            raise AssertionError("retry job 不应 complete")

        def release(self, done):
            released.append(done.key)
            active[:] = [job for job in active if job.key != done.key]
            pending.insert(0, done)
            return True

    class _Runner:
        last_note = ""

        def __init__(self, kind):
            self._kind = kind

        def run(self, project_id, repo_path, cfg):
            runner_calls.append(self._kind)
            return 2 if self._kind == "codegraph" else 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner(kind))
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc123", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.read_manifest", lambda *a, **k: [])

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: True)

    assert worker.drain_once() == 2
    assert runner_calls == ["codegraph"]
    assert released == ["demo__codegraph", "demo__code_vec"]


def test_same_drain_dependency_still_works_with_limit_queue(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    jobs = [
        Job("demo", "codegraph", 1.0, token="cg"),
        Job("demo", "code_vec", 2.0, token="cv"),
    ]
    released: list[str] = []
    runner_calls: list[str] = []

    class _Q:
        def __init__(self):
            self._jobs = list(jobs)

        def pending(self, projects=None, limit=None):
            if not self._jobs:
                return []
            batch = self._jobs[:limit] if limit is not None else list(self._jobs)
            self._jobs = self._jobs[len(batch):]
            return batch

        def complete(self, done):
            raise AssertionError("retry job 不应 complete")

        def release(self, done):
            released.append(done.key)
            return True

    class _Runner:
        last_note = ""

        def __init__(self, kind):
            self._kind = kind

        def run(self, project_id, repo_path, cfg):
            runner_calls.append(self._kind)
            return 2 if self._kind == "codegraph" else 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner(kind))
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc123", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.read_manifest", lambda *a, **k: [])

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: True)

    assert worker.drain_once() == 2
    assert runner_calls == ["codegraph"]
    assert released == ["demo__codegraph", "demo__code_vec"]


def test_codegraph_failure_blocks_same_drain_code_vec_and_marks_failed(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    jobs = [
        Job("demo", "codegraph", 1.0, token="cg"),
        Job("demo", "code_vec", 2.0, token="cv"),
    ]
    completed: list[str] = []
    records: list[BuildRecord] = []
    runner_calls: list[str] = []

    class _Q:
        def pending(self, projects=None):
            return list(jobs)

        def complete(self, done):
            completed.append(done.key)
            return True

    class _Runner:
        last_note = "codegraph rc=1"

        def __init__(self, kind):
            self._kind = kind
            if kind != "codegraph":
                self.last_note = ""

        def run(self, project_id, repo_path, cfg):
            runner_calls.append(self._kind)
            return 1

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner(kind))
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc123", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.read_manifest", lambda *a, **k: [])
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: records.append(rec))

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: None)

    assert worker.drain_once() == 2
    assert runner_calls == ["codegraph"]
    assert completed == ["demo__codegraph", "demo__code_vec"]
    assert [(rec.kind, rec.status) for rec in records] == [("codegraph", "failed"), ("code_vec", "failed")]
    assert "codegraph" in records[-1].note


def test_fresh_codegraph_manifest_allows_code_vec_without_same_drain_dependency(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "code_vec", 1.0, token="cv", meta=JobMeta(target_commit="abc123"))
    completed: list[str] = []
    runner_calls: list[str] = []

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            completed.append(done.key)
            return True

    class _Runner:
        last_note = ""

        def run(self, project_id, repo_path, cfg):
            runner_calls.append("code_vec")
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit",
                        lambda repo_path, commit, timeout_sec=10: True, raising=False)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc123", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr(
        "codev_platform.index_manifest.read_manifest",
        lambda project_id, path=None: [BuildRecord(project_id="demo", kind="codegraph", git_commit="abc123", status="ok")],
    )
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: None)

    assert worker.drain_once() == 1
    assert runner_calls == ["code_vec"]
    assert completed == ["demo__code_vec"]


def test_descendant_codegraph_manifest_allows_code_vec_without_same_drain_dependency(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "code_vec", 1.0, token="cv", meta=JobMeta(target_commit="base123"))
    completed: list[str] = []
    runner_calls: list[str] = []

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            completed.append(done.key)
            return True

    class _Runner:
        last_note = ""

        def run(self, project_id, repo_path, cfg):
            runner_calls.append("code_vec")
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit",
                        lambda repo_path, commit, timeout_sec=10: True, raising=False)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "desc456", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "desc456")
    monkeypatch.setattr(
        "codev_platform.index_manifest.latest_build",
        lambda project_id, kind: BuildRecord(project_id="demo", kind="codegraph", git_commit="desc456", status="ok"),
    )
    monkeypatch.setattr(
        "codev_platform.index_manifest._commit_covers",
        lambda repo_path, target, indexed: target == "base123" and indexed == "desc456",
    )
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: None)

    assert worker.drain_once() == 1
    assert runner_calls == ["code_vec"]
    assert completed == ["demo__code_vec"]


def test_failed_codegraph_manifest_blocks_code_vec_without_retry(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "code_vec", 1.0, token="cv", meta=JobMeta(target_commit="abc123"))
    completed: list[str] = []
    released: list[str] = []
    records: list[BuildRecord] = []

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            completed.append(done.key)
            return True

        def release(self, done):
            released.append(done.key)
            return True

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: None if kind == "noop" else type("R", (), {"last_note": "", "run": lambda self, project_id, repo_path, cfg: 0})())
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit",
                        lambda repo_path, commit, timeout_sec=10: True, raising=False)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc123", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr(
        "codev_platform.index_manifest.latest_build",
        lambda project_id, kind: BuildRecord(project_id="demo", kind="codegraph", git_commit="abc123", status="failed"),
    )
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: records.append(rec))

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: None)

    assert worker.drain_once() == 1
    assert completed == ["demo__code_vec"]
    assert released == []
    assert [(rec.kind, rec.status) for rec in records] == [("code_vec", "failed")]
    assert "blocked by dependency: codegraph failed" in records[0].note


def test_missing_codegraph_manifest_retries_code_vec(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "code_vec", 1.0, token="cv", meta=JobMeta(target_commit="abc123"))
    completed: list[str] = []
    released: list[str] = []

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            completed.append(done.key)
            return True

        def release(self, done):
            released.append(done.key)
            return True

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: type("R", (), {"last_note": "", "run": lambda self, project_id, repo_path, cfg: 0})())
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit",
                        lambda repo_path, commit, timeout_sec=10: True, raising=False)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "abc123", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.latest_build", lambda project_id, kind: None)

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: None)

    assert worker.drain_once() == 1
    assert completed == []
    assert released == ["demo__code_vec"]


def test_stale_codegraph_manifest_retries_code_vec(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "code_vec", 1.0, token="cv", meta=JobMeta(target_commit="base123"))
    completed: list[str] = []
    released: list[str] = []

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            completed.append(done.key)
            return True

        def release(self, done):
            released.append(done.key)
            return True

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: type("R", (), {"last_note": "", "run": lambda self, project_id, repo_path, cfg: 0})())
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_covers_commit",
                        lambda repo_path, commit, timeout_sec=10: True, raising=False)
    monkeypatch.setattr("codev_platform.reindex.git_sync.repo_head",
                        lambda repo_path, timeout_sec=10: "desc456", raising=False)
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "desc456")
    monkeypatch.setattr(
        "codev_platform.index_manifest.latest_build",
        lambda project_id, kind: BuildRecord(project_id="demo", kind="codegraph", git_commit="old999", status="ok"),
    )
    monkeypatch.setattr("codev_platform.index_manifest._commit_covers", lambda repo_path, target, indexed: False)

    worker = ReindexWorker(_Q(), {})
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: None)

    assert worker.drain_once() == 1
    assert completed == []
    assert released == ["demo__code_vec"]


def test_refresh_health_uses_run_tree_and_requires_zero_returncode(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple[object, dict]] = []

    def fake_run_tree(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=1)

    monkeypatch.setattr(
        "codev_platform.mcp_serve._platform_runtime_python",
        lambda: tmp_path / "platform-python.exe",
    )
    monkeypatch.setattr("codev_platform.core.process_tree.run_tree", fake_run_tree)

    ok = ReindexWorker(_Q := type("Q", (), {})(), {})._refresh_health("demo", repo)

    assert ok is False
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert list(args[:5]) == [
        str(tmp_path / "platform-python.exe"),
        "-I",
        "-m",
        "codev_platform.cli",
        "health",
    ]
    assert kwargs["timeout"] == 120
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["no_window"] is True


def test_worker_success_path_records_phase_sequence_and_ok_result(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "chroma", time.time(), token="claim-1")
    phases: list[str] = []

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            return True

    class _Runner:
        last_note = "runner ok"

        def run(self, project_id, repo_path, cfg):
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    owner = supervisor.new_owner_token()
    supervisor.record_worker_start(owner, mode="short")
    phase_names = {
        "queue_scan", "git_sync", "runner", "manifest",
        "queue_complete", "health_refresh", "idle",
    }

    def _event(job_or_none, status):
        if status in phase_names:
            phases.append(status)
        supervisor.record_job_event(owner, job_or_none, status)

    worker = ReindexWorker(_Q(), {}, on_job_event=_event)
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: True)

    assert worker.drain_once() == 1

    st = supervisor.worker_status()
    assert phases == [
        "queue_scan", "git_sync", "runner", "manifest",
        "queue_complete", "health_refresh", "idle",
    ]
    assert st["phase"] == "idle"
    assert st["last_result"]["status"] == "ok"
    assert st["last_result"]["job_key"] == "demo__chroma"
    assert st["manifest_ok"] is True
    assert st["health_failed"] is False


def test_worker_runner_failure_records_failed_result(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "chroma", time.time(), token="claim-1")

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            return True

    class _Runner:
        last_note = "runner failed"

        def run(self, project_id, repo_path, cfg):
            return 1

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    owner = supervisor.new_owner_token()
    supervisor.record_worker_start(owner, mode="short")
    worker = ReindexWorker(
        _Q(), {},
        on_job_event=lambda job_or_none, status: supervisor.record_job_event(owner, job_or_none, status),
    )

    assert worker.drain_once() == 1

    st = supervisor.worker_status()
    assert st["last_result"]["status"] == "failed"
    assert st["last_result"]["detail"] == "runner failed"


def test_worker_manifest_failure_marks_manifest_false_without_changing_job_result(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "chroma", time.time(), token="claim-1")

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            return True

    class _Runner:
        last_note = "runner ok"

        def run(self, project_id, repo_path, cfg):
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr(
        "codev_platform.index_manifest.record_build",
        lambda rec: (_ for _ in ()).throw(RuntimeError("manifest down")),
    )

    owner = supervisor.new_owner_token()
    supervisor.record_worker_start(owner, mode="short")
    worker = ReindexWorker(
        _Q(), {},
        on_job_event=lambda job_or_none, status: supervisor.record_job_event(owner, job_or_none, status),
    )
    monkeypatch.setattr(worker, "_refresh_health", lambda project_id, repo_path: None)

    assert worker.drain_once() == 1

    st = supervisor.worker_status()
    assert st["last_result"]["status"] == "ok"
    assert st["manifest_ok"] is False


def test_worker_health_refresh_failure_marks_health_failed_without_overwriting_result(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    job = Job("demo", "chroma", time.time(), token="claim-1")

    class _Q:
        def pending(self, projects=None):
            return [job]

        def complete(self, done):
            return True

    class _Runner:
        last_note = "runner ok"

        def run(self, project_id, repo_path, cfg):
            return 0

    monkeypatch.setattr(worker_mod, "_repo_for", lambda cfg, project_id: repo)
    monkeypatch.setattr("codev_platform.reindex.runners.get_runner", lambda kind: _Runner())
    monkeypatch.setattr("codev_platform.index_manifest.git_head", lambda repo_path: "abc123")
    monkeypatch.setattr("codev_platform.index_manifest.record_build", lambda rec: None)

    owner = supervisor.new_owner_token()
    supervisor.record_worker_start(owner, mode="short")
    worker = ReindexWorker(
        _Q(), {},
        on_job_event=lambda job_or_none, status: supervisor.record_job_event(owner, job_or_none, status),
    )
    monkeypatch.setattr(
        worker,
        "_refresh_health",
        lambda project_id, repo_path: (_ for _ in ()).throw(TimeoutError("health timeout")),
    )

    assert worker.drain_once() == 1

    st = supervisor.worker_status()
    assert st["last_result"]["status"] == "ok"
    assert st["health_failed"] is True
