"""本地 hook 生产者跨 Windows/WSL 数据边界的路由门禁。"""

from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.reindex.producer_route import (
    LocalHookRelayRequired,
    effective_queue_backend,
    require_local_hook_queue_route,
)


@pytest.mark.parametrize(
    "queue_root",
    [
        Path(r"\\wsl.localhost\Ubuntu\home\demo\data\reindex_queue"),
        Path(r"\\wsl$\Ubuntu\home\demo\data\reindex_queue"),
        Path(r"\\?\UNC\wsl.localhost\Ubuntu\home\demo\data\reindex_queue"),
    ],
)
def test_windows_file_queue_on_wsl_unc_requires_git_hook_relay(queue_root):
    with pytest.raises(LocalHookRelayRequired) as captured:
        require_local_hook_queue_route(
            {"reindex": {"queue_backend": "file"}},
            platform_name="nt",
            queue_root=queue_root,
        )

    message = str(captured.value)
    assert "git hook run post-commit" in message
    assert "codev-platform post-commit" in message
    assert str(queue_root) not in message


@pytest.mark.parametrize(
    ("platform_name", "backend", "queue_root"),
    [
        ("posix", "file", Path("/home/demo/data/reindex_queue")),
        ("nt", "file", Path(r"D:\data\reindex_queue")),
        ("nt", "pg", Path(r"\\wsl.localhost\Ubuntu\home\demo\data\reindex_queue")),
    ],
)
def test_supported_local_hook_queue_routes_are_allowed(platform_name, backend, queue_root):
    require_local_hook_queue_route(
        {"reindex": {"queue_backend": backend}},
        platform_name=platform_name,
        queue_root=queue_root,
    )


@pytest.mark.parametrize("configured", ["file ", "FILE", "typo", ""])
def test_effective_file_fallback_cannot_bypass_wsl_owner_guard(configured):
    cfg = {"reindex": {"queue_backend": configured}}
    assert effective_queue_backend(cfg) == "file"

    with pytest.raises(LocalHookRelayRequired):
        require_local_hook_queue_route(
            cfg,
            platform_name="nt",
            queue_root=Path(r"\\wsl.localhost\Ubuntu\srv\codev\data\reindex_queue"),
        )


def test_pg_backend_whitespace_is_normalized_without_file_guard():
    cfg = {"reindex": {"queue_backend": " PG "}}
    assert effective_queue_backend(cfg) == "pg"
    require_local_hook_queue_route(
        cfg,
        platform_name="nt",
        queue_root=Path(r"\\wsl.localhost\Ubuntu\srv\codev\data\reindex_queue"),
    )
