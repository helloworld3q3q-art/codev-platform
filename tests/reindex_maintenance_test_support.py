"""reindex 维护状态机测试的共享夹具与轻量辅助构造器。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _isolate_legacy_reindex_codegraph_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """让旧状态机用例不耦合由独立集成用例覆盖的 CodeGraph 接线。"""
    from codev_platform.ops import reindex_maintenance as maintenance

    monkeypatch.setattr(maintenance, "_default_codegraph_prepare", lambda: None)
    monkeypatch.setattr(
        maintenance,
        "_default_codegraph_maintenance_proof",
        lambda: None,
    )
    _isolate_legacy_webhook_boundary(monkeypatch)


def _isolate_legacy_webhook_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧维护测试不触及真实 Webhook systemd 生命周期。"""
    from codev_platform.ops import reindex_maintenance as maintenance

    monkeypatch.setattr(maintenance, "_default_webhook_prepare", lambda **_kwargs: None)
    monkeypatch.setattr(
        maintenance,
        "_default_webhook_maintenance_proof",
        lambda **_kwargs: None,
    )


def _dropin(tmp_path: Path) -> Path:
    return tmp_path / "codev-reindex.service.d" / "10-codev-reindex-maintenance.conf"


def _ok(*, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="不得输出原始错误")


def _standby_hooks(*, completer=None) -> dict[str, object]:
    """为 systemd 编排单测提供已验证的待命身份，不触及真实全局 marker。"""
    return {
        "standby_armer": lambda: SimpleNamespace(generation="a" * 32),
        "unit_invocation_reader": lambda: "b" * 32,
        "standby_claimer": lambda _generation, _invocation: None,
        "standby_renewer": lambda _generation, _invocation: None,
        "standby_completer": (
            (lambda _generation, _invocation: None) if completer is None else completer
        ),
    }
