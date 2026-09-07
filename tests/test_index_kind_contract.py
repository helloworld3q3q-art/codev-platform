"""生产四库契约与执行注册表的一致性门禁。"""

from pathlib import Path

import pytest

from codev_platform.index_kind_contract import REQUIRED_INDEX_KINDS
from codev_platform.index_manifest import KNOWN_KINDS
from codev_platform.reindex import runners
from codev_platform.reindex.runner_proof import proof_policy_kinds


def test_持久化兼容别名与中立生产契约保持同一对象() -> None:
    assert KNOWN_KINDS is REQUIRED_INDEX_KINDS


def test_生产契约中的每类索引都有唯一runner() -> None:
    assert runners.kinds() == REQUIRED_INDEX_KINDS
    assert all(runners.get_runner(kind) is not None for kind in REQUIRED_INDEX_KINDS)


def test_生产契约中的每类索引都有同序成功证明策略() -> None:
    assert proof_policy_kinds() == REQUIRED_INDEX_KINDS


def test_runner注册表拒绝同名覆盖(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Runner:
        kind = "chroma"

        def run(self, _project_id: str, _repo: Path, _cfg: dict) -> int:
            return 0

    monkeypatch.setattr(runners, "_REGISTRY", {})
    runners.register(_Runner())

    with pytest.raises(ValueError, match="重复"):
        runners.register(_Runner())
