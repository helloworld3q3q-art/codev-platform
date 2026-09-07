"""运行时构建子进程的最小环境真值，避免继承业务凭据与导入路径。"""

from __future__ import annotations

import os
from collections.abc import Mapping


_BASE_KEYS = (
    "COMSPEC",
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "WINDIR",
)
_NETWORK_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "PIP_CERT",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_FILE",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


def isolated_process_environment(
    *,
    network: bool = False,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """仅保留进程启动必需变量；联网边界才透传代理与证书配置。"""
    if type(network) is not bool:
        raise TypeError("network 必须是布尔值")
    keys = (*_BASE_KEYS, *_NETWORK_KEYS) if network else _BASE_KEYS
    environment = {key: os.environ[key] for key in keys if os.environ.get(key)}
    environment.setdefault("HOME", os.path.expanduser("~"))
    environment.setdefault("PATH", os.defpath)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": "",
        }
    )
    if overrides is not None:
        if not isinstance(overrides, Mapping) or any(
            type(key) is not str or type(value) is not str for key, value in overrides.items()
        ):
            raise TypeError("子进程环境覆盖必须是字符串映射")
        environment.update(overrides)
    return environment


__all__ = ["isolated_process_environment"]
