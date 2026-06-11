"""read_file / list_dir 工具测试 —— 重点锁沙箱(穿越拒绝 + 敏感文件拒读)。

repo 根用 tmp_path 造,monkeypatch repo_path_of 指向它,不依赖真 platform_meta。
"""
from __future__ import annotations

import pytest

from codev_platform.agent.tools import fs


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    (tmp_path / "codev_platform" / "web").mkdir(parents=True)
    (tmp_path / "codev_platform" / "web" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "web-ui").mkdir()
    (tmp_path / ".env").write_text("STOCK_DB_PASSWORD=supersecret\n", encoding="utf-8")
    (tmp_path / "deploy.pem").write_text("-----BEGIN KEY-----\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    # 仓根解析 + project_id 解析都打桩到 tmp_path(不连真 config / platform_meta)
    monkeypatch.setattr(fs, "_repo_root", lambda pid: tmp_path.resolve())
    monkeypatch.setattr(fs, "resolve_project_id", lambda explicit: "codev-platform")
    return tmp_path


def test_read_file_ok(repo):
    r = fs.ReadFileTool("codev-platform").run({"path": "codev_platform/web/app.py"})
    assert not r.is_error
    assert "print('hi')" in r.content
    assert r.content.startswith("# codev_platform/web/app.py")


def test_read_file_window(repo):
    # 行窗口读: offset=2,limit=2 → 取第 2-3 行, 带行号 + 续读提示, 不含其它行。
    (repo / "multi.py").write_text("L1\nL2\nL3\nL4\nL5\n", encoding="utf-8")
    r = fs.ReadFileTool("codev-platform").run({"path": "multi.py", "offset": 2, "limit": 2})
    assert not r.is_error
    assert "行 2-3 / 共 5" in r.content
    assert "2\tL2" in r.content and "3\tL3" in r.content
    assert "1\tL1" not in r.content and "4\tL4" not in r.content  # 窗口外不含
    assert "offset=4 继续" in r.content                            # 还有更多 → 续读提示


def test_read_file_window_to_end_no_more_hint(repo):
    (repo / "m2.py").write_text("a\nb\nc\n", encoding="utf-8")
    r = fs.ReadFileTool("codev-platform").run({"path": "m2.py", "offset": 2, "limit": 10})
    assert "行 2-3 / 共 3" in r.content and "继续" not in r.content  # 读到末尾, 无续读提示


def test_read_file_default_whole_unchanged(repo):
    # 不给 offset/limit → 原全文行为(无行号, # path 开头), 默认不变。
    r = fs.ReadFileTool("codev-platform").run({"path": "codev_platform/web/app.py"})
    assert r.content.startswith("# codev_platform/web/app.py\n")
    assert "print('hi')" in r.content and "/ 共 " not in r.content


def test_read_file_blocks_path_traversal(repo):
    r = fs.ReadFileTool("codev-platform").run({"path": "../../../etc/passwd"})
    assert r.is_error and "穿越" in r.content


def test_read_file_blocks_absolute_escape(repo):
    r = fs.ReadFileTool("codev-platform").run({"path": "/etc/hosts"})
    assert r.is_error  # 绝对路径 resolve 后不在仓根内 → 拒绝


def test_read_file_blocks_secrets(repo):
    assert fs.ReadFileTool("codev-platform").run({"path": ".env"}).is_error
    assert fs.ReadFileTool("codev-platform").run({"path": "deploy.pem"}).is_error


def test_read_file_missing(repo):
    r = fs.ReadFileTool("codev-platform").run({"path": "nope.py"})
    assert r.is_error and "不存在" in r.content


def test_list_dir_root_skips_noise(repo):
    r = fs.ListDirTool("codev-platform").run({})
    assert not r.is_error
    assert "d codev_platform" in r.content
    assert "d web-ui" in r.content          # 能看到前端目录(正是 web 端 agent 当初漏掉的)
    assert "node_modules" not in r.content   # 噪声目录被跳过


def test_list_dir_traversal_blocked(repo):
    r = fs.ListDirTool("codev-platform").run({"path": "../.."})
    assert r.is_error and "穿越" in r.content


def test_repo_root_unknown_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "_repo_root", lambda pid: None)
    monkeypatch.setattr(fs, "resolve_project_id", lambda explicit: "x")
    r = fs.ReadFileTool("x").run({"path": "a.py"})
    assert r.is_error and "仓根未知" in r.content
