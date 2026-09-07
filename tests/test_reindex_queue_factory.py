"""默认队列工厂的严格后端与敏感信息边界测试。"""
from __future__ import annotations

import pytest

from codev_platform.reindex.producer_route import LocalHookRelayRequired


def test_严格pg初始化失败不回显_dsn(monkeypatch) -> None:
    from codev_platform.reindex import open_default_queue
    import codev_platform.reindex.pg_queue as pg_queue

    secret_dsn = "postgresql://user:top-secret@db.example/reindex"

    class _BrokenPgQueue:
        def __init__(self, _dsn: str) -> None:
            pass

        def probe(self) -> None:
            raise RuntimeError(f"连接失败: {secret_dsn}")

    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: {"reindex": {"queue_backend": "pg"}, "memory": {"pg_dsn": secret_dsn}},
    )
    monkeypatch.setattr(pg_queue, "PgJobQueue", _BrokenPgQueue)

    with pytest.raises(RuntimeError) as captured:
        open_default_queue(fail_soft=False)

    assert secret_dsn not in str(captured.value)
    assert "RuntimeError" in str(captured.value)


def test_file_queue_factory_always_applies_runtime_owner_guard(monkeypatch, tmp_path) -> None:
    from codev_platform.reindex import open_default_queue
    from codev_platform.reindex import producer_route

    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: {"reindex": {"queue_backend": "typo"}},
    )
    monkeypatch.setattr("codev_platform.reindex.spool_dir", lambda: tmp_path / "queue")
    calls: list[tuple[object, str | None]] = []

    def reject(_cfg, *, queue_root, selected_backend):
        calls.append((queue_root, selected_backend))
        raise LocalHookRelayRequired("owner required")

    monkeypatch.setattr(producer_route, "require_local_hook_queue_route", reject)

    with pytest.raises(LocalHookRelayRequired, match="owner required"):
        open_default_queue()

    assert calls == [(tmp_path / "queue", "file")]
