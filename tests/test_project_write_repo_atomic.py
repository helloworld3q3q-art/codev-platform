"""ProjectWriteRepository.register_project 原子排他写测试。

已存在同 code 的 meta.json -> 抛 PlatformError(INVALID_PARAMS), 绝不覆盖原内容。
"""
from __future__ import annotations

import json

import pytest

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.web.repositories.project_write_repo import ProjectWriteRepository


def test_register_then_reregister_same_code_raises_and_keeps_original(tmp_path):
    repo = ProjectWriteRepository(meta_dir=tmp_path)

    first = repo.register_project(
        code="p1", name="A", repo_path=None, description=None, org_id="o1",
    )
    assert first["code"] == "p1"
    assert first["name"] == "A"
    assert first["orgId"] == "o1"

    with pytest.raises(PlatformError) as exc_info:
        repo.register_project(
            code="p1", name="B", repo_path=None, description=None, org_id="o2",
        )
    assert exc_info.value.code is ErrorCode.INVALID_PARAMS

    # 原 meta.json 未被覆盖: 仍是 A / o1。
    meta = json.loads((tmp_path / "p1" / "meta.json").read_text(encoding="utf-8"))
    assert meta["display_name"] == "A"
    assert meta["org_id"] == "o1"
