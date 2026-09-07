"""systemd 外 reindex worker 的 fail-closed 进程证明测试。"""
from __future__ import annotations

import pytest


def _process(
    proc_root,
    pid: int,
    args: list[str],
    cgroup: str,
    environment: dict[str, str] | None = None,
) -> None:
    directory = proc_root / str(pid)
    directory.mkdir(parents=True)
    directory.joinpath("cmdline").write_bytes(
        b"\0".join(part.encode("utf-8") for part in args) + b"\0"
    )
    directory.joinpath("cgroup").write_text(cgroup, encoding="utf-8")
    if environment is not None:
        entries = [f"{key}={value}".encode() for key, value in environment.items()]
        directory.joinpath("environ").write_bytes(b"\0".join(entries) + b"\0")


def _worker_args() -> list[str]:
    return [
        "/venv/bin/python",
        "-m",
        "codev_platform.cli",
        "reindex-queue",
        "worker",
        "--require-execution-mode",
        "isolated",
    ]


def _module_args(action: str, *arguments: str) -> list[str]:
    """构造旧版和新版共用的 python -m CLI 命令行。"""
    return [
        "/venv/bin/python",
        "-m",
        "codev_platform.cli",
        action,
        *arguments,
    ]


def _direct_module_args(module: str, *arguments: str) -> list[str]:
    """构造不经过平台 CLI 的正式 Python 模块入口命令行。"""
    return ["/venv/bin/python", "-m", module, *arguments]


def test_证明允许属于codev_reindex_unit及无关进程(tmp_path) -> None:
    from codev_platform.reindex.external_worker_guard import (
        assert_no_external_reindex_workers,
    )

    _process(
        tmp_path,
        101,
        _worker_args(),
        "0::/system.slice/codev-reindex.service/reindex-attempt.scope\n",
    )
    _process(tmp_path, 102, ["/usr/bin/python", "-m", "http.server"], "0::/user.slice\n")

    assert_no_external_reindex_workers(proc_root=tmp_path)


def test_证明拒绝systemd外精确匹配的reindex_worker且不暴露pid(tmp_path) -> None:
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_workers,
    )

    _process(tmp_path, 333, _worker_args(), "0::/user.slice/user-1000.slice\n")

    with pytest.raises(ExternalReindexWorkerError) as raised:
        assert_no_external_reindex_workers(proc_root=tmp_path)

    assert "333" not in str(raised.value)


def test_证明也拒绝console_script形式的systemd外worker(tmp_path) -> None:
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_workers,
    )

    _process(
        tmp_path,
        334,
        ["/venv/bin/codev-platform", "reindex-queue", "worker"],
        "0::/user.slice/user-1000.slice\n",
    )

    with pytest.raises(ExternalReindexWorkerError):
        assert_no_external_reindex_workers(proc_root=tmp_path)


@pytest.mark.parametrize(
    "arguments",
    [
        _module_args("reindex", "--codegraph"),
        ["/venv/bin/codev-platform", "reindex", "--ingest"],
        _module_args("post-commit", "--foreground"),
        _module_args("post-merge", "--foreground"),
        _module_args("post-checkout", "old", "new", "1", "--foreground"),
        _module_args("reindex-queue", "worker"),
        ["/venv/bin/codev-platform", "reindex-queue", "drain-once"],
        _module_args("graph", "ingest"),
        ["/venv/bin/codev-platform", "graph", "ingest"],
        _direct_module_args("codev_platform.chroma.indexer"),
        _direct_module_args("codev_platform.chroma.indexer", "--force"),
        _direct_module_args("codev_platform.recall.code_vector_store", "--project", "demo"),
        ["/usr/local/bin/codegraph", "sync"],
        ["/usr/bin/node", "/usr/bin/codegraph", "sync"],
        [
            "/usr/bin/node",
            "/usr/lib/node_modules/@colbymchenry/codegraph/npm-shim.js",
            "sync",
        ],
        [
            "/opt/codegraph/runtime/node",
            "--liftoff-only",
            "/opt/codegraph/lib/dist/bin/codegraph.js",
            "serve",
            "--mcp",
            "--path",
            "/srv/project",
        ],
        ["/usr/local/bin/codegraph", "index"],
        ["/usr/local/bin/codegraph", "init"],
        ["/usr/local/bin/codegraph", "uninit"],
        ["/usr/local/bin/codegraph", "serve", "--mcp"],
        ["/usr/bin/node", "/usr/bin/codegraph", "serve", "--mcp"],
        ["/usr/local/bin/codegraph", "serve", "--mcp", "--no-watch"],
    ],
)
def test_宽写入者证明拒绝systemd外旧版与新版写入口(tmp_path, arguments) -> None:
    """维护迁移必须阻断所有已知的直接索引写入入口。"""
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_writers,
    )

    _process(tmp_path, 335, arguments, "0::/user.slice/user-1000.slice\n")

    with pytest.raises(ExternalReindexWorkerError) as raised:
        assert_no_external_reindex_writers(proc_root=tmp_path)

    assert "335" not in str(raised.value)


def test_宽写入者证明拒绝CodeGraph代理的python父进程(tmp_path) -> None:
    """`python -m codev_platform.codegraph.server` 尚未 exec 时也必须被识别。"""
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_writers,
    )

    _process(
        tmp_path,
        348,
        _direct_module_args("codev_platform.codegraph.server", "--http", "--port", "18091"),
        "0::/user.slice/user-1000.slice\n",
    )

    with pytest.raises(ExternalReindexWorkerError):
        assert_no_external_reindex_writers(proc_root=tmp_path)


@pytest.mark.parametrize(
    ("runtime_flag", "runtime_value"),
    [
        ("--require", "/opt/codegraph/preload.cjs"),
        ("--loader", "/opt/codegraph/loader.mjs"),
    ],
)
def test_宽写入者证明拒绝node带值前置参数的codegraph守护进程(
    tmp_path,
    runtime_flag,
    runtime_value,
) -> None:
    """Node 带值前置参数不能遮蔽后续的 CodeGraph daemon 命令。"""
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_writers,
    )

    _process(
        tmp_path,
        346,
        [
            "/opt/codegraph/runtime/node",
            runtime_flag,
            runtime_value,
            "--liftoff-only",
            "/opt/codegraph/lib/dist/bin/codegraph.js",
            "serve",
            "--mcp",
            "--path",
            "/srv/project",
        ],
        "0::/user.slice/user-1000.slice\n",
    )

    with pytest.raises(ExternalReindexWorkerError):
        assert_no_external_reindex_writers(proc_root=tmp_path)


def test_宽写入者证明不把非前台hook误判为写进程(tmp_path) -> None:
    """普通 hook 只入队，不应因维护扫描造成误杀。"""
    from codev_platform.reindex.external_worker_guard import (
        assert_no_external_reindex_writers,
    )

    _process(tmp_path, 336, _module_args("post-commit"), "0::/user.slice/user-1000.slice\n")
    _process(tmp_path, 337, _module_args("post-merge"), "0::/user.slice/user-1000.slice\n")
    _process(
        tmp_path,
        338,
        _module_args("post-checkout", "old", "new", "1"),
        "0::/user.slice/user-1000.slice\n",
    )

    assert_no_external_reindex_writers(proc_root=tmp_path)


def test_宽写入者证明不把chroma只读演练误判为写进程(tmp_path) -> None:
    """Chroma dry-run 不改索引，维护扫描不应阻断其演练用途。"""
    from codev_platform.reindex.external_worker_guard import (
        assert_no_external_reindex_writers,
    )

    _process(
        tmp_path,
        340,
        _direct_module_args("codev_platform.chroma.indexer", "--dry-run"),
        "0::/user.slice/user-1000.slice\n",
    )

    assert_no_external_reindex_writers(proc_root=tmp_path)


def test_宽写入者证明不把codegraph状态查询误判为写进程(tmp_path) -> None:
    """CodeGraph status 不写索引，维护扫描不应阻断只读诊断。"""
    from codev_platform.reindex.external_worker_guard import (
        assert_no_external_reindex_writers,
    )

    _process(
        tmp_path,
        341,
        ["/usr/local/bin/codegraph", "status"],
        "0::/user.slice/user-1000.slice\n",
    )
    _process(
        tmp_path,
        344,
        ["/bin/bash", "sync"],
        "0::/user.slice/user-1000.slice\n",
    )
    _process(
        tmp_path,
        347,
        [
            "/usr/bin/node",
            "/srv/ordinary-worker.js",
            "/opt/codegraph/lib/dist/bin/codegraph.js",
            "serve",
            "--mcp",
        ],
        "0::/user.slice/user-1000.slice\n",
    )

    assert_no_external_reindex_writers(proc_root=tmp_path)


@pytest.mark.parametrize(
    "arguments",
    [
        ["/usr/local/bin/codegraph", "serve", "--mcp", "--no-watch"],
        ["/usr/bin/node", "/usr/bin/codegraph", "serve", "--mcp", "--no-watch"],
        [
            "/bin/sh",
            "/usr/lib/node_modules/@colbymchenry/codegraph/node_modules/"
            "@colbymchenry/codegraph-linux-x64/bin/codegraph",
            "serve",
            "--mcp",
            "--no-watch",
        ],
    ],
)
def test_维护窗口拒绝带禁watch参数的codegraph服务(tmp_path, arguments) -> None:
    """CodeGraph 0.9.7 首次查询仍会追赶同步，不能按只读放行。"""
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_writers,
    )

    _process(
        tmp_path,
        342,
        arguments,
        "0::/user.slice/user-1000.slice\n",
        {"CODEGRAPH_NO_WATCH": "1", "CODEGRAPH_NO_DAEMON": "1"},
    )

    with pytest.raises(ExternalReindexWorkerError):
        assert_no_external_reindex_writers(proc_root=tmp_path)


def test_旧worker证明保持只扫描worker而非全部写入口(tmp_path) -> None:
    """保留窄 worker API，调用方迁移到新证明前语义不漂移。"""
    from codev_platform.reindex.external_worker_guard import (
        assert_no_external_reindex_workers,
    )

    _process(tmp_path, 339, _module_args("reindex"), "0::/user.slice/user-1000.slice\n")

    assert_no_external_reindex_workers(proc_root=tmp_path)


def test_宽写入者候选缺少cgroup时失败关闭(tmp_path) -> None:
    """任一识别到的写入口若无法证明归属，就不得开始维护迁移。"""
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_writers,
    )

    directory = tmp_path / "445"
    directory.mkdir()
    directory.joinpath("cmdline").write_bytes(
        b"\0".join(part.encode() for part in _module_args("reindex")) + b"\0"
    )

    with pytest.raises(ExternalReindexWorkerError):
        assert_no_external_reindex_writers(proc_root=tmp_path)


def test_证明在proc扫描不完整时失败关闭(tmp_path) -> None:
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_workers,
    )

    _process(tmp_path, 1, ["/bin/true"], "0::/user.slice\n")

    with pytest.raises(ExternalReindexWorkerError):
        assert_no_external_reindex_workers(proc_root=tmp_path, max_processes=0)


def test_证明遇到候选worker缺失cgroup时失败关闭(tmp_path) -> None:
    from codev_platform.reindex.external_worker_guard import (
        ExternalReindexWorkerError,
        assert_no_external_reindex_workers,
    )

    directory = tmp_path / "444"
    directory.mkdir()
    directory.joinpath("cmdline").write_bytes(b"\0".join(part.encode() for part in _worker_args()))

    with pytest.raises(ExternalReindexWorkerError):
        assert_no_external_reindex_workers(proc_root=tmp_path)


def test_当前进程属于codev_reindex_unit_cgroup时允许维护窗口内启动(tmp_path) -> None:
    from codev_platform.reindex.external_worker_guard import (
        current_process_in_reindex_unit_cgroup,
    )

    cgroup = tmp_path / "self-cgroup"
    cgroup.write_text(
        "0::/system.slice/codev-reindex.service/reindex-attempt.scope\n",
        encoding="utf-8",
    )

    assert current_process_in_reindex_unit_cgroup(cgroup_path=cgroup) is True


def test_当前进程属于指定codegraph_unit_cgroup时可被精确证明(tmp_path) -> None:
    from codev_platform.reindex.external_worker_guard import (
        current_process_in_systemd_unit_cgroup,
    )

    cgroup = tmp_path / "self-cgroup"
    cgroup.write_text(
        "0::/system.slice/codev-mcp-codegraph.service/codegraph-session.scope\n",
        encoding="utf-8",
    )

    assert current_process_in_systemd_unit_cgroup(
        "codev-mcp-codegraph.service",
        cgroup_path=cgroup,
    ) is True


@pytest.mark.parametrize(
    ("unit", "content"),
    [
        ("codev-mcp-codegraph.service", "0::/system.slice/codev-mcp-codegraph.service-old\n"),
        ("../codev-mcp-codegraph.service", "0::/system.slice/codev-mcp-codegraph.service\n"),
    ],
)
def test_指定unit不精确或名称不安全时当前进程证明失败关闭(
    tmp_path,
    unit,
    content,
) -> None:
    from codev_platform.reindex.external_worker_guard import (
        current_process_in_systemd_unit_cgroup,
    )

    cgroup = tmp_path / "self-cgroup"
    cgroup.write_text(content, encoding="utf-8")

    assert current_process_in_systemd_unit_cgroup(unit, cgroup_path=cgroup) is False


@pytest.mark.parametrize(
    "content",
    [
        "0::/user.slice/user-1000.slice\n",
        "invalid-cgroup-line\n",
    ],
)
def test_当前进程不属于受控unit或cgroup损坏时失败关闭(tmp_path, content) -> None:
    from codev_platform.reindex.external_worker_guard import (
        current_process_in_reindex_unit_cgroup,
    )

    cgroup = tmp_path / "self-cgroup"
    cgroup.write_text(content, encoding="utf-8")

    assert current_process_in_reindex_unit_cgroup(cgroup_path=cgroup) is False


def test_当前进程cgroup无法读取时失败关闭(tmp_path) -> None:
    from codev_platform.reindex.external_worker_guard import (
        current_process_in_reindex_unit_cgroup,
    )

    assert current_process_in_reindex_unit_cgroup(
        cgroup_path=tmp_path / "missing-cgroup"
    ) is False
