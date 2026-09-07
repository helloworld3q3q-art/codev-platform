"""运行代际状态的兼容门面，重导出模型、codec 与命名转换。"""

from __future__ import annotations

from codev_platform.runtime_generation_state_model import (
    GenerationMode,
    GenerationState,
    GenerationStateError,
    decode_generation_state,
    encode_generation_state,
    generation_state_sha256,
)
from codev_platform.runtime_generation_state_transitions import (
    begin_switch,
    begin_validation,
    commit_serving,
    mark_restricted,
    mark_safety_unproven,
    prepare_serving_publication,
)


__all__ = [
    "GenerationMode",
    "GenerationState",
    "GenerationStateError",
    "begin_switch",
    "begin_validation",
    "commit_serving",
    "decode_generation_state",
    "encode_generation_state",
    "generation_state_sha256",
    "mark_restricted",
    "mark_safety_unproven",
    "prepare_serving_publication",
]
