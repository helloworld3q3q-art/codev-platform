"""基座 root-fd worker 请求边界回归。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import RuntimeAbi, canonical_json_bytes
from codev_platform._runtime_base_errors import RuntimeBaseIntegrityError
from codev_platform.runtime_bound_worker_base import _decode_request, _decode_verify_request
from codev_platform import runtime_base, runtime_bound_worker_base
from codev_platform.runtime_object_access import RuntimeObjectAccessError


def test基座构建请求拒绝非摘要基座ID(tmp_path: Path) -> None:
    """构建请求必须在解析阶段拒绝非内容地址的基座身份。"""
    with pytest.raises(ValueError, match="base_id"):
        _decode_request(
            {
                "abi": _abi_value(),
                "base_id": "not-a-sha256",
                "contract": _contract_value(),
                "lock_path": str(tmp_path / "requirements.lock"),
                "runtime_root": str(tmp_path / "runtime"),
                "wheelhouse": None,
            },
        )


def test基座复验请求拒绝非摘要基座ID(tmp_path: Path) -> None:
    """复验请求必须在解析阶段拒绝非内容地址的基座身份。"""
    with pytest.raises(ValueError, match="base_id"):
        _decode_verify_request(
            {
                "base_id": "not-a-sha256",
                "deep": False,
                "runtime_root": str(tmp_path / "runtime"),
            },
        )


def test基座worker将对象访问失败转换为隔离可识别的领域错误(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已完成基座访问漂移必须进入既有 corrupt 隔离分支。"""

    def reject(_root: Path) -> None:
        raise RuntimeObjectAccessError("模拟访问模式漂移")

    monkeypatch.setattr(runtime_bound_worker_base, "verify_object_access_from_cwd", reject)

    with pytest.raises(RuntimeBaseIntegrityError, match="基座对象访问模式无法复验"):
        runtime_bound_worker_base._verify_base_object_access(Path("bases/demo"))


def test基座已锁定cwd复验复用worker端口而不绝对化根(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """release worker 已持有 root binding 时不得回到通用绝对根 API。"""
    default_ports = object()
    worker_ports = object()
    bound_root = object()
    metadata = object()
    observed: list[tuple[Path, str, object]] = []
    layout_checks: list[None] = []
    monkeypatch.setattr(
        runtime_bound_worker_base,
        "_verify_runtime_layout",
        lambda: layout_checks.append(None),
    )
    monkeypatch.setattr(runtime_base, "_default_ports", lambda: default_ports)
    monkeypatch.setattr(
        runtime_bound_worker_base,
        "_worker_ports",
        lambda ports, root: worker_ports if (ports, root) == (default_ports, bound_root) else None,
    )

    def verify_locked(root: Path, base_id: str, ports: object) -> object:
        observed.append((root, base_id, ports))
        return metadata

    monkeypatch.setattr(runtime_base, "_verify_locked", verify_locked)

    result = runtime_bound_worker_base.verify_base_locked_from_cwd(
        "a" * 64,
        bound_root,
    )

    assert result is metadata
    assert observed == [(Path("."), "a" * 64, worker_ports)]
    assert layout_checks == [None]


def _abi_value() -> dict[str, object]:
    abi = RuntimeAbi(
        implementation="cpython",
        python_version="3.12.3",
        cache_tag="cpython-312",
        soabi="cpython-312-x86_64-linux-gnu",
        platform_tag="linux-x86_64",
        machine="x86_64",
    )
    return json.loads(canonical_json_bytes(abi))


def _contract_value() -> dict[str, object]:
    return {
        "artifacts": [
            {
                "filename": "demo-1.0-py3-none-any.whl",
                "package": "demo",
                "sha256": "a" * 64,
            },
        ],
        "info": {
            "approved_index_url": "https://example.invalid/simple",
            "artifact_manifest_sha256": "b" * 64,
            "cuda_tags": [],
            "pin_count": 1,
            "requirements_sha256": "c" * 64,
        },
        "pins": [{"name": "demo", "version": "1.0"}],
    }
