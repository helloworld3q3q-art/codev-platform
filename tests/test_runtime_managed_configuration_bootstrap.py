"""缺失受管配置 bootstrap 的维护窗口与来源门禁测试。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform.runtime_managed_configuration_bootstrap as bootstrap
from codev_platform.runtime_managed_configuration import (
    ManagedConfigurationBootstrapReceipt,
)
from codev_platform.runtime_service_process import ServiceAccount


_ACCOUNT = ServiceAccount(
    name="worker",
    uid=23456,
    gid=23457,
    home=Path("/srv/worker"),
)


def _receipt(state: str = "ready") -> ManagedConfigurationBootstrapReceipt:
    return ManagedConfigurationBootstrapReceipt(
        config_sha256="a" * 64,
        environment_sha256="b" * 64,
        evidence_sha256="c" * 64,
        state=state,
    )


def _ports(events: list[str], *, permitted: bool = True) -> bootstrap.ManagedConfigurationBootstrapPorts:
    @contextmanager
    def permit():
        events.append("permit-enter")
        try:
            yield permitted
        finally:
            events.append("permit-exit")

    def read(account: ServiceAccount) -> bytes:
        assert account is _ACCOUNT
        events.append("read")
        return b'{"runtime":{},"systemd":{}}'

    def publish(content: bytes, *, service_user: str, apply: bool):
        assert content == b'{"runtime":{},"systemd":{}}'
        assert service_user == "worker"
        events.append(f"publish:{apply}")
        return _receipt("published" if apply else "ready")

    return bootstrap.ManagedConfigurationBootstrapPorts(
        resolve_service_account=lambda user: _ACCOUNT if user == "worker" else None,
        maintenance_permit=permit,
        inspect_maintenance=lambda: events.append("inspect"),
        read_service_configuration=read,
        publish_configuration=publish,
    )


def test_默认引导只读取固定来源且不请求维护许可() -> None:
    events: list[str] = []

    receipt = bootstrap.bootstrap_service_managed_configuration(
        service_user="worker",
        apply=False,
        ports=_ports(events),
    )

    assert receipt.state == "ready"
    assert events == ["read", "publish:False"]


def test_确认写入必须被维护窗口完整包围() -> None:
    events: list[str] = []

    receipt = bootstrap.bootstrap_service_managed_configuration(
        service_user="worker",
        apply=True,
        ports=_ports(events),
    )

    assert receipt.state == "published"
    assert events == [
        "permit-enter",
        "inspect",
        "read",
        "publish:True",
        "inspect",
        "permit-exit",
    ]


def test_维护窗口未证明时不读取来源更不写入() -> None:
    events: list[str] = []

    with pytest.raises(bootstrap.ManagedConfigurationBootstrapError, match="维护窗口"):
        bootstrap.bootstrap_service_managed_configuration(
            service_user="worker",
            apply=True,
            ports=_ports(events, permitted=False),
        )

    assert events == ["permit-enter", "permit-exit"]


@pytest.mark.parametrize(
    ("mode", "uid", "links", "expected"),
    [
        (0o100600, _ACCOUNT.uid, 1, True),
        (0o120600, _ACCOUNT.uid, 1, False),
        (0o100620, _ACCOUNT.uid, 1, False),
        (0o100600, _ACCOUNT.uid + 1, 1, False),
        (0o100600, _ACCOUNT.uid, 2, False),
    ],
)
def test_服务账号来源文件必须是私有单链接普通文件(
    mode: int,
    uid: int,
    links: int,
    expected: bool,
) -> None:
    metadata = SimpleNamespace(
        st_mode=mode,
        st_uid=uid,
        st_nlink=links,
        st_size=1,
    )

    assert bootstrap._trusted_service_file(metadata, _ACCOUNT.uid) is expected


def test_无效发布回执不能冒充成功() -> None:
    events: list[str] = []
    ports = _ports(events)
    invalid = bootstrap.ManagedConfigurationBootstrapPorts(
        resolve_service_account=ports.resolve_service_account,
        maintenance_permit=ports.maintenance_permit,
        inspect_maintenance=ports.inspect_maintenance,
        read_service_configuration=ports.read_service_configuration,
        publish_configuration=lambda *_args, **_kwargs: object(),
    )

    with pytest.raises(bootstrap.ManagedConfigurationBootstrapError, match="回执"):
        bootstrap.bootstrap_service_managed_configuration(
            service_user="worker",
            apply=False,
            ports=invalid,
        )


def test_回执摘要字段必须是完整哈希而非任意文本() -> None:
    events: list[str] = []
    ports = _ports(events)
    invalid = bootstrap.ManagedConfigurationBootstrapPorts(
        resolve_service_account=ports.resolve_service_account,
        maintenance_permit=ports.maintenance_permit,
        inspect_maintenance=ports.inspect_maintenance,
        read_service_configuration=ports.read_service_configuration,
        publish_configuration=lambda *_args, **_kwargs: ManagedConfigurationBootstrapReceipt(
            config_sha256="token=abc",
            environment_sha256="b" * 64,
            evidence_sha256="c" * 64,
            state="ready",
        ),
    )

    with pytest.raises(bootstrap.ManagedConfigurationBootstrapError, match="回执"):
        bootstrap.bootstrap_service_managed_configuration(
            service_user="worker",
            apply=False,
            ports=invalid,
        )
