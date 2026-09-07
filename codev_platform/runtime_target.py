"""正式服务器运行时的固定 Python/平台目标契约。"""

from __future__ import annotations

import re

from codev_platform.core.runtime_models import RuntimeAbi


class RuntimeTargetError(RuntimeError):
    """当前解释器不是受支持的 Linux CPython 3.12 x86_64 构建目标。"""


def require_server_runtime_target(abi: RuntimeAbi) -> None:
    """在下载任何制品前固定目标，避免误生成 Windows/cp311/aarch64 lock。"""
    if type(abi) is not RuntimeAbi:
        raise RuntimeTargetError("服务器运行时 ABI 无效")
    if (
        abi.implementation != "cpython"
        or re.fullmatch(r"3\.12(?:\.\d+)?", abi.python_version) is None
        or abi.cache_tag != "cpython-312"
        or not abi.soabi.startswith("cpython-312-")
        or abi.platform_tag.replace("_", "-") != "linux-x86-64"
        or abi.machine not in {"x86_64", "AMD64"}
    ):
        raise RuntimeTargetError("正式运行时只接受 Linux CPython 3.12 x86_64")


__all__ = ["RuntimeTargetError", "require_server_runtime_target"]
