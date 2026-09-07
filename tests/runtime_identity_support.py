"""运行身份状态面测试共用的稳定身份桩。"""

from __future__ import annotations

from codev_platform.core.runtime_models import RuntimeIdentity

RUNTIME_REVISION = "a" * 40


def fake_runtime_identity() -> RuntimeIdentity:
    """返回不依赖宿主安装形态的有效运行身份。"""
    return RuntimeIdentity(
        mode="editable",
        runtime_revision=RUNTIME_REVISION,
        release_id=None,
        wheel_sha256=None,
        base_id=None,
        base_requirements_sha256=None,
        interpreter_realpath="/opt/codev/bin/python",
        environment_prefix="/opt/codev",
        source_root="/srv/codev-platform",
    )
