"""稳定运行文件安全写入边界测试。"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


def test_文本追加显式收紧目录和文件权限(tmp_path: Path) -> None:
    from codev_platform.core.runtime_artifact_io import append_runtime_artifact_text

    path = tmp_path / "logs" / "usage.jsonl"

    assert append_runtime_artifact_text(path, "第一行\n") is True
    assert path.read_text(encoding="utf-8") == "第一行\n"
    if os.name != "nt":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_文本追加遇到短写会循环直到完整提交(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codev_platform.core.runtime_artifact_io as artifact_io

    class ShortWriter:
        def __init__(self) -> None:
            self.content = bytearray()

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def write(self, content: bytes | memoryview) -> int:
            chunk = bytes(content[:2])
            self.content.extend(chunk)
            return len(chunk)

    writer = ShortWriter()
    monkeypatch.setattr(artifact_io, "open_runtime_artifact_binary", lambda _path: writer)

    assert artifact_io.append_runtime_artifact_text(tmp_path / "usage.jsonl", "完整写入")
    assert bytes(writer.content) == "完整写入".encode()


def test_二进制日志打开显式收紧已有宽权限(tmp_path: Path) -> None:
    from codev_platform.core.runtime_artifact_io import open_runtime_artifact_binary

    path = tmp_path / "logs" / "daemon.log"
    path.parent.mkdir(mode=0o777)
    path.write_bytes(b"old\n")
    path.chmod(0o666)

    with open_runtime_artifact_binary(path) as stream:
        stream.write(b"new\n")

    assert path.read_bytes() == b"old\nnew\n"
    if os.name != "nt":
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_文本覆盖不会把旧快照尾部残留(tmp_path: Path) -> None:
    from codev_platform.core.runtime_artifact_io import write_runtime_artifact_text

    path = tmp_path / "health" / "snapshot.json"
    path.parent.mkdir()
    path.write_text("旧内容很长", encoding="utf-8")

    assert write_runtime_artifact_text(path, "新\n") is True
    assert path.read_text(encoding="utf-8") == "新\n"


def test_原子覆盖提交失败时保留旧快照(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.core.runtime_artifact_io import write_runtime_artifact_text

    path = tmp_path / "health" / "snapshot.json"
    path.parent.mkdir()
    path.write_text("旧快照\n", encoding="utf-8")

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    assert write_runtime_artifact_text(path, "新快照\n") is False
    assert path.read_text(encoding="utf-8") == "旧快照\n"
    assert not tuple(path.parent.glob(".*.tmp"))


def test_安全写入拒绝符号链接目标(tmp_path: Path) -> None:
    from codev_platform.core.runtime_artifact_io import RuntimeArtifactIOError
    from codev_platform.core.runtime_artifact_io import open_runtime_artifact_binary

    target = tmp_path / "target.log"
    target.write_text("不可改\n", encoding="utf-8")
    link = tmp_path / "logs" / "daemon.log"
    link.parent.mkdir()
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("当前系统不允许创建测试符号链接")

    with pytest.raises(RuntimeArtifactIOError, match="运行文件.*不安全"):
        open_runtime_artifact_binary(link)

    assert target.read_text(encoding="utf-8") == "不可改\n"


def test_安全写入拒绝父目录链接或_reparse(tmp_path: Path) -> None:
    from codev_platform.core.runtime_artifact_io import RuntimeArtifactIOError
    from codev_platform.core.runtime_artifact_io import open_runtime_artifact_binary

    target = tmp_path / "real-logs"
    target.mkdir()
    linked_parent = tmp_path / "logs"
    try:
        linked_parent.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("当前系统不允许创建测试目录链接")

    with pytest.raises(RuntimeArtifactIOError, match="运行文件目录.*不安全"):
        open_runtime_artifact_binary(linked_parent / "daemon.log")

    assert not (target / "daemon.log").exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows 硬链接权限与 POSIX 不同")
def test_安全写入拒绝多链接目标(tmp_path: Path) -> None:
    from codev_platform.core.runtime_artifact_io import RuntimeArtifactIOError
    from codev_platform.core.runtime_artifact_io import open_runtime_artifact_binary

    target = tmp_path / "target.log"
    target.write_text("不可改\n", encoding="utf-8")
    link = tmp_path / "logs" / "daemon.log"
    link.parent.mkdir()
    os.link(target, link)

    with pytest.raises(RuntimeArtifactIOError, match="运行文件类型不安全"):
        open_runtime_artifact_binary(link)

    assert target.read_text(encoding="utf-8") == "不可改\n"


@pytest.mark.skipif(os.name == "nt", reason="Windows 没有 POSIX FIFO")
@pytest.mark.parametrize(
    "function_name",
    ("append_runtime_artifact_text", "write_runtime_artifact_text"),
)
def test_安全写入遇到_fifo_必须立即失败而不是阻塞(
    tmp_path: Path,
    function_name: str,
) -> None:
    path = tmp_path / "logs" / "blocked.log"
    path.parent.mkdir()
    os.mkfifo(path)
    script = f"""
import sys
from pathlib import Path
from codev_platform.core.runtime_artifact_io import {function_name}
print({function_name}(Path(sys.argv[1]), "测试\\n"), flush=True)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert completed.stdout == "False\n"
