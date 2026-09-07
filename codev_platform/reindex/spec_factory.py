"""纯函数式 attempt spec 构造边界与输入选择协议。"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from codev_platform.core.runtime_models import RuntimeIdentity
from codev_platform.reindex.attempts import AttemptSpec, CanonicalJsonObject
from codev_platform.reindex.queue_ports import ClaimedJob


@dataclass(frozen=True, slots=True)
class AttemptInputSelection:
    """父侧为单次 attempt 选择的无秘密输入描述。"""

    input_kind: str
    input_payload: CanonicalJsonObject


class AttemptInputSelector(Protocol):
    """只读取 claim 并选择输入类型，不物化文件或访问 Git。"""

    def select(self, claim: ClaimedJob) -> AttemptInputSelection:
        raise NotImplementedError("input selector method")


class ConfiguredInputSelector:
    """为 legacy/configured 模式生成最小白名单载荷。"""

    def select(self, claim: ClaimedJob) -> AttemptInputSelection:
        return AttemptInputSelection(
            input_kind="configured",
            input_payload=CanonicalJsonObject.from_value(
                {"project_id": claim.job.project_id},
            ),
        )


class AttemptSpecFactory:
    """使用启动时缓存身份构造唯一可信的业务 spec。"""

    def __init__(
        self,
        *,
        runtime_identity: RuntimeIdentity,
        input_selector: AttemptInputSelector,
        timeout_for_kind: Callable[[str], float],
        attempt_id_factory: Callable[[], str],
        fence_factory: Callable[[], str],
    ) -> None:
        self._runtime_identity = runtime_identity
        self._input_selector = input_selector
        self._timeout_for_kind = timeout_for_kind
        self._attempt_id_factory = attempt_id_factory
        self._fence_factory = fence_factory

    def create(self, claim: ClaimedJob) -> AttemptSpec:
        target_commit = str(claim.job.meta.target_commit or "").strip()
        if not target_commit:
            raise ValueError("隔离 attempt 必须提供 target_commit")
        timeout_sec = self._timeout(claim.job.kind)
        selected = self._input_selector.select(claim)
        return AttemptSpec(
            schema_version=1,
            attempt_id=self._attempt_id_factory(),
            fence=self._fence_factory(),
            project_id=claim.job.project_id,
            kind=claim.job.kind,
            input_kind=selected.input_kind,
            input_payload=selected.input_payload,
            target_commit=target_commit,
            timeout_sec=timeout_sec,
            runtime_revision=self._runtime_identity.runtime_revision,
        )

    def _timeout(self, kind: str) -> float:
        raw = self._timeout_for_kind(kind)
        if type(raw) not in (int, float):
            raise ValueError("attempt timeout 必须是有限正数")
        timeout = float(raw)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("attempt timeout 必须是有限正数")
        return timeout


__all__ = [
    "AttemptInputSelection",
    "AttemptInputSelector",
    "AttemptSpecFactory",
    "ConfiguredInputSelector",
]
