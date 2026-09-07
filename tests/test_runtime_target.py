"""正式服务器运行时目标 ABI 门禁测试。"""

from __future__ import annotations

import pytest

from codev_platform.core.runtime_models import RuntimeAbi
from codev_platform.runtime_target import RuntimeTargetError, require_server_runtime_target


def _abi(**changes: str) -> RuntimeAbi:
    values = {
        "implementation": "cpython",
        "python_version": "3.12.3",
        "cache_tag": "cpython-312",
        "soabi": "cpython-312-x86_64-linux-gnu",
        "platform_tag": "linux-x86_64",
        "machine": "x86_64",
    }
    values.update(changes)
    return RuntimeAbi(**values)


def test_linux_cpython312_x86_64_is_accepted() -> None:
    require_server_runtime_target(_abi())


@pytest.mark.parametrize(
    "changes",
    (
        {"implementation": "pypy"},
        {"python_version": "3.11.9", "cache_tag": "cpython-311"},
        {"platform_tag": "win-amd64"},
        {"machine": "aarch64", "platform_tag": "linux-aarch64"},
    ),
)
def test_other_runtime_targets_fail_before_artifact_download(changes: dict[str, str]) -> None:
    with pytest.raises(RuntimeTargetError, match="Linux CPython 3.12 x86_64"):
        require_server_runtime_target(_abi(**changes))
