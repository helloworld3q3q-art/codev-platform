"""FileSpoolQueue.complete —— 未知 kind(旧 spool/坏文件)丢弃时不应崩 drain(codex P2 #4)。"""
from __future__ import annotations

from codev_platform.reindex.queue import FileSpoolQueue, Job


def test_complete_unknown_kind_removes_file_no_crash(tmp_path):
    # 模拟升级后旧 spool 文件: 未知 kind 'oldkind'。worker 丢弃时调 complete, 修复前经
    # _path→_validate 白名单校验抛 ValueError 崩 drain; 修复后直接删 raw 文件不崩。
    q = FileSpoolQueue(tmp_path)
    f = tmp_path / "demo-proj__oldkind"
    f.touch()
    job = Job("demo-proj", "oldkind", f.stat().st_mtime)
    assert q.complete(job) is True   # 不抛 ValueError
    assert not f.exists()            # 坏 spool 文件被清掉, drain 不中断


def test_complete_path_traversal_kind_is_safe(tmp_path):
    # 防御: kind 含路径穿越 → 不删(不该入 spool), 也不崩。
    q = FileSpoolQueue(tmp_path)
    job = Job("demo-proj", "../escape", 0.0)
    assert q.complete(job) is True   # 当已完成, 不崩不删到 spool 外


def test_complete_known_kind_still_works(tmp_path):
    # 回归: 合法 kind 照常删除(不破坏正常路径)。
    q = FileSpoolQueue(tmp_path)
    f = tmp_path / "demo-proj__chroma"
    f.touch()
    job = Job("demo-proj", "chroma", f.stat().st_mtime)
    assert q.complete(job) is True
    assert not f.exists()


def test_complete_literal_dotdot_file_is_deleted(tmp_path):
    # 残留坏文件(安全审计 P2#4): spool 里 literal '..' 片段名的文件(pending 读得到, 原逻辑
    # 只 return True 不删 → 永久重处理)。resolved 仍在 spool 内 → 现在应被删。
    q = FileSpoolQueue(tmp_path)
    f = tmp_path / "..__chroma"            # pid='..' 的坏 spool 文件(literal 名, 非路径穿越)
    f.write_text("")
    assert q.complete(Job("..", "chroma", f.stat().st_mtime)) is True
    assert not f.exists()                   # 已删, 不再无限重处理


def test_pending_orders_by_mtime_not_alphabetical(tmp_path):
    """审计 B2: pending() 按入队时刻(mtime)= FIFO, 不是文件名字母序。

    'code_vec' < 'codegraph'(_ < g)字母序会让 code_vec 先跑 → 读陈旧 codegraph.db。
    dispatch/webhook 按依赖序入队的语义全靠 pending() 保 FIFO。
    """
    import os
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "codegraph")
    q.enqueue("demo-proj", "code_vec")
    # 显式设 mtime: codegraph 先入队(旧), code_vec 后(新)
    os.utime(tmp_path / "demo-proj__codegraph", (1000, 1000))
    os.utime(tmp_path / "demo-proj__code_vec", (2000, 2000))
    kinds = [j.kind for j in q.pending()]
    assert kinds.index("codegraph") < kinds.index("code_vec")   # FIFO; 字母序会反过来
