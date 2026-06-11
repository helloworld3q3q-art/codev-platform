"""ReindexWorker 多 org 亲和 + 崩溃恢复接线(无需 PG)。

- _own_projects(): 只圈出 config.projects 里配了存在 repo_path 的 project(传给 pending 做白名单)。
- drain_once(): 把白名单传给 queue.pending(projects=...)。
- _reclaim_stale_own(): 仅当 queue 有 reclaim_stale_own 方法时调(file 后端无 → 跳过, 不崩)。
"""
from __future__ import annotations

from codev_platform.reindex.queue import FileSpoolQueue, Job
from codev_platform.reindex.worker import ReindexWorker


def test_own_projects_none_when_no_projects_cfg(tmp_path):
    # config 无 projects 段 → None(退回"认领全部"旧语义, 单机 file 常态)。
    w = ReindexWorker(FileSpoolQueue(tmp_path), {})
    assert w._own_projects() is None


def test_own_projects_only_those_with_existing_repo(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    cfg = {"projects": {
        "has-repo": {"repo_path": str(repo)},      # 有且存在 → 入集合
        "no-path": {"some": "x"},                  # 无 repo_path → 排除
        "missing": {"repo_path": str(tmp_path / "nope")},  # 路径不存在 → 排除
    }}
    w = ReindexWorker(FileSpoolQueue(tmp_path), cfg)
    assert w._own_projects() == {"has-repo"}


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
