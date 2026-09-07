from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import codev_platform.runtime_bound_worker as worker
from codev_platform.runtime_bound_worker import RuntimeBoundWorkerError, run_bound_operation
from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBinding,
    RuntimeRootBindingError,
)


_LINUX = sys.platform.startswith("linux")


@pytest.mark.skipif(not _LINUX, reason="root-fd worker 仅在 WSL/Linux 验证")
def testworker在可见根替换后仍进入原根descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    original_identity = (root.stat().st_dev, root.stat().st_ino)
    previous = tmp_path / "runtime-previous"
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    binding = RuntimeRootBinding(root, os.geteuid())

    original_duplicate = BoundRuntimeRoot._duplicate_root_fd

    def duplicate_then_replace(bound_root: BoundRuntimeRoot) -> int:
        descriptor = original_duplicate(bound_root)
        root.rename(previous)
        root.symlink_to(replacement, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(BoundRuntimeRoot, "_duplicate_root_fd", duplicate_then_replace)
    with pytest.raises(RuntimeRootBindingError, match="运行时根"):
        with binding.bind() as bound_root:
            result = run_bound_operation(bound_root, "root-identity", {})
            assert (result["device"], result["inode"]) == original_identity

    assert not (replacement / "bases").exists()
    assert not (replacement / "releases").exists()


@pytest.mark.skipif(not _LINUX, reason="root-fd worker 仅在 WSL/Linux 验证")
def test继承根描述符拒绝替换后的可见命名根(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    previous = tmp_path / "runtime-previous"
    parent_binding = RuntimeRootBinding(root, os.geteuid())

    with parent_binding.bind() as bound_root:
        descriptor = bound_root._duplicate_root_fd()

    root.rename(previous)
    root.mkdir()
    inherited_binding = RuntimeRootBinding(root, os.geteuid())
    try:
        with pytest.raises(RuntimeRootBindingError, match="运行时根"):
            with inherited_binding.bind_inherited_descriptor(descriptor):
                pytest.fail("替换后的命名根不能得到继承根租约")
    finally:
        os.close(descriptor)

    assert not (root / "bases").exists()
    assert not (root / "releases").exists()


@pytest.mark.skipif(not _LINUX, reason="root-fd worker 仅在 WSL/Linux 验证")
def testworker执行服从外层运行时总截止(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import codev_platform.runtime_bound_worker as worker
    from codev_platform.runtime_deadline import runtime_deadline_scope

    root = tmp_path / "runtime"
    root.mkdir()
    observed: dict[str, object] = {}

    def run_in_scope(**kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["timeout"] = float(kwargs["timeout"])
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            args=("systemd-run",),
            returncode=0,
            stdout=b'{"ok":true,"result":{"device":1,"inode":1}}',
            stderr=b"",
        )

    def forbidden_direct_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("root-fd worker 不得绕过进程树直接启动")

    monkeypatch.setattr(worker, "run_in_worker_scope", run_in_scope, raising=False)
    monkeypatch.setattr(worker.subprocess, "run", forbidden_direct_run)
    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        with runtime_deadline_scope(0.5):
            run_bound_operation(bound_root, "root-identity", {}, timeout_sec=120.0)

    assert 0 < observed["timeout"] <= 0.5
    kwargs = observed["kwargs"]
    assert kwargs["operation"] == "root-identity"
    assert kwargs["payload"] == b"{}"
    assert kwargs["root_descriptor"] >= 3
    assert kwargs["parent_pidfd"] >= 3
    assert kwargs["bootstrap_path"] == Path(worker.__file__).resolve().with_name(
        "runtime_bound_worker_bootstrap.py"
    )
    assert kwargs["source_root"] == Path(worker.__file__).resolve().parents[1]


@pytest.mark.skipif(not _LINUX, reason="需要 Linux pidfd 前置门禁")
@pytest.mark.parametrize("missing", ("pidfd_open", "pidfd_send_signal"))
def testworker缺少pidfd能力时拒绝在启动scope前执行(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing: str,
) -> None:
    """没有不可复用的父进程身份时，不能退回可 fork 泄漏的存活 pipe。"""
    root = tmp_path / "runtime"
    root.mkdir()

    def forbidden_scope_start(**_kwargs: object) -> object:
        raise AssertionError("缺少 pidfd 时不应启动 scope")

    monkeypatch.setattr(worker, "run_in_worker_scope", forbidden_scope_start)
    if missing == "pidfd_open":
        monkeypatch.delattr(worker.os, missing, raising=False)
    else:
        monkeypatch.delattr(worker.signal, missing, raising=False)

    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        with pytest.raises(RuntimeBoundWorkerError, match="pidfd"):
            run_bound_operation(bound_root, "root-identity", {})


@pytest.mark.skipif(not _LINUX, reason="root-fd worker 仅在 WSL/Linux 验证")
def testworker拒绝PYTHONPATH影子包并返回受信根身份(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """子解释器不得从调用方 cwd 或 PYTHONPATH 导入伪造 worker。"""
    shadow_root = tmp_path / "shadow"
    shadow_package = shadow_root / "codev_platform"
    shadow_package.mkdir(parents=True)
    marker = tmp_path / "shadow-worker-ran.txt"
    (shadow_package / "__init__.py").write_text("", encoding="utf-8")
    (shadow_package / "runtime_bound_worker.py").write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "Path(os.environ['WORKER_SHADOW_MARKER']).write_text('ran', encoding='utf-8')\n"
        "print(json.dumps({'ok': True, 'result': {'device': -1, 'inode': -1}}))\n",
        encoding="utf-8",
    )
    root = tmp_path / "runtime"
    root.mkdir()
    expected = (root.stat().st_dev, root.stat().st_ino)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(shadow_root))
    monkeypatch.setenv("WORKER_SHADOW_MARKER", str(marker))

    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        result = run_bound_operation(bound_root, "root-identity", {})

    assert (result["device"], result["inode"]) == expected
    assert not marker.exists()


@pytest.mark.skipif(not _LINUX, reason="需要 Linux root-fd worker 前置门禁")
def testworker在非Linux父端预检时拒绝启动(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """其他 POSIX 平台不得先拉起子进程、再由 Linux guard 失败。"""
    import codev_platform.runtime_bound_worker as worker

    root = tmp_path / "runtime"
    root.mkdir()
    monkeypatch.setattr(worker.sys, "platform", "darwin")

    with RuntimeRootBinding(root, os.geteuid()).bind() as bound_root:
        with pytest.raises(RuntimeBoundWorkerError, match="Linux"):
            run_bound_operation(bound_root, "root-identity", {})
