"""运行时 Python 执行前静态信任边界测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codev_platform import runtime_execution_trust as trust


_ROOT_POSIX = os.name == "posix" and os.geteuid() == 0


def _layout(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    runtime = tmp_path / "runtime"
    object_root = runtime / "releases" / ("a" * 64)
    python = object_root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = object_root / "venv" / "site-packages"
    python.parent.mkdir(parents=True)
    purelib.mkdir(parents=True)
    python.write_bytes(b"python")
    return runtime, object_root, python, purelib


@pytest.mark.skipif(os.name == "posix", reason="Windows 重解析点围栏")
def test_non_posix_rejects_ancestor_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, object_root, python, purelib = _layout(tmp_path)
    original = trust._is_linklike
    blocked = runtime
    monkeypatch.setattr(
        trust,
        "_is_linklike",
        lambda path: path == blocked or original(path),
    )

    with pytest.raises(trust.RuntimeExecutionTrustError, match="链接"):
        trust.verify_execution_trust(runtime, object_root, ((python, True), (purelib, False)))


@pytest.mark.skipif(os.name == "posix", reason="Windows 解释器叶子围栏")
def test_non_posix_rejects_python_leaf_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, object_root, python, purelib = _layout(tmp_path)
    original = trust._is_linklike
    monkeypatch.setattr(
        trust,
        "_is_linklike",
        lambda path: path == python or original(path),
    )

    with pytest.raises(trust.RuntimeExecutionTrustError, match="链接"):
        trust.verify_execution_trust(runtime, object_root, ((python, True), (purelib, False)))


def test_managed_path_escape_is_rejected(tmp_path: Path) -> None:
    runtime, object_root, _python, purelib = _layout(tmp_path)
    outside = tmp_path / "outside-python"
    outside.write_bytes(b"python")

    with pytest.raises(trust.RuntimeExecutionTrustError, match="越界"):
        trust.verify_execution_trust(runtime, object_root, ((outside, False), (purelib, False)))


def test_root控制解释器拒绝不存在的目标(tmp_path: Path) -> None:
    with pytest.raises(trust.RuntimeExecutionTrustError, match="解释器"):
        trust.verify_root_controlled_executable(tmp_path / "missing-python")


@pytest.mark.skipif(not _ROOT_POSIX, reason="仅由 root 的 POSIX worker 验证 cwd 路径边界")
def testcwd执行信任在根替换后只读取继承cwd(
    tmp_path: Path,
) -> None:
    """worker 不得因静态 trust 复验重新读取替换后的命名 runtime 根。"""
    root = tmp_path / "runtime"
    object_root = root / "releases" / ("a" * 64)
    python = object_root / "venv" / "bin" / "python"
    purelib = object_root / "venv" / "site-packages"
    python.parent.mkdir(parents=True)
    purelib.mkdir(parents=True)
    python.symlink_to("/usr/bin/env")
    previous = tmp_path / "runtime-previous"
    cwd_descriptor = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fchdir(root_descriptor)
        root.rename(previous)
        root.mkdir()
        trust.verify_execution_trust_from_cwd(
            Path("releases") / ("a" * 64),
            (
                (Path("releases") / ("a" * 64) / "venv" / "bin" / "python", True),
                (Path("releases") / ("a" * 64) / "venv" / "site-packages", False),
            ),
        )
    finally:
        os.fchdir(cwd_descriptor)
        os.close(root_descriptor)
        os.close(cwd_descriptor)

    assert not (root / "releases").exists()
    assert (previous / "releases" / ("a" * 64) / "venv" / "site-packages").is_dir()


@pytest.mark.skipif(not _ROOT_POSIX, reason="仅由 root 的 POSIX worker 验证 cwd 路径边界")
def testcwd执行信任拒绝未列入执行路径的可写成员(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cwd 入口必须保持绝对入口的整棵对象树权限证明。"""
    root = tmp_path / "runtime"
    object_root = root / "releases" / ("a" * 64)
    python = object_root / "venv" / "bin" / "python"
    purelib = object_root / "venv" / "site-packages"
    python.parent.mkdir(parents=True)
    purelib.mkdir(parents=True)
    python.symlink_to("/usr/bin/env")
    unexpected = object_root / "unmanaged.py"
    unexpected.write_bytes(b"pass\n")
    os.chmod(unexpected, 0o666)
    monkeypatch.chdir(root)

    with pytest.raises(trust.RuntimeExecutionTrustError, match="非所有者可写"):
        trust.verify_execution_trust_from_cwd(
            Path("releases") / ("a" * 64),
            (
                (Path("releases") / ("a" * 64) / "venv" / "bin" / "python", True),
                (Path("releases") / ("a" * 64) / "venv" / "site-packages", False),
            ),
        )


@pytest.mark.skipif(not _ROOT_POSIX, reason="仅由 root 的 POSIX worker 验证 cwd 路径边界")
def testcwd执行信任拒绝未列入执行路径的不可信链接目标(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """整树扫描不得跳过会逃离受管对象的额外符号链接。"""
    root = tmp_path / "runtime"
    object_root = root / "releases" / ("a" * 64)
    python = object_root / "venv" / "bin" / "python"
    purelib = object_root / "venv" / "site-packages"
    python.parent.mkdir(parents=True)
    purelib.mkdir(parents=True)
    python.symlink_to("/usr/bin/env")
    (object_root / "foreign-link").symlink_to("/tmp", target_is_directory=True)
    monkeypatch.chdir(root)

    with pytest.raises(trust.RuntimeExecutionTrustError, match="链接"):
        trust.verify_execution_trust_from_cwd(
            Path("releases") / ("a" * 64),
            (
                (Path("releases") / ("a" * 64) / "venv" / "bin" / "python", True),
                (Path("releases") / ("a" * 64) / "venv" / "site-packages", False),
            ),
        )


@pytest.mark.skipif(os.name == "posix", reason="Windows 解释器重解析点围栏")
def test_non_posix_root控制解释器拒绝重解析点(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "python.exe"
    executable.write_bytes(b"python")
    original = trust._is_linklike
    monkeypatch.setattr(
        trust,
        "_is_linklike",
        lambda path: path == executable or original(path),
    )

    with pytest.raises(trust.RuntimeExecutionTrustError, match="解释器.*链接"):
        trust.verify_root_controlled_executable(executable)
