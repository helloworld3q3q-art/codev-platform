"""稳定 queue owner 的后端绑定测试。"""
from __future__ import annotations

import pytest

from codev_platform.reindex.file_queue import FileSpoolQueue
from codev_platform.reindex.pg_queue import PgJobQueue
from codev_platform.reindex.runtime_owner import fingerprint_backend


def test_file_binding_uses_canonical_spool_location(tmp_path) -> None:
    from codev_platform.reindex.queue_backend_binding import queue_backend_binding

    queue = FileSpoolQueue(tmp_path / "spool")

    binding = queue_backend_binding(queue, {})

    assert binding.kind == "file"
    assert binding.fingerprint == fingerprint_backend("file", str(queue.location.resolve()))


def test_pg绑定只使用已冻结实例的无秘密描述符而不读取当前配置() -> None:
    from codev_platform.reindex.queue_backend_binding import queue_backend_binding
    from codev_platform.reindex.pg_queue_binding import FrozenPgQueueBinding

    queue = object.__new__(PgJobQueue)
    locator = (
        "pg-v2|host=db.example|port=5432|db=reindex|"
        "schema=tenant_a|table=reindex_jobs"
    )
    queue._backend_locator = locator
    queue._frozen_binding = FrozenPgQueueBinding(
        locator=locator,
        schema="tenant_a",
        table="reindex_jobs",
        qualified_table='"tenant_a"."reindex_jobs"',
        qualified_index='"tenant_a"."ix_reindex_jobs_claim"',
    )
    queue._opened = True

    binding = queue_backend_binding(queue)

    assert binding.kind == "pg"
    assert binding.fingerprint == fingerprint_backend("pg", locator)
    assert "secret" not in repr(binding)


def test_pg绑定拒绝缺少实例描述符且不回显配置() -> None:
    from codev_platform.reindex.queue_backend_binding import (
        QueueBackendBindingError,
        queue_backend_binding,
    )

    queue = object.__new__(PgJobQueue)
    queue._opened = True

    with pytest.raises(QueueBackendBindingError) as raised:
        queue_backend_binding(queue)
    assert "dsn" not in str(raised.value).lower()


def test_pg基础描述符规范化连接目标并拒绝多host与危险选项(monkeypatch) -> None:
    import codev_platform.reindex.pg_queue_binding as binding_module
    from codev_platform.reindex.pg_queue_binding import pg_binding_base_locator

    assert pg_binding_base_locator(
        "postgresql://user:secret@Db.Example:5544/reindex",
    ) == "pg-v2-base|host=db.example|port=5544|db=reindex"

    for dsn in (
        "postgresql://user:secret@db-a,db-b/reindex",
        "postgresql://user:secret@/reindex?host=/var/run/postgresql",
        "postgresql://user:secret@db.example/reindex?service=shared",
        "postgresql://user:secret@db.example/reindex?options=-c%20search_path%3Dother",
        "postgresql://user:secret@db.example/reindex?search_path=other",
    ):
        with pytest.raises(ValueError):
            pg_binding_base_locator(dsn)

    monkeypatch.setattr(
        binding_module,
        "_conninfo_to_dict",
        lambda _dsn: {
            "host": "Db.Example",
            "port": "5544",
            "dbname": "reindex",
            "user": "user",
            "password": "secret",
        },
    )
    assert pg_binding_base_locator(
        "host=Db.Example port=5544 dbname=reindex user=user password=secret",
    ) == "pg-v2-base|host=db.example|port=5544|db=reindex"


@pytest.mark.parametrize(
    "values",
    [
        {"host": "db-a,db-b", "port": "5432", "dbname": "reindex"},
        {"host": "db.example", "port": "5432", "dbname": "reindex", "service": "shared"},
        {"host": "db.example", "hostaddr": "127.0.0.1", "port": "5432", "dbname": "reindex"},
    ],
)
def test_pg_keyword基础描述符拒绝多host_service与hostaddr(monkeypatch, values) -> None:
    import codev_platform.reindex.pg_queue_binding as binding_module
    from codev_platform.reindex.pg_queue_binding import pg_binding_base_locator

    monkeypatch.setattr(binding_module, "_conninfo_to_dict", lambda _dsn: values)

    with pytest.raises(ValueError):
        pg_binding_base_locator("host=private password=secret")


def test_unknown_queue_type_fails_closed() -> None:
    from codev_platform.reindex.queue_backend_binding import (
        QueueBackendBindingError,
        queue_backend_binding,
    )

    with pytest.raises(QueueBackendBindingError):
        queue_backend_binding(object())
