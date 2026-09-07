"""运行时分域 store 的公共输入错误边界回归。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.runtime_generation_store import (
    RuntimeGenerationStore,
    RuntimeGenerationStoreError,
)
from codev_platform.runtime_store_protocols import RuntimeStorePolicy
from codev_platform.runtime_transaction_controlled_store import (
    RuntimeTransactionControlledStoreError,
    RuntimeTransactionStore,
)


def _unexpected_gate(*_args: object, **_kwargs: object) -> object:
    """无效对象必须在进入 gate 前被公共边界拒绝。"""
    raise AssertionError("无效公共输入不应进入 control gate")


def _stores(tmp_path: Path) -> tuple[RuntimeGenerationStore, RuntimeTransactionStore]:
    """构造只用于预编码错误测试的最小合法 store 依赖。"""
    root = tmp_path / "runtime"
    root.mkdir()
    policy = RuntimeStorePolicy(root=root, owner_uid=0)
    generation = RuntimeGenerationStore(
        policy,
        SimpleNamespace(mutation=_unexpected_gate),
    )
    transaction = RuntimeTransactionStore(
        policy,
        SimpleNamespace(
            mutation=_unexpected_gate,
            terminal_cleanup=_unexpected_gate,
        ),
    )
    return generation, transaction


@pytest.mark.parametrize(
    "invoke",
    (
        lambda store: store.write_generation_once(object(), proof=object()),
        lambda store: store.write_serving_fence_once(object(), proof=object()),
        lambda store: store.write_acceptance_once(
            object(),
            fence=object(),
            proof=object(),
        ),
        lambda store: store.write_serving_permit_once(
            object(),
            target_state=object(),
            proof=object(),
        ),
    ),
)
def testgeneration公共预编码错误稳定映射为store错误(
    tmp_path: Path,
    invoke: object,
) -> None:
    """领域编码失败不能泄露到 generation store 的公开调用方。"""
    generation, _transaction = _stores(tmp_path)

    with pytest.raises(RuntimeGenerationStoreError):
        invoke(generation)


@pytest.mark.parametrize(
    "invoke",
    (
        lambda store: store.write_attempt_once(object(), proof=object()),
        lambda store: store.write_journal(
            object(),
            proof=object(),
            expected_sha256=None,
        ),
        lambda store: store.write_envelope_once(object(), proof=object()),
        lambda store: store.write_terminal_evidence_once(object(), proof=object()),
    ),
)
def testtransaction公共预编码错误稳定映射为store错误(
    tmp_path: Path,
    invoke: object,
) -> None:
    """领域编码失败不能泄露到 transaction store 的公开调用方。"""
    _generation, transaction = _stores(tmp_path)

    with pytest.raises(RuntimeTransactionControlledStoreError):
        invoke(transaction)
