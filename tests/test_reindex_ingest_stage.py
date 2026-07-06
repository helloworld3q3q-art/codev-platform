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


def test_codegraph_stage_syncs_all_repo_specs(_repo, tmp_path, monkeypatch):
    from codev_platform.core.repos import RepoSpec
    extra = tmp_path / "extra"; extra.mkdir()
    specs = [
        RepoSpec(root=_repo.resolve(), tag="", is_main=True, source_project_id="demo-proj"),
        RepoSpec(root=extra.resolve(), tag="extra", is_main=False, source_project_id="extra-proj"),
    ]
    calls: list[Path] = []
    links: list[tuple[str, Path]] = []

    class _CP:
        returncode = 0

    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs",
                        lambda pid, **kw: specs)
    monkeypatch.setattr("codev_platform.ops.codegraph.ensure_codegraph_linked",
                        lambda pid, repo, cfg: links.append((pid, Path(repo))) or {"action": "ok"})
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(R.C, "run",
                        lambda cmd, **kw: calls.append(Path(kw["cwd"])) or _CP())

    rc = R.cmd_reindex(_args(repo=str(_repo), codegraph=True))

    assert rc == 0
    assert calls == [_repo.resolve(), extra.resolve()]
    assert links == [("demo-proj", _repo.resolve()), ("extra-proj", extra.resolve())]


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


def test_dispatch_enqueues_parent_project_for_extra_repo_change(tmp_path, monkeypatch):
    enq: list[tuple[str, str]] = []

    class _Q:
        def enqueue(self, pid, kind):
            enq.append((pid, kind))

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda: _Q())
    monkeypatch.setattr(R.C, "project_id_of", lambda repo: "child-proj")
    monkeypatch.setattr(R.C, "config", lambda: {})
    monkeypatch.setattr(R.C, "meta_health", lambda pid: {})
    monkeypatch.setattr(
        "codev_platform.ops.reindex.dispatch.impacted_project_ids_for_repo",
        lambda repo, **kw: ["child-proj", "parent-proj"],
    )

    rc = R._dispatch_reindex(tmp_path, ["apps/web/src/Foo.java"],
                             foreground=False, trigger_line="t", banner="test")

    assert rc == 0
    assert [pid for pid, kind in enq if kind == "codegraph"] == ["child-proj", "parent-proj"]
    parent_order = [kind for pid, kind in enq if pid == "parent-proj"]
    assert parent_order == ["codegraph", "ingest", "code_vec"]


def test_code_vec_runner_registered():
    from codev_platform.reindex.runners import kinds
    assert "code_vec" in kinds()


def test_code_vec_runner_syncs_codegraph_first(monkeypatch):
    # R4 真修: code_vec runner 用 --codegraph --code-vec 同进程先同步再建, 保证读新鲜 db。
    import codev_platform.reindex.runners as RU
    cap = {}
    monkeypatch.setattr(RU, "_venv_python", lambda cfg: "py")
    monkeypatch.setattr(RU.subprocess, "run",
                        lambda cmd, **k: cap.update(cmd=cmd) or type("C", (), {"returncode": 0})())
    RU.get_runner("code_vec").run("demo-proj", Path("/repo"), {})
    assert "--codegraph" in cap["cmd"] and "--code-vec" in cap["cmd"]
    assert cap["cmd"].index("--codegraph") < cap["cmd"].index("--code-vec")   # 先 sync 后建


# ---- R4: codegraph 锁忙(rc=2)处置 ----

def test_decide_codegraph_lock_outcome_truth_table():
    from codev_platform.ops.reindex.commands import decide_codegraph_lock_outcome as d
    assert d(True, True, True) == (True, 2)     # 锁忙+要跑 code_vec+开关 → 跳过+重试
    assert d(True, True, False) == (False, 0)   # 开关关 → 旧行为
    assert d(True, False, True) == (False, 0)   # 本次不跑 code_vec → 不强制重试
    assert d(False, True, True) == (False, 0)   # 没锁 → 正常
    assert d(False, False, False) == (False, 0)


def _run_codegraph_locked(monkeypatch):
    """C.run: codegraph sync 返 rc=2(锁忙), 其它返 0。"""
    class _CP:
        def __init__(self, rc): self.returncode = rc
    def _run(cmd, **kw):
        s = " ".join(str(c) for c in cmd)
        return _CP(2 if "codegraph" in s else 0)
    monkeypatch.setattr(R.C, "run", _run)
    monkeypatch.setattr(R.C, "chroma_python", lambda: __file__)
    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project",
                        lambda r, p, **k: type("Rep", (), {"ingested": [], "summaries": {}})())


def test_codegraph_lock_skips_code_vec_and_rc2(_repo, monkeypatch):
    _run_codegraph_locked(monkeypatch)
    called = {"build": False}
    monkeypatch.setattr("codev_platform.recall.code_vector_store.build_code_vector_index",
                        lambda pid, **k: called.__setitem__("build", True) or 0)
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})  # switch 默认 True
    rc = R.cmd_reindex(_args(repo=str(_repo)))
    assert rc == 2                      # 锁忙 → rc=2 让 worker 重试
    assert called["build"] is False     # code_vec 被跳过, 不嵌陈旧 db


def test_codegraph_lock_switch_off_legacy(_repo, monkeypatch):
    _run_codegraph_locked(monkeypatch)
    called = {"build": False}
    monkeypatch.setattr("codev_platform.recall.code_vector_store.build_code_vector_index",
                        lambda pid, **k: called.__setitem__("build", True) or 0)
    # 开关关 → 回退旧行为: code_vec 照跑, rc=0
    monkeypatch.setattr("codev_platform.core.config.load_config",
                        lambda: {"reindex": {"codevec_block_on_codegraph_lock": False}})
    rc = R.cmd_reindex(_args(repo=str(_repo)))
    assert rc == 0 and called["build"] is True


# ---- R2: code_vec 写侧锁忙 → rc=2 ----

def test_code_vec_lock_busy_returns_rc2(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project",
                        lambda r, p, **k: type("Rep", (), {"ingested": [], "summaries": {}})())
    from codev_platform.recall.code_vector_store import CodeVecLockBusy

    def _busy(pid, **k):
        raise CodeVecLockBusy("another build running")
    monkeypatch.setattr("codev_platform.recall.code_vector_store.build_code_vector_index", _busy)
    rc = R.cmd_reindex(_args(repo=str(_repo)))
    assert rc == 2     # 锁忙 → worker 重试; 区别于普通异常的 fail-soft rc=0


def test_code_vec_generic_failure_still_rc0(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project",
                        lambda r, p, **k: type("Rep", (), {"ingested": [], "summaries": {}})())

    def _boom(pid, **k):
        raise RuntimeError("embed daemon down")
    monkeypatch.setattr("codev_platform.recall.code_vector_store.build_code_vector_index", _boom)
    rc = R.cmd_reindex(_args(repo=str(_repo)))
    assert rc == 0     # 普通失败仍 fail-soft 不污染基线
