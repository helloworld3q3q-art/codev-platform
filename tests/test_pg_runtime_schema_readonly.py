"""业务 PG Store 运行期禁止 DDL 的回归门禁。"""

from __future__ import annotations

import inspect

import pytest

from codev_platform.agent import memory_store_pg, rbac_store_pg, session_pg
from codev_platform.gateway import token_store_pg
from codev_platform.graph import pg_store
from codev_platform.web.repositories import account_store_pg


@pytest.mark.parametrize(
    "module",
    (
        rbac_store_pg,
        account_store_pg,
        token_store_pg,
        pg_store,
        memory_store_pg,
        session_pg,
    ),
)
def test_runtime_store_source_contains_no_ddl(module) -> None:
    source = inspect.getsource(module).upper()

    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "METADATA.CREATE_ALL" not in source
