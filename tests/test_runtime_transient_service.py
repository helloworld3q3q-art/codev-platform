"""部署叶子 transient unit 的双层超时与 cgroup 回收测试。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import codev_platform.runtime_transient_service as transient
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError
from codev_platform.runtime_managed_process import (
    ManagedProcessResult,
    RuntimeManagedProcessError,
)
from codev_platform.runtime_transient_service import (
    TransientServicePorts,
    TransientServiceSpec,
    deployment_transient_unit,
    run_transient_service,
)


def _spec() -> TransientServiceSpec:
    return TransientServiceSpec(
        unit=deployment_transient_unit("a" * 40, "database"),
        user="helloworld",
        executable=Path(
            "/var/lib/codev-platform/runtime/releases/" + "b" * 64 + "/venv/bin/python"
        ),
        arguments=("-I", "-m", "codev_platform.runtime_deployment_worker", "verify-database"),
        environment_file=Path("/etc/codev-platform/platform.env"),
        timeout_sec=120.0,
    )


def test_稳定命名叶子只聚合通用受管进程规格和固定资源profile() -> None:
    events: list[object] = []

    def run_managed(spec: object) -> ManagedProcessResult:
        events.append(spec)
        return ManagedProcessResult(0, b'{"status":"ok"}\n', b"")

    output = run_transient_service(
        _spec(),
        ports=TransientServicePorts(run_managed=run_managed),
        platform_name="linux",
    )

    assert output == '{"status":"ok"}\n'
    assert _spec().unit == "codev-runtime-deploy-database.service"
    managed_spec = events[0]
    assert managed_spec.unit_slot == "deploy-database"
    assert managed_spec.user == "helloworld"
    assert managed_spec.working_directory == "%h"
    assert managed_spec.environment_file == _spec().environment_file
    assert managed_spec.limits.runtime_sec == 120.0
    assert managed_spec.limits.stop_sec == 30.0
    assert managed_spec.limits.tasks_max == 128
    assert managed_spec.limits.memory_max_bytes == 8 * 1024**3


def test_通用执行失败被部署边界固定脱敏() -> None:
    def run_managed(_spec: object) -> ManagedProcessResult:
        raise RuntimeManagedProcessError("secret argv output")

    with pytest.raises(RuntimeDeploymentError, match="无法执行") as captured:
        run_transient_service(
            _spec(),
            ports=TransientServicePorts(run_managed=run_managed),
            platform_name="linux",
        )

    assert "secret" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_叶子非零或stderr非空都不能返回成功() -> None:
    for result in (
        ManagedProcessResult(7, b"private", b""),
        ManagedProcessResult(0, b"ok", b"warning"),
    ):
        with pytest.raises(RuntimeDeploymentError, match="执行失败"):
            run_transient_service(
                _spec(),
                ports=TransientServicePorts(run_managed=lambda _spec, value=result: value),
                platform_name="linux",
            )


def test_测试端口不要求真实root但生产端口必须要求(monkeypatch) -> None:
    monkeypatch.setattr(transient.sys, "platform", "linux")
    monkeypatch.setattr(transient.os, "geteuid", lambda: 1000, raising=False)
    ports = TransientServicePorts(
        run_managed=lambda _spec: ManagedProcessResult(0, b"ok", b""),
    )

    assert run_transient_service(_spec(), ports=ports, platform_name="linux") == "ok"
    with pytest.raises(RuntimeDeploymentError, match="root"):
        run_transient_service(
            _spec(),
            platform_name="linux",
        )


def test_user为none时叶子保持root身份() -> None:
    specs: list[object] = []
    spec = replace(_spec(), user=None)

    run_transient_service(
        spec,
        ports=TransientServicePorts(
            run_managed=lambda value: specs.append(value) or ManagedProcessResult(0, b"ok", b""),
        ),
        platform_name="linux",
    )

    assert specs[0].user is None
