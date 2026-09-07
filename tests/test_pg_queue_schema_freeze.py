"""PG 队列 schema 冻结与无秘密绑定边界测试。"""
from __future__ import annotations

import threading
import sys
from types import ModuleType

import pytest

from tests.pg_queue_fakes import (
    FakeConn,
    FakePool,
    TEST_TABLE,
    schema_verification_responses,
)


def _unfrozen_queue(*responses: object):
    """构造尚未连接、尚未冻结 schema 的最小 Pg queue。"""
    from codev_platform.reindex.pg_queue import PgJobQueue

    queue = PgJobQueue.__new__(PgJobQueue)
    queue._bare_table = TEST_TABLE
    queue._binding_base = "pg-v2-base|host=db.example|port=5432|db=reindex"
    queue._pool = FakePool(FakeConn(responses))
    queue._ensure_lock = threading.Lock()
    queue._pool_started = True
    queue._opened = False
    return queue


def _exception_chain_text(error: BaseException) -> str:
    """收集 cause/context，确保脱敏不只依赖 traceback 渲染规则。"""
    pending = [error]
    seen: set[int] = set()
    fragments: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        fragments.extend((str(current), repr(current)))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return "\n".join(fragments)


def _install_connection_pool(monkeypatch, connection_pool: type) -> None:
    """让构造器单测不依赖本机 psycopg_pool 安装与连接状态。"""
    module = ModuleType("psycopg_pool")
    module.ConnectionPool = connection_pool
    monkeypatch.setitem(sys.modules, "psycopg_pool", module)


def test_构造期仅保存基础描述符与裸表(monkeypatch) -> None:
    from codev_platform.reindex.pg_queue import PgJobQueue

    class _ConnectionPool:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    _install_connection_pool(monkeypatch, _ConnectionPool)

    queue = PgJobQueue(
        "postgresql://user:secret@db.example/reindex",
        table=TEST_TABLE,
    )

    assert queue._binding_base == "pg-v2-base|host=db.example|port=5432|db=reindex"
    assert queue._bare_table == TEST_TABLE
    assert not hasattr(queue, "_backend_locator")
    assert not hasattr(queue, "_t")


def test_连接池构造异常的完整异常链不含dsn(monkeypatch) -> None:
    from codev_platform.reindex.pg_queue import PgJobQueue, PgQueueInitializationError

    secret_dsn = "postgresql://user:top-secret@db.example/reindex"

    class _ConnectionPool:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError(f"连接池创建失败: {secret_dsn}")

    _install_connection_pool(monkeypatch, _ConnectionPool)

    with pytest.raises(PgQueueInitializationError) as raised:
        PgJobQueue(secret_dsn, table=TEST_TABLE)

    assert "top-secret" not in _exception_chain_text(raised.value)
    assert "postgresql://" not in _exception_chain_text(raised.value)


def test_ensure只读取一次current_schema并冻结限定表与绑定() -> None:
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget

    queue = _unfrozen_queue(*schema_verification_responses(schema="tenant_a"))

    queue._ensure(PgOperationBudget.start(2.0))

    assert queue._t == '"tenant_a"."reindex_jobs_test"'
    assert queue.owner_binding_locator() == (
        "pg-v2|host=db.example|port=5432|db=reindex|"
        "schema=tenant_a|table=reindex_jobs_test"
    )
    assert [sql for sql, _params in queue._pool._conn.calls if sql == "SELECT current_schema()"] == [
        "SELECT current_schema()"
    ]
    contract_calls = queue._pool._conn.calls[1:-1]
    assert all(params[0] == "tenant_a" for _sql, params in contract_calls)
    assert all(
        len(params) == 1 or params[1] == "reindex_jobs_test"
        for _sql, params in contract_calls
    )
    assert '"tenant_a"."reindex_jobs_test"' in queue._pool._conn.calls[-1][0]
    assert not any(
        sql.lstrip().upper().startswith(("CREATE ", "ALTER "))
        for sql, _params in queue._pool._conn.calls
    )

    queue._opened = False
    queue._pool._conn.responses.extend(
        schema_verification_responses(schema="tenant_a", include_schema=False)
    )
    queue._ensure(PgOperationBudget.start(2.0))

    assert [sql for sql, _params in queue._pool._conn.calls if sql == "SELECT current_schema()"] == [
        "SELECT current_schema()"
    ]


def test_owner_binding_locator在schema冻结前拒绝() -> None:
    queue = _unfrozen_queue()

    with pytest.raises(ValueError, match="冻结"):
        queue.owner_binding_locator()


def test_queue_backend_binding受控触发schema冻结() -> None:
    from codev_platform.reindex.queue_backend_binding import queue_backend_binding

    queue = _unfrozen_queue(*schema_verification_responses(schema="tenant_a"))

    binding = queue_backend_binding(queue)

    assert binding.kind == "pg"
    assert queue._t == '"tenant_a"."reindex_jobs_test"'
    assert queue.owner_binding_locator().endswith("schema=tenant_a|table=reindex_jobs_test")


def test_queue_backend_binding冻结失败时异常链不含dsn() -> None:
    from codev_platform.reindex.queue_backend_binding import (
        QueueBackendBindingError,
        queue_backend_binding,
    )

    secret_dsn = "postgresql://user:top-secret@db.example/reindex"
    queue = _unfrozen_queue(RuntimeError(f"连接失败: {secret_dsn}"))

    with pytest.raises(QueueBackendBindingError) as raised:
        queue_backend_binding(queue)

    assert "top-secret" not in _exception_chain_text(raised.value)
    assert "postgresql://" not in _exception_chain_text(raised.value)


@pytest.mark.parametrize(
    "dsn",
    (
        "postgresql://user:secret@db.example/reindex?options=-c%20search_path%3Dother",
        "postgresql://user:secret@db.example/reindex?search_path=other",
        "postgresql://user:secret@db.example/reindex?hostaddr=127.0.0.1",
        "postgresql://user:secret@db.example/reindex?service=shared",
    ),
)
def test_基础描述符拒绝改变schema或连接目标的选项(dsn: str) -> None:
    from codev_platform.reindex.pg_queue_binding import pg_binding_base_locator

    with pytest.raises(ValueError):
        pg_binding_base_locator(dsn)


@pytest.mark.parametrize("key", ("options", "search_path", "hostaddr", "service"))
def test_keyword连接串拒绝危险目标覆盖项(monkeypatch, key: str) -> None:
    import codev_platform.reindex.pg_queue_binding as binding_module
    from codev_platform.reindex.pg_queue_binding import pg_binding_base_locator

    values = {"host": "db.example", "port": "5432", "dbname": "reindex", key: "unsafe"}
    monkeypatch.setattr(binding_module, "_conninfo_to_dict", lambda _dsn: values)

    with pytest.raises(ValueError):
        pg_binding_base_locator("host=db.example password=secret")


def test_keyword连接串拒绝重复host但不误判密码中的host文本() -> None:
    from codev_platform.reindex.pg_queue_binding import pg_binding_base_locator

    with pytest.raises(ValueError):
        pg_binding_base_locator("host=db-a host=db-b port=5432 dbname=reindex")

    assert pg_binding_base_locator(
        "host=db.example port=5432 dbname=reindex password='not a host=target'"
    ) == "pg-v2-base|host=db.example|port=5432|db=reindex"


def test_keyword连接串拒绝dbname与database双数据库目标(monkeypatch) -> None:
    import codev_platform.reindex.pg_queue_binding as binding_module
    from codev_platform.reindex.pg_queue_binding import pg_binding_base_locator

    monkeypatch.setattr(
        binding_module,
        "_conninfo_to_dict",
        lambda _dsn: {"host": "db.example", "port": "5432", "dbname": "reindex"},
    )
    with pytest.raises(ValueError):
        pg_binding_base_locator(
            "host=db.example port=5432 dbname=reindex database=other"
        )


def test_schema探测异常的完整异常链不含dsn() -> None:
    from codev_platform.reindex.pg_queue import PgQueueInitializationError
    from codev_platform.reindex.pg_queue_tx import PgOperationBudget

    secret_dsn = "postgresql://user:top-secret@db.example/reindex"
    queue = _unfrozen_queue(RuntimeError(f"连接失败: {secret_dsn}"))

    with pytest.raises(PgQueueInitializationError) as raised:
        queue._ensure(PgOperationBudget.start(2.0))

    assert "top-secret" not in _exception_chain_text(raised.value)
    assert "postgresql://" not in _exception_chain_text(raised.value)
