"""机器配置规范化、秘密边界和固定路径测试。"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform.runtime_managed_configuration as managed_configuration
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError
from codev_platform.runtime_managed_configuration import prepare_managed_configuration


def test_配置强制指向正式运行时与root环境文件() -> None:
    payload = prepare_managed_configuration(
        b'{"runtime":{"release_root":"/old"},"systemd":{},"memory":{"pg_dsn":null}}',
        b"CODEV_PLATFORM_MEMORY_DSN=postgresql://user:secret@db/new\n",
    )

    config = json.loads(payload.config)
    assert config["runtime"]["release_root"] == "/var/lib/codev-platform/runtime"
    assert config["systemd"]["env_file"] == "/etc/codev-platform/platform.env"
    assert b"CODEV_PLATFORM_CONFIG=/etc/codev-platform/config.json\n" in payload.environment
    assert len(payload.evidence_sha256) == 64


def test_已存在配置路径必须精确一致() -> None:
    with pytest.raises(RuntimeDeploymentError, match="非受管"):
        prepare_managed_configuration(
            b"{}",
            b"CODEV_PLATFORM_CONFIG=/home/helloworld/.codev-platform/config.json\n",
        )


def test_拒绝重复JSON字段和重复环境变量() -> None:
    with pytest.raises(RuntimeDeploymentError, match="严格解码"):
        prepare_managed_configuration(b'{"runtime":{},"runtime":{}}', b"")

    with pytest.raises(RuntimeDeploymentError, match="格式"):
        prepare_managed_configuration(b"{}", b"TOKEN=one\nTOKEN=two\n")


def test_规范化结果幂等且摘要不包含正文() -> None:
    first = prepare_managed_configuration(b'{"systemd":{},"runtime":{}}', b"SAFE=value\n")
    second = prepare_managed_configuration(first.config, first.environment)

    assert first == second
    assert b"SAFE=value" not in first.evidence_sha256.encode("ascii")


def test_环境文件禁止覆盖Python与运行身份保留变量() -> None:
    with pytest.raises(RuntimeDeploymentError, match="格式"):
        prepare_managed_configuration(b"{}", b"PYTHONPATH=/tmp/attack\n")


@pytest.mark.parametrize(
    ("mode", "gid", "accepted"),
    [
        (0o600, 0, True),
        (0o640, 23457, True),
        (0o640, 0, False),
        (0o644, 23457, False),
    ],
)
def test_bootstrap只兼容历史root_only或最终服务组环境投影(
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    gid: int,
    accepted: bool,
) -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | mode,
        st_uid=0,
        st_gid=gid,
        st_nlink=1,
        st_size=1,
    )
    path = SimpleNamespace(lstat=lambda: metadata)
    monkeypatch.setattr(managed_configuration, "MANAGED_ENVIRONMENT_PATH", path)
    monkeypatch.setattr(
        managed_configuration,
        "_read_fd_snapshot",
        lambda *_args, **_kwargs: b"SAFE=value\n",
    )

    if accepted:
        assert managed_configuration._read_bootstrap_environment_file(23457) == b"SAFE=value\n"
    else:
        with pytest.raises(RuntimeDeploymentError, match="权限"):
            managed_configuration._read_bootstrap_environment_file(23457)


def _配置bootstrap文件端口(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    config = tmp_path / "etc" / "codev-platform" / "config.json"
    environment = config.with_name("platform.env")
    environment.parent.mkdir(parents=True)
    environment.write_bytes(b"SAFE=value\n")
    monkeypatch.setattr(managed_configuration, "MANAGED_CONFIG_PATH", config)
    monkeypatch.setattr(managed_configuration, "MANAGED_ENVIRONMENT_PATH", environment)
    monkeypatch.setattr(managed_configuration, "_require_root", lambda: None)
    monkeypatch.setattr(managed_configuration, "_service_group_id", lambda _user: 23457)
    monkeypatch.setattr(
        managed_configuration,
        "_ensure_published_directory",
        lambda path, _group: path.mkdir(parents=True, exist_ok=True),
    )
    monkeypatch.setattr(
        managed_configuration,
        "_atomic_write",
        lambda path, content, _mode, _group: path.write_bytes(content),
    )
    monkeypatch.setattr(
        managed_configuration,
        "_read_published_file",
        lambda path, _maximum, _group: Path(path).read_bytes(),
    )
    monkeypatch.setattr(
        managed_configuration,
        "_read_bootstrap_environment_file",
        lambda _group: environment.read_bytes(),
    )
    monkeypatch.setattr(
        managed_configuration,
        "validate_systemd_environment_file",
        lambda _path: frozenset({"SAFE"}),
    )
    return config, environment


def test_缺失受管配置默认只验证不写入(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, environment = _配置bootstrap文件端口(monkeypatch, tmp_path)

    receipt = managed_configuration.bootstrap_missing_managed_configuration(
        b'{"runtime":{},"systemd":{}}',
        service_user="worker",
        apply=False,
    )

    assert receipt.state == "ready"
    assert not config.exists()
    assert environment.read_bytes() == b"SAFE=value\n"


def test_缺失受管配置按配置先于引用发布且可幂等续跑(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, environment = _配置bootstrap文件端口(monkeypatch, tmp_path)
    source = b'{"runtime":{},"systemd":{}}'

    first = managed_configuration.bootstrap_missing_managed_configuration(
        source,
        service_user="worker",
        apply=True,
    )
    second = managed_configuration.bootstrap_missing_managed_configuration(
        source,
        service_user="worker",
        apply=True,
    )

    assert first.state == "published"
    assert second.state == "already_published"
    assert f"CODEV_PLATFORM_CONFIG={config.as_posix()}\n".encode() in (
        environment.read_bytes()
    )
    assert json.loads(config.read_text(encoding="utf-8"))["runtime"]["release_root"] == (
        "/var/lib/codev-platform/runtime"
    )


def test_中断后仅写入配置的同内容半态可安全续跑(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, environment = _配置bootstrap文件端口(monkeypatch, tmp_path)
    source = b'{"runtime":{},"systemd":{}}'
    expected = prepare_managed_configuration(source, environment.read_bytes())
    config.write_bytes(expected.config)

    receipt = managed_configuration.bootstrap_missing_managed_configuration(
        source,
        service_user="worker",
        apply=True,
    )

    assert receipt.state == "published"
    assert environment.read_bytes() == expected.environment


def test_未知既有配置与悬空环境引用均拒绝覆盖(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config, environment = _配置bootstrap文件端口(monkeypatch, tmp_path)
    config.write_bytes(b"{}")

    with pytest.raises(RuntimeDeploymentError, match="内容不一致"):
        managed_configuration.bootstrap_missing_managed_configuration(
            b'{"runtime":{},"systemd":{}}',
            service_user="worker",
            apply=True,
        )

    config.unlink()
    environment.write_bytes(
        f"CODEV_PLATFORM_CONFIG={config.as_posix()}\n".encode()
    )
    with pytest.raises(RuntimeDeploymentError, match="状态不完整"):
        managed_configuration.bootstrap_missing_managed_configuration(
            b'{"runtime":{},"systemd":{}}',
            service_user="worker",
            apply=True,
        )
