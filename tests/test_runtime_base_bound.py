"""基座 root-fd worker 门面的失败优先级回归。"""

from __future__ import annotations

import pytest

import codev_platform.runtime_base_bound as runtime_base_bound
from codev_platform._runtime_base_errors import RuntimeBaseError
from codev_platform.runtime_bound_worker import RuntimeBoundWorkerError
from codev_platform.runtime_root_binding import RuntimeRootBindingError


def test基座worker失败后仍优先报告可见根漂移(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """失败 worker 不能绕过其后的根身份复验而掩盖替换边界。"""

    class DriftingRoot:
        def verify_visible(self) -> None:
            raise RuntimeRootBindingError("模拟命名根替换")

    def fail_worker(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeBoundWorkerError("模拟 worker 失败")

    monkeypatch.setattr(runtime_base_bound, "run_bound_operation", fail_worker)

    with pytest.raises(RuntimeBaseError, match="基座复验期间运行时根目录已漂移"):
        runtime_base_bound._run_worker_and_verify(
            DriftingRoot(),
            "base-verify",
            {},
            timeout_sec=120.0,
            phase="基座复验",
        )
