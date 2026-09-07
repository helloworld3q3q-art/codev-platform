"""systemd 受管进程的纯契约、编排顺序与错误边界测试。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform.runtime_managed_process as managed


def _limits(**overrides: object) -> managed.ManagedProcessLimits:
    values = {
        "runtime_sec": 120.0,
        "stop_sec": 30.0,
        "stdout_limit_bytes": 4096,
        "stderr_limit_bytes": 4096,
        "tasks_max": 64,
        "memory_high_bytes": 3 * 1024**3,
        "memory_max_bytes": 4 * 1024**3,
        "memory_swap_max_bytes": 0,
        "cpu_quota_percent": 200,
        **overrides,
    }
    return managed.ManagedProcessLimits(**values)  # type: ignore[arg-type]


def _spec(**overrides: object) -> managed.ManagedProcessSpec:
    values = {
        "unit_slot": "target-user-probe",
        "argv": ("/usr/bin/python3", "-I", "-c", "print('ok')"),
        "working_directory": "/",
        "environment": (),
        "environment_file": None,
        "user": None,
        "limits": _limits(),
        **overrides,
    }
    return managed.ManagedProcessSpec(**values)  # type: ignore[arg-type]


def test_资源边界冻结且拒绝可扩大为无界的值() -> None:
    limits = _limits()

    with pytest.raises(FrozenInstanceError):
        limits.tasks_max = 65  # type: ignore[misc]
    for overrides in (
        {"runtime_sec": float("inf")},
        {"stop_sec": 0},
        {"stdout_limit_bytes": 1024 * 1024 + 1},
        {"stderr_limit_bytes": -1},
        {"tasks_max": True},
        {"tasks_max": 4097},
        {"memory_high_bytes": 5 * 1024**3},
        {"memory_max_bytes": 65 * 1024**3},
        {"memory_swap_max_bytes": -1},
        {"cpu_quota_percent": 1601},
    ):
        with pytest.raises(managed.RuntimeManagedProcessError):
            _limits(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"unit_slot": "Bad_Slot"},
        {"unit_slot": "../probe"},
        {"argv": ()},
        {"argv": ("relative",)},
        {"argv": ("/usr/bin/python3\x00",)},
        {"working_directory": "relative"},
        {"environment": (("LD_PRELOAD", "/run/evil.so"),)},
        {"environment": (("Z", "1"), ("A", "2"))},
        {"environment": (("BAD=KEY", "1"),)},
        {"environment_file": Path("relative.env")},
        {"environment_file": Path("/etc/codev-platform/*.env")},
        {"environment_file": Path("/etc/codev-platform/value?.env")},
        {"environment_file": Path("/etc/codev-platform/[prod].env")},
        {"environment_file": Path("/etc/codev-platform/value%.env")},
        {"environment_file": Path("/etc/codev-platform/value name.env")},
        {"user": "root"},
    ],
)
def test_执行规格拒绝路径注入非规范环境和特权用户(overrides: dict[str, object]) -> None:
    with pytest.raises(managed.RuntimeManagedProcessError):
        _spec(**overrides)


def test_稳定语义slot生成不含修订的固定unit() -> None:
    assert managed.managed_unit_name("candidate-build") == ("codev-runtime-candidate-build.service")
    assert managed.managed_unit_name("target-user-probe") == (
        "codev-runtime-target-user-probe.service"
    )


def test_编排严格覆盖同slot互斥前置收敛执行与后置证明() -> None:
    events: list[object] = []
    process = SimpleNamespace(poll=lambda: 0)
    expected = managed.ManagedProcessResult(0, b"ok\n", b"")

    @contextmanager
    def lock(unit: str, timeout_sec: float):
        events.append(("lock-enter", unit, timeout_sec))
        yield
        events.append(("lock-exit", unit))

    ports = managed.ManagedProcessPorts(
        lock_unit=lock,
        settle_unit=lambda unit, timeout: events.append(("settle", unit, timeout)),
        spawn_unit=lambda spec, unit: events.append(("spawn", spec, unit)) or process,
        collect_output=lambda proc, stdout, stderr, deadline: (
            events.append(("collect", proc, stdout, stderr, deadline)) or expected
        ),
        abort_client=lambda proc, timeout: events.append(("abort", proc, timeout)),
    )

    result = managed.run_managed_process(
        _spec(),
        ports=ports,
        platform_name="linux",
    )

    unit = "codev-runtime-target-user-probe.service"
    assert result == expected
    assert [event[0] for event in events] == [
        "lock-enter",
        "settle",
        "spawn",
        "collect",
        "settle",
        "lock-exit",
    ]
    assert events[0] == ("lock-enter", unit, 30.0)


def test_已回收systemd_run客户端发生后置异常时绝不按旧PID清算() -> None:
    events: list[object] = []
    process = SimpleNamespace(poll=lambda: 7)

    @contextmanager
    def lock(_unit: str, _timeout_sec: float):
        yield

    def collect(*_args: object) -> managed.ManagedProcessResult:
        raise OSError("sensitive output failure")

    ports = managed.ManagedProcessPorts(
        lock_unit=lock,
        settle_unit=lambda unit, timeout: events.append(("settle", unit, timeout)),
        spawn_unit=lambda _spec, _unit: process,
        collect_output=collect,
        abort_client=lambda proc, timeout: events.append(("abort", proc, timeout)),
    )

    with pytest.raises(managed.RuntimeManagedProcessError) as caught:
        managed.run_managed_process(_spec(), ports=ports, platform_name="linux")

    assert str(caught.value) == "systemd 受管进程执行失败"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert [event[0] for event in events] == ["settle", "settle", "settle"]


def test_存活客户端失败时先清算unit再只终止客户端() -> None:
    events: list[object] = []
    alive = True

    class Process:
        def poll(self) -> int | None:
            return None if alive else 0

    process = Process()

    @contextmanager
    def lock(_unit: str, _timeout_sec: float):
        yield

    def settle(unit: str, timeout: float) -> None:
        nonlocal alive
        events.append(("settle", unit, timeout))
        if len(events) > 1:
            alive = False

    ports = managed.ManagedProcessPorts(
        lock_unit=lock,
        settle_unit=settle,
        spawn_unit=lambda _spec, _unit: process,
        collect_output=lambda *_args: (_ for _ in ()).throw(TimeoutError("sensitive")),
        abort_client=lambda proc, timeout: events.append(("abort", proc, timeout)),
    )

    with pytest.raises(managed.RuntimeManagedProcessError):
        managed.run_managed_process(_spec(), ports=ports, platform_name="linux")

    assert [event[0] for event in events] == ["settle", "settle", "settle"]


def test_首次清算失败时终止客户端并再次证明unit死亡() -> None:
    events: list[object] = []
    alive = True
    settle_count = 0

    class Process:
        def poll(self) -> int | None:
            return None if alive else 0

    process = Process()

    @contextmanager
    def lock(_unit: str, _timeout_sec: float):
        yield

    def settle(unit: str, timeout: float) -> None:
        nonlocal settle_count
        settle_count += 1
        events.append(("settle", unit, timeout))
        if settle_count == 2:
            raise OSError("首次清算失败")

    def abort(proc: object, timeout: float) -> None:
        nonlocal alive
        events.append(("abort", proc, timeout))
        alive = False

    ports = managed.ManagedProcessPorts(
        lock_unit=lock,
        settle_unit=settle,
        spawn_unit=lambda _spec, _unit: process,
        collect_output=lambda *_args: (_ for _ in ()).throw(TimeoutError("执行失败")),
        abort_client=abort,
    )

    with pytest.raises(managed.RuntimeManagedProcessError) as caught:
        managed.run_managed_process(_spec(), ports=ports, platform_name="linux")

    assert type(caught.value) is managed.RuntimeManagedProcessError
    assert [event[0] for event in events] == ["settle", "settle", "abort", "settle"]


def test_最终仍无法证明unit死亡时返回独立安全状态错误() -> None:
    process = SimpleNamespace(poll=lambda: 0)

    @contextmanager
    def lock(_unit: str, _timeout_sec: float):
        yield

    calls = 0

    def settle(_unit: str, _timeout: float) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("始终无法证明")

    ports = managed.ManagedProcessPorts(
        lock_unit=lock,
        settle_unit=settle,
        spawn_unit=lambda _spec, _unit: process,
        collect_output=lambda *_args: (_ for _ in ()).throw(TimeoutError("执行失败")),
        abort_client=lambda *_args: None,
    )

    with pytest.raises(managed.RuntimeManagedProcessStateError) as caught:
        managed.run_managed_process(_spec(), ports=ports, platform_name="linux")

    assert str(caught.value) == "systemd 受管进程安全状态未证明"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(), MemoryError()])
def test_中断期间最终死亡证明失败时安全状态错误优先(
    interruption: BaseException,
) -> None:
    process = SimpleNamespace(poll=lambda: 0)
    calls = 0

    @contextmanager
    def lock(_unit: str, _timeout_sec: float):
        yield

    def settle(_unit: str, _timeout: float) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("安全状态未知")

    ports = managed.ManagedProcessPorts(
        lock_unit=lock,
        settle_unit=settle,
        spawn_unit=lambda _spec, _unit: process,
        collect_output=lambda *_args: (_ for _ in ()).throw(interruption),
        abort_client=lambda *_args: None,
    )

    with pytest.raises(managed.RuntimeManagedProcessStateError) as caught:
        managed.run_managed_process(_spec(), ports=ports, platform_name="linux")

    assert str(caught.value) == "systemd 受管进程安全状态未证明"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_非root默认入口在创建锁或调用systemd前失败(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(managed.os, "geteuid", lambda: 1001, raising=False)
    monkeypatch.setattr(
        managed,
        "default_ports",
        lambda: pytest.fail("非 root 不得构造生产端口"),
    )

    with pytest.raises(managed.RuntimeManagedProcessError):
        managed.run_managed_process(_spec(), platform_name="linux")
