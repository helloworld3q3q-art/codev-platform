"""运行代际验收与 serving 围栏之间的跨域绑定校验。"""

from __future__ import annotations

import hmac

from codev_platform.runtime_fencing import ServingFenceRecord
from codev_platform.runtime_generation_acceptance import GenerationAcceptance


class GenerationAcceptanceBindingError(ValueError):
    """验收记录与 serving 围栏无法形成同一公开绑定。"""


def verify_serving_fence_acceptance(
    record: ServingFenceRecord,
    acceptance: GenerationAcceptance,
) -> ServingFenceRecord:
    """复验公开 serving 围栏与验收记录的五个共享绑定字段。"""
    if type(record) is not ServingFenceRecord:
        raise GenerationAcceptanceBindingError("只接受 ServingFenceRecord")
    if type(acceptance) is not GenerationAcceptance:
        raise GenerationAcceptanceBindingError("只接受 GenerationAcceptance")
    scalars_match = (
        record.fence_id == acceptance.serving_fence_id
        and record.generation_id == acceptance.generation_id
        and record.accepted_attempt_id == acceptance.attempt_id
        and record.epoch == acceptance.serving_fence_epoch
    )
    token_matches = hmac.compare_digest(
        record.token_sha256,
        acceptance.serving_fence_token_sha256,
    )
    if not scalars_match or not token_matches:
        raise GenerationAcceptanceBindingError("ServingFenceRecord 与 acceptance 绑定不一致")
    return record


__all__ = [
    "GenerationAcceptanceBindingError",
    "verify_serving_fence_acceptance",
]
