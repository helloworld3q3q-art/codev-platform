"""冻结 CodeGraph 的 M1 一次性启动桥测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _门禁(*, marker: bool, mask: bool):
    calls: list[str] = []

    class 门禁错误(RuntimeError):
        pass

    def 原始启动校验() -> None:
        calls.append("原始启动校验")
        raise 门禁错误("维护 marker 仍生效")

    gate = SimpleNamespace(
        CodegraphMaintenanceGateError=门禁错误,
        _maintenance_marker_active=lambda: marker,
        _runtime_mask_active=lambda: mask,
        require_codegraph_service_start_permitted=原始启动校验,
    )
    return gate, 门禁错误, 原始启动校验, calls


def test_仅首次M1服务启动校验可被桥接放行(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops import codegraph_startup_bridge as module

    gate, 门禁错误, 原始启动校验, calls = _门禁(marker=True, mask=False)
    monkeypatch.setattr(module, "_codegraph_hold_active", lambda: False)
    monkeypatch.setattr(module, "_current_process_in_codegraph_cgroup", lambda: True)

    module._install_m1_initial_start_permit(gate)
    gate.require_codegraph_service_start_permitted()

    assert gate.require_codegraph_service_start_permitted is 原始启动校验
    with pytest.raises(门禁错误, match="marker"):
        gate.require_codegraph_service_start_permitted()
    assert calls == ["原始启动校验"]


def test_主入口先安装一次性许可再启动冻结服务模块(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform import codegraph
    from codev_platform.ops import codegraph_startup_bridge as module

    gate, _门禁错误, 原始启动校验, _calls = _门禁(marker=True, mask=False)
    events: list[object] = []
    monkeypatch.setattr(codegraph, "maintenance_gate", gate, raising=False)
    monkeypatch.setattr(module, "_codegraph_hold_active", lambda: False)
    monkeypatch.setattr(module, "_current_process_in_codegraph_cgroup", lambda: True)

    def 启动冻结模块(name: str, *, run_name: str, alter_sys: bool) -> None:
        events.append((name, run_name, alter_sys))
        gate.require_codegraph_service_start_permitted()

    monkeypatch.setattr(module.runpy, "run_module", 启动冻结模块)

    module.main()

    assert events == [("codev_platform.codegraph.server", "__main__", True)]
    assert gate.require_codegraph_service_start_permitted is 原始启动校验


def test_marker未激活时桥接拒绝执行(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.ops import codegraph_startup_bridge as module

    gate, 门禁错误, 原始启动校验, _calls = _门禁(marker=False, mask=False)
    monkeypatch.setattr(module, "_codegraph_hold_active", lambda: False)
    monkeypatch.setattr(module, "_current_process_in_codegraph_cgroup", lambda: True)

    with pytest.raises(门禁错误, match="边界"):
        module._install_m1_initial_start_permit(gate)

    assert gate.require_codegraph_service_start_permitted is 原始启动校验


@pytest.mark.parametrize(
    ("hold", "mask", "in_codegraph_cgroup"),
    [(True, False, True), (False, True, True), (False, False, False)],
)
def test_M1边界任一条件不满足即恢复原门禁并拒绝(
    monkeypatch: pytest.MonkeyPatch,
    hold: bool,
    mask: bool,
    in_codegraph_cgroup: bool,
) -> None:
    from codev_platform.ops import codegraph_startup_bridge as module

    gate, 门禁错误, 原始启动校验, calls = _门禁(marker=True, mask=mask)
    monkeypatch.setattr(module, "_codegraph_hold_active", lambda: hold)
    monkeypatch.setattr(
        module,
        "_current_process_in_codegraph_cgroup",
        lambda: in_codegraph_cgroup,
    )

    module._install_m1_initial_start_permit(gate)

    with pytest.raises(门禁错误, match="边界"):
        gate.require_codegraph_service_start_permitted()
    assert gate.require_codegraph_service_start_permitted is 原始启动校验
    assert calls == []
