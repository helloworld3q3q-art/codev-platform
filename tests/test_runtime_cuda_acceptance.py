"""不可变 target release 的 CUDA 能力验收测试。"""

from __future__ import annotations

import pytest

from codev_platform.runtime_cuda_acceptance import CudaPorts, verify_cuda_runtime
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError


def _ports(*, available: bool, count: int, computed: bool = True) -> CudaPorts:
    return CudaPorts(
        is_available=lambda: available,
        device_count=lambda: count,
        compute_probe=lambda: computed,
    )


def test_要求cuda时必须发现设备并完成真实张量计算() -> None:
    result = verify_cuda_runtime(
        require_cuda=True,
        ports=_ports(available=True, count=1),
    )

    assert result.available is True
    assert result.device_count == 1
    assert len(result.evidence_sha256) == 64


@pytest.mark.parametrize(
    ("available", "count", "computed"),
    [(False, 0, True), (True, 0, True), (True, 1, False)],
)
def test_要求cuda时能力不完整即失败(
    available: bool,
    count: int,
    computed: bool,
) -> None:
    with pytest.raises(RuntimeDeploymentError, match="CUDA"):
        verify_cuda_runtime(
            require_cuda=True,
            ports=_ports(available=available, count=count, computed=computed),
        )


def test_允许cpu时无设备可通过但状态必须自洽() -> None:
    result = verify_cuda_runtime(
        require_cuda=False,
        ports=_ports(available=False, count=0),
    )

    assert result.available is False
    assert result.device_count == 0

    with pytest.raises(RuntimeDeploymentError, match="CUDA"):
        verify_cuda_runtime(
            require_cuda=False,
            ports=_ports(available=False, count=1),
        )
