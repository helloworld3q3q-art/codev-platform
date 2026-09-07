"""维护失败补偿后的统一最终证明。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


Proof = Callable[[], None]
Attempt = Callable[[Proof], bool]


@dataclass(frozen=True)
class FinalMaintenanceProof:
    """同一收敛轮次内的最终安全证明结果。"""

    no_external_workers: bool
    reindex_stopped: bool
    codegraph_held: bool
    webhook_closed: bool

    @property
    def proven(self) -> bool:
        """只有全部索引与入口写入边界都已在最终态验证通过才算安全。"""
        return (
            self.no_external_workers
            and self.reindex_stopped
            and self.codegraph_held
            and self.webhook_closed
        )


def collect_final_maintenance_proof(
    *,
    attempt: Attempt,
    prove_external_workers: Proof,
    prove_reindex_stopped: Proof,
    prove_codegraph_held: Proof,
    prove_webhook_closed: Proof,
) -> FinalMaintenanceProof:
    """按外部写入者、reindex、CodeGraph、Webhook 的固定顺序复证最终状态。"""
    return FinalMaintenanceProof(
        no_external_workers=attempt(prove_external_workers),
        reindex_stopped=attempt(prove_reindex_stopped),
        codegraph_held=attempt(prove_codegraph_held),
        webhook_closed=attempt(prove_webhook_closed),
    )


__all__ = ["FinalMaintenanceProof", "collect_final_maintenance_proof"]
