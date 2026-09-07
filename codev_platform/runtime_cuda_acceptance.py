"""target release 内 CUDA 发现与真实张量计算验收。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from codev_platform.runtime_deployment_contract import RuntimeDeploymentError


@dataclass(frozen=True, slots=True)
class CudaPorts:
    is_available: Callable[[], bool]
    device_count: Callable[[], int]
    compute_probe: Callable[[], bool]

    def validate(self) -> CudaPorts:
        if type(self) is not CudaPorts or not all(
            callable(item)
            for item in (self.is_available, self.device_count, self.compute_probe)
        ):
            raise RuntimeDeploymentError("CUDA 验收端口不可用")
        return self


@dataclass(frozen=True, slots=True)
class CudaAcceptanceReceipt:
    available: bool
    device_count: int
    evidence_sha256: str


def verify_cuda_runtime(
    *,
    require_cuda: bool,
    ports: CudaPorts | None = None,
) -> CudaAcceptanceReceipt:
    """能力状态必须自洽；要求 CUDA 时还必须完成一次真实设备计算。"""
    if type(require_cuda) is not bool:
        raise RuntimeDeploymentError("CUDA 验收要求无效")
    active = default_ports() if ports is None else ports.validate()
    try:
        available = active.is_available()
        count = active.device_count()
        if type(available) is not bool or type(count) is not int or count < 0:
            raise RuntimeDeploymentError("CUDA 能力状态无效")
        if available != (count > 0):
            raise RuntimeDeploymentError("CUDA 可用状态与设备数量不一致")
        computed = active.compute_probe() if available else False
        if type(computed) is not bool:
            raise RuntimeDeploymentError("CUDA 计算探针状态无效")
        if available and not computed:
            raise RuntimeDeploymentError("CUDA 真实张量计算失败")
        if require_cuda and (not available or not computed):
            raise RuntimeDeploymentError("部署要求 CUDA，但 target release 不可用")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeDeploymentError:
        raise
    except Exception:
        raise RuntimeDeploymentError("CUDA 验收失败") from None
    evidence = hashlib.sha256(
        f"required={int(require_cuda)}\navailable={int(available)}\ndevices={count}\n"
        f"computed={int(computed)}\n".encode("ascii")
    ).hexdigest()
    return CudaAcceptanceReceipt(available, count, evidence)


def default_ports() -> CudaPorts:
    try:
        import torch
    except Exception:
        raise RuntimeDeploymentError("target release 无法导入 CUDA 运行库") from None

    def compute() -> bool:
        tensor = torch.tensor((1.0, 2.0, 3.0), device="cuda")
        value = float((tensor * 2.0).sum().item())
        torch.cuda.synchronize()
        return value == 12.0

    return CudaPorts(
        is_available=torch.cuda.is_available,
        device_count=torch.cuda.device_count,
        compute_probe=compute,
    )


__all__ = [
    "CudaAcceptanceReceipt",
    "CudaPorts",
    "verify_cuda_runtime",
]
