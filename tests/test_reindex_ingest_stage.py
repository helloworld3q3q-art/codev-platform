"""reindex 的统一图谱 ingest stage —— task1 回归。

覆盖:
- 默认 reindex (无 flag) 会跑 ingest stage (调 ingest_project)
- --ingest 单选只跑 ingest, 不跑 codegraph/chroma
- ingest 失败被隔离: 抛异常只 warn, reindex 退出码仍 0 (不拖垮基线)
- 无 project_id 时跳过 ingest (不抛)
- codegraph scope 改动 → _dispatch_reindex 顺带入队 ingest
- ingest runner 已注册 (kind='ingest')
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from codev_platform.ops import reindex as R


def _args(**kw) -> argparse.Namespace:
    base = dict(repo=None, chroma=False, codegraph=False,
                ingest=False, code_vec=False, force=False)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "project.json").write_text(
        '{"project_id": "demo-proj"}', encoding="utf-8")
    return tmp_path


def _stub_stages(monkeypatch):
    """让 codegraph/chroma stage 都 no-op 成功, 只留 ingest 真跑。"""
    class _CP:
        returncode = 0
    monkeypatch.setattr(R.C, "run", lambda *a, **k: _CP())
    # chroma python 存在性检查: 指向任意存在文件 → 跳过 FAIL, 走 C.run (已被 stub)
    monkeypatch.setattr(R.C, "chroma_python", lambda: __file__)
    # code_vec stage 默认 reindex 也跑 → stub 成 no-op, 避免真去建嵌入 (隔离 ingest 测试)
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.build_code_vector_index",
        lambda pid, **k: 0)


def test_default_runs_ingest_stage(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    called = {}

    def fake_ingest(repo, pid, **kw):
        called["repo"] = Path(repo)
        called["pid"] = pid
        return type("Rep", (), {"ingested": ["fake.plugin"], "summaries": {}})()

    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project", fake_ingest)
    rc = R.cmd_reindex(_args(repo=str(_repo)))
    assert rc == 0
    assert called["pid"] == "demo-proj"
    assert called["repo"] == _repo


def test_ingest_only_skips_other_stages(_repo, monkeypatch):
    ran = {"run": False}
    monkeypatch.setattr(R.C, "run", lambda *a, **k: ran.__setitem__("run", True) or type("C", (), {"returncode": 0})())
    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project",
                        lambda r, p, **k: type("Rep", (), {"ingested": [], "summaries": {}})())
    rc = R.cmd_reindex(_args(repo=str(_repo), ingest=True))
    assert rc == 0
    assert ran["run"] is False  # codegraph/chroma 一律没跑


def test_ingest_failure_isolated(_repo, monkeypatch):
    _stub_stages(monkeypatch)

    def boom(repo, pid, **kw):
        raise RuntimeError("plugin exploded")

    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project", boom)
    rc = R.cmd_reindex(_args(repo=str(_repo)))
    assert rc == 0  # ingest 崩了, 但基线退出码不受影响


def test_ingest_skipped_without_project_id(tmp_path, monkeypatch):
    _stub_stages(monkeypatch)
    seen = {"called": False}
    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project",
                        lambda *a, **k: seen.__setitem__("called", True))
    rc = R.cmd_reindex(_args(repo=str(tmp_path), ingest=True))  # 无 .claude/project.json
    assert rc == 0
    assert seen["called"] is False


def test_dispatch_enqueues_ingest_on_code_change(tmp_path, monkeypatch):
    enq: list[tuple[str, str]] = []

    class _Q:
        def enqueue(self, pid, kind):
            enq.append((pid, kind))

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Q())
    monkeypatch.setattr(R.C, "project_id_of", lambda repo: "demo-proj")
    # codegraph scope 命中 (apps/<x>/src/*.py 走 generic codegraph default)
    monkeypatch.setattr(R.C, "meta_health", lambda pid: {})
    rc = R._dispatch_reindex(tmp_path, ["apps/web/src/Foo.java"],
                             foreground=False, trigger_line="t", banner="test")
    assert rc == 0
    kinds = {k for _, k in enq}
    assert "codegraph" in kinds
    assert "ingest" in kinds  # 代码改动顺带刷 ingest


def test_ingest_runner_registered():
    from codev_platform.reindex.runners import kinds
    assert "ingest" in kinds()


# ---- code_vec stage (vector lane 随提交刷新) ----

def test_default_runs_code_vec_stage(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    called = {}
    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project",
                        lambda r, p, **k: type("Rep", (), {"ingested": [], "summaries": {}})())
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.build_code_vector_index",
        lambda pid, **k: called.update(pid=pid, incremental=k.get("incremental")) or 3)
    rc = R.cmd_reindex(_args(repo=str(_repo)))   # 默认全跑
    assert rc == 0
    assert called["pid"] == "demo-proj"
    assert called["incremental"] is True   # 非 --force → 增量


def test_code_vec_force_is_full(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    seen = {}
    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project",
                        lambda r, p, **k: type("Rep", (), {"ingested": [], "summaries": {}})())
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.build_code_vector_index",
        lambda pid, **k: seen.update(incremental=k.get("incremental")) or 0)
    rc = R.cmd_reindex(_args(repo=str(_repo), force=True))
    assert rc == 0
    assert seen["incremental"] is False   # --force → 全量


def test_code_vec_failure_isolated(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project",
                        lambda r, p, **k: type("Rep", (), {"ingested": [], "summaries": {}})())

    def boom(pid, **k):
        raise RuntimeError("embed daemon down")
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.build_code_vector_index", boom)
    rc = R.cmd_reindex(_args(repo=str(_repo)))
    assert rc == 0   # code_vec 崩了, 基线退出码不受影响


def test_dispatch_enqueues_code_vec_on_code_change(tmp_path, monkeypatch):
    enq: list[tuple[str, str]] = []

    class _Q:
        def enqueue(self, pid, kind):
            enq.append((pid, kind))

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Q())
    monkeypatch.setattr(R.C, "project_id_of", lambda repo: "demo-proj")
    monkeypatch.setattr(R.C, "meta_health", lambda pid: {})
    rc = R._dispatch_reindex(tmp_path, ["apps/web/src/Foo.java"],
                             foreground=False, trigger_line="t", banner="test")
    assert rc == 0
    order = [k for _, k in enq]
    assert "code_vec" in order
    # code_vec 必须排在 codegraph 之后(读新鲜 codegraph.db)
    assert order.index("code_vec") > order.index("codegraph")


def test_code_vec_runner_registered():
    from codev_platform.reindex.runners import kinds
    assert "code_vec" in kinds()
