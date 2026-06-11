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


def test_pending_codegraph_before_code_vec_on_SAME_mtime(tmp_path):
    """审计 B2(真修): mtime **平手**时 code_vec 必须仍排在 codegraph 之后。

    全流程审计实证: ext4 上同 loop touch 三文件 mtime 完全相同, 文件名 tie-break
    ('code_vec'<'codegraph')会让 code_vec 先跑 → 读陈旧 codegraph.db。修法=tie-break 用
    runner 注册依赖序。**本测刻意设同 mtime 强制平手**(旧测用 os.utime 拉开 mtime 掩盖了它)。
    """
    import os
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "code_vec")     # 字母序靠前者先入队, 放大文件名 tie-break 翻车几率
    q.enqueue("demo-proj", "codegraph")
    same = (1000, 1000)                     # 同 mtime → 强制走 tie-break
    os.utime(tmp_path / "demo-proj__code_vec", same)
    os.utime(tmp_path / "demo-proj__codegraph", same)
    kinds = [j.kind for j in q.pending()]
    assert kinds.index("codegraph") < kinds.index("code_vec")   # 依赖序; 文件名 tie-break 会反


def test_pending_preserves_fifo_across_mtimes(tmp_path):
    """不同 mtime 仍按 FIFO(mtime 主序优先于依赖序 tie-break)。"""
    import os
    q = FileSpoolQueue(tmp_path)
    q.enqueue("demo-proj", "code_vec")
    q.enqueue("demo-proj", "chroma")
    os.utime(tmp_path / "demo-proj__code_vec", (1000, 1000))    # 先入队(旧)
    os.utime(tmp_path / "demo-proj__chroma", (2000, 2000))      # 后入队(新)
    kinds = [j.kind for j in q.pending()]
    assert kinds.index("code_vec") < kinds.index("chroma")      # mtime 主序 = FIFO
