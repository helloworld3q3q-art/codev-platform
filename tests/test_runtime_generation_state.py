"""运行代际状态模型、codec 与公开入口门禁测试。"""

from __future__ import annotations

import dataclasses
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from codev_platform.runtime_fencing import control_lease_record_sha256
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    GenerationStateError,
    begin_switch,
    begin_validation,
    commit_serving,
    decode_generation_state,
    encode_generation_state,
    generation_state_sha256,
    mark_restricted,
    mark_safety_unproven,
)
from tests.runtime_contract_malicious_support import strict_json_mutations
from tests.runtime_generation_state_test_support import (
    _attempt,
    _control,
    _lifecycle,
    _steady,
    _target_acceptance,
    _target_fence,
)


def test_generation_state字段冻结且不存在通用替换旁路() -> None:
    assert tuple(mode.value for mode in GenerationMode) == (
        "steady",
        "switching",
        "validating",
        "restricted",
        "safety_unproven",
    )
    assert tuple(field.name for field in dataclasses.fields(GenerationState)) == (
        "schema_version",
        "state_version",
        "mode",
        "serving_generation_id",
        "serving_fence_id",
        "serving_fence_epoch",
        "serving_fence_token_sha256",
        "desired_generation_id",
        "rollback_generation_id",
        "control_attempt_id",
        "control_reservation_sha256",
        "control_lease_record_sha256",
        "control_lease_epoch",
        "acceptance_sha256",
        "maintenance_active",
        "updated_at",
    )
    assert GenerationState.__dataclass_params__.frozen is True
    assert "__dict__" not in GenerationState.__slots__
    assert not hasattr(
        __import__(
            "codev_platform.runtime_generation_state",
            fromlist=["replace_state"],
        ),
        "replace_state",
    )


@pytest.mark.parametrize(
    "changes",
    (
        {"schema_version": True},
        {"schema_version": 2},
        {"state_version": True},
        {"state_version": 0},
        {"state_version": 2**63},
        {"mode": "steady"},
        {"serving_generation_id": "0" * 64},
        {"serving_fence_id": "bad/fence"},
        {"serving_fence_epoch": 0},
        {"serving_fence_token_sha256": "A" * 64},
        {"acceptance_sha256": None},
        {"maintenance_active": 0},
        {"updated_at": "2026-07-19T10:00:00+00:00"},
        {"updated_at": "2026-02-30T10:00:00Z"},
        {"control_attempt_id": "d" * 32},
        {"control_lease_record_sha256": "4" * 64},
        {"control_lease_epoch": 4},
        {"desired_generation_id": "d" * 64},
        {"rollback_generation_id": None},
        {"rollback_generation_id": "a" * 64},
    ),
)
def test_generation_state非法字段与不完整绑定fail_closed(
    changes: dict[str, object],
) -> None:
    with pytest.raises(GenerationStateError):
        _steady(**changes)


@pytest.mark.parametrize(
    "mode",
    (
        GenerationMode.SWITCHING,
        GenerationMode.VALIDATING,
        GenerationMode.RESTRICTED,
        GenerationMode.SAFETY_UNPROVEN,
    ),
)
def test_generation_state非公开稳态必须关闭维护门禁(mode: GenerationMode) -> None:
    with pytest.raises(GenerationStateError, match="维护"):
        _steady(mode=mode)


@pytest.mark.parametrize(
    "mode",
    (GenerationMode.RESTRICTED, GenerationMode.SAFETY_UNPROVEN),
)
def test危险态不能直接构造为缺少完整控制锚点(mode: GenerationMode) -> None:
    """危险态必须保留可复验的 control lineage 起点和公开验收绑定。"""
    with pytest.raises(GenerationStateError, match="危险"):
        _steady(mode=mode, maintenance_active=True)

    record, _ = _control()
    with pytest.raises(GenerationStateError, match="危险"):
        _steady(
            mode=mode,
            maintenance_active=True,
            control_attempt_id=record.attempt_id,
            control_reservation_sha256=record.reservation_sha256,
            control_lease_record_sha256=control_lease_record_sha256(record),
            control_lease_epoch=record.epoch,
            acceptance_sha256=None,
        )


def test状态转换模块可从独立入口导入() -> None:
    """兼容门面不得反向依赖其实现模块，避免冷启动导入循环。"""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import codev_platform.runtime_generation_state_transitions",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "transition",
    (
        begin_switch,
        begin_validation,
        commit_serving,
        mark_restricted,
        mark_safety_unproven,
    ),
)
def test受控状态入口必须显式注入control_lease_lineage(transition) -> None:
    """遗漏 lineage 必须由 Python 签名直接拒绝，不能静默采用 singleton。"""
    parameter = inspect.signature(transition).parameters["control_lease_lineage"]

    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize(
    "operation",
    (
        lambda states, record, proof: begin_switch(
            states[0],
            _attempt(),
            record,
            proof,
            control_lease_lineage=None,  # type: ignore[arg-type]
            updated_at="2026-07-19T10:01:00Z",
        ),
        lambda states, record, proof: begin_validation(
            states[1],
            record,
            proof,
            control_lease_lineage=None,  # type: ignore[arg-type]
            updated_at="2026-07-19T10:02:00Z",
        ),
        lambda states, record, proof: commit_serving(
            states[2],
            _target_acceptance(),
            _target_fence(),
            record,
            proof,
            control_lease_lineage=None,  # type: ignore[arg-type]
            updated_at="2026-07-19T10:04:00Z",
        ),
        lambda states, record, proof: mark_restricted(
            states[2],
            record,
            proof,
            control_lease_lineage=None,  # type: ignore[arg-type]
            updated_at="2026-07-19T10:04:00Z",
        ),
        lambda states, record, proof: mark_safety_unproven(
            states[2],
            record,
            proof,
            control_lease_lineage=None,  # type: ignore[arg-type]
            updated_at="2026-07-19T10:04:00Z",
        ),
    ),
)
def test受控状态入口拒绝显式None_control_lease_lineage(operation) -> None:
    """关键字存在不代表可把显式 lineage 降级为隐式 singleton。"""
    record, proof = _control()

    with pytest.raises(GenerationStateError, match="lineage"):
        operation(_lifecycle(), record, proof)


def test_generation_state规范往返且摘要绑定全部字段() -> None:
    state = _steady()
    assert decode_generation_state(encode_generation_state(state)) == state
    assert generation_state_sha256(state) != generation_state_sha256(
        dataclasses.replace(
            state,
            state_version=8,
            updated_at="2026-07-19T10:00:01Z",
        )
    )


@pytest.mark.parametrize(
    ("mutation", "payload"),
    [
        pytest.param(name, payload, id=name)
        for name, payload in strict_json_mutations(
            encode_generation_state(_steady()),
            max_bytes=32_768,
            wrong_field="mode",
        )
    ],
)
def test_generation_state_decoder逐项拒绝单变量恶意载荷(
    mutation: str,
    payload: object,
) -> None:
    del mutation
    with pytest.raises(GenerationStateError):
        decode_generation_state(payload)  # type: ignore[arg-type]
