"""中性 cgroup.events 解析器的契约与依赖方向测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.core.cgroup_events import parse_cgroup_events
from codev_platform.reindex.cgroup_process import (
    parse_cgroup_events as process_parse_cgroup_events,
)
from codev_platform.reindex.cgroup_v2 import (
    parse_cgroup_events as compatibility_parse_cgroup_events,
)


def test_cgroup_events解析器接受合法populated值和扩展字段() -> None:
    assert parse_cgroup_events(b"populated 0\nfrozen 0\n") is False
    assert parse_cgroup_events(b"populated 1\nfrozen 0\n") is True
    assert parse_cgroup_events(b"populated 1\nfuture_key 42\n") is True


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"frozen 0\n",
        b"populated 2\n",
        b"populated yes\n",
        b"populated 0\npopulated 1\n",
        b"populated 0 extra\n",
        b"populated 0\ninvalid-key 1\n",
        b"populated 0\n\xff 1\n",
        b"populated 0\n" + b"x" * (1024 * 1024),
    ],
    ids=[
        "empty",
        "missing-populated",
        "invalid-populated",
        "nonnumeric",
        "duplicate",
        "extra-column",
        "invalid-key",
        "non-ascii",
        "oversized",
    ],
)
def test_cgroup_events解析器拒绝不完整或不可信输入(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_cgroup_events(raw)


@pytest.mark.parametrize(
    "raw",
    [None, "populated 0\n", bytearray(b"populated 0\n")],
    ids=["none", "str", "bytearray"],
)
def test_cgroup_events解析器只接受原生bytes(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_cgroup_events(raw)  # type: ignore[arg-type]


def test_reindex旧入口兼容重导出中性解析器() -> None:
    assert compatibility_parse_cgroup_events is parse_cgroup_events
    assert process_parse_cgroup_events is parse_cgroup_events


@pytest.mark.parametrize(
    "relative_path",
    [
        "codev_platform/runtime_deployment_quiesce.py",
        "codev_platform/ops/reindex_admin_systemd_guard.py",
    ],
)
def test_非reindex消费者只从中性core导入解析器(relative_path: str) -> None:
    root = Path(__file__).parents[1]
    source = (root / relative_path).read_text(encoding="utf-8")

    assert "from codev_platform.core.cgroup_events import parse_cgroup_events" in source
    assert "from codev_platform.reindex.cgroup_v2 import parse_cgroup_events" not in source


def test_部署叶子门面不再自行解析cgroup且不反向依赖reindex() -> None:
    root = Path(__file__).parents[1]
    source = (root / "codev_platform/runtime_transient_service.py").read_text(encoding="utf-8")

    assert "runtime_managed_process" in source
    assert "parse_cgroup_events" not in source
    assert "codev_platform.reindex" not in source
