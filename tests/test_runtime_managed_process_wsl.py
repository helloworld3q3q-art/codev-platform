"""真实 WSL/systemd 下的资源控制、精确环境与控制器崩溃恢复测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest

from codev_platform.core.process_tree import popen_tree
from codev_platform.runtime_managed_process import (
    ManagedProcessLimits,
    ManagedProcessSpec,
    RuntimeManagedProcessError,
    run_managed_process,
)
from codev_platform.runtime_systemd_process import settle_systemd_unit


_SYSTEMD_ROOT_AVAILABLE = (
    os.name == "posix"
    and sys.platform.startswith("linux")
    and hasattr(os, "geteuid")
    and os.geteuid() == 0
    and Path("/run/systemd/system").is_dir()
    and Path("/usr/bin/systemd-run").is_file()
)
_SYSTEMD_ONLY = pytest.mark.skipif(
    not _SYSTEMD_ROOT_AVAILABLE,
    reason="需要 WSL/Linux root 与 systemd",
)


def _limits(**overrides: object) -> ManagedProcessLimits:
    values = {
        "runtime_sec": 10.0,
        "stop_sec": 2.0,
        "stdout_limit_bytes": 4096,
        "stderr_limit_bytes": 4096,
        "tasks_max": 32,
        "memory_high_bytes": 256 * 1024**2,
        "memory_max_bytes": 512 * 1024**2,
        "memory_swap_max_bytes": 0,
        "cpu_quota_percent": 100,
        **overrides,
    }
    return ManagedProcessLimits(**values)  # type: ignore[arg-type]


def _spec(slot: str, argv: tuple[str, ...], **overrides: object) -> ManagedProcessSpec:
    values = {
        "unit_slot": slot,
        "argv": argv,
        "working_directory": "/",
        "environment": (),
        "environment_file": None,
        "user": None,
        "limits": _limits(),
        **overrides,
    }
    return ManagedProcessSpec(**values)  # type: ignore[arg-type]


@_SYSTEMD_ONLY
def test_真实unit获得精确环境与五项cgroup资源边界() -> None:
    environment = run_managed_process(_spec("integration-environment", ("/usr/bin/env", "-0")))
    assert environment.returncode == 0
    assert environment.stdout == b""
    assert environment.stderr == b""

    script = r"""
import json
from pathlib import Path

line = next(item for item in Path('/proc/self/cgroup').read_text(encoding='ascii').splitlines() if '::' in item)
relative = line.split('::', 1)[1].lstrip('/')
root = Path('/sys/fs/cgroup') / relative
print(json.dumps({
    'unit': root.name,
    'pids': (root / 'pids.max').read_text(encoding='ascii').strip(),
    'high': (root / 'memory.high').read_text(encoding='ascii').strip(),
    'max': (root / 'memory.max').read_text(encoding='ascii').strip(),
    'swap': (root / 'memory.swap.max').read_text(encoding='ascii').strip(),
    'cpu': (root / 'cpu.max').read_text(encoding='ascii').strip(),
}, sort_keys=True))
"""
    result = run_managed_process(
        _spec(
            "integration-resource-proof",
            ("/usr/bin/python3", "-I", "-B", "-c", script),
        )
    )

    proof = json.loads(result.stdout)
    assert result.returncode == 0 and result.stderr == b""
    assert proof == {
        "cpu": "100000 100000",
        "high": str(256 * 1024**2),
        "max": str(512 * 1024**2),
        "pids": "32",
        "swap": "0",
        "unit": "codev-runtime-integration-resource-proof.service",
    }


@_SYSTEMD_ONLY
def test_真实EnvironmentFile只把复验后的键传给payload() -> None:
    environment_file = Path(f"/run/codev-platform-test-{uuid4().hex}.env")
    environment_file.write_text("SAFE=1\n", encoding="utf-8")
    environment_file.chmod(0o600)
    try:
        result = run_managed_process(
            _spec(
                "integration-environment-file",
                ("/usr/bin/env", "-0"),
                environment_file=environment_file,
            )
        )
    finally:
        environment_file.unlink(missing_ok=True)

    assert result.returncode == 0
    assert result.stdout.split(b"\x00") == [b"SAFE=1", b""]
    assert result.stderr == b""


@_SYSTEMD_ONLY
def test_控制器SIGKILL后同slot重试先清算双fork残留再启动新任务(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "escaped.pid"
    payload = r"""
import os
import sys
import time

if os.fork() != 0:
    os._exit(0)
os.setsid()
if os.fork() != 0:
    os._exit(0)
temporary = sys.argv[1] + '.tmp'
with open(temporary, 'w', encoding='ascii') as stream:
    stream.write(str(os.getpid()))
os.replace(temporary, sys.argv[1])
time.sleep(30)
"""
    controller = f"""
from pathlib import Path
from codev_platform.runtime_managed_process import ManagedProcessLimits, ManagedProcessSpec, run_managed_process

limits = ManagedProcessLimits(30.0, 2.0, 4096, 4096, 32, 268435456, 536870912, 0, 100)
spec = ManagedProcessSpec(
    unit_slot='integration-crash-recovery',
    argv=('/usr/bin/python3', '-I', '-B', '-c', {payload!r}, {str(marker)!r}),
    working_directory='/',
    environment=(),
    environment_file=None,
    user=None,
    limits=limits,
)
run_managed_process(spec)
"""
    process = popen_tree(
        (sys.executable, "-c", controller),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    deadline = time.monotonic() + 10.0
    while not marker.is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.is_file()
    escaped_pid = int(marker.read_text(encoding="ascii"))

    process.kill()
    process.wait(timeout=2.0)
    result = run_managed_process(_spec("integration-crash-recovery", ("/bin/true",)))

    assert result.returncode == 0
    deadline = time.monotonic() + 2.0
    while Path(f"/proc/{escaped_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not Path(f"/proc/{escaped_pid}").exists()
    settle_systemd_unit("codev-runtime-integration-crash-recovery.service", 2.0)


@_SYSTEMD_ONLY
def test_MemoryMax触发OOM时整组失败关闭且unit可清算() -> None:
    script = "blocks=[]\nwhile True: blocks.append(bytearray(64 * 1024**2))\n"
    result = run_managed_process(
        _spec(
            "integration-oom",
            ("/usr/bin/python3", "-I", "-B", "-c", script),
            limits=_limits(
                runtime_sec=10.0,
                memory_high_bytes=64 * 1024**2,
                memory_max_bytes=64 * 1024**2,
            ),
        )
    )

    assert result.returncode != 0
    settle_systemd_unit("codev-runtime-integration-oom.service", 2.0)


@_SYSTEMD_ONLY
def test_TasksMax阻止fork风暴且已有子进程可全部回收() -> None:
    script = r"""
import errno
import os
import signal
import time

children = []
while True:
    try:
        pid = os.fork()
    except OSError as error:
        print(f'{len(children)}:{error.errno}')
        break
    if pid == 0:
        time.sleep(30)
        os._exit(0)
    children.append(pid)
for pid in children:
    os.kill(pid, signal.SIGKILL)
for pid in children:
    os.waitpid(pid, 0)
"""
    result = run_managed_process(
        _spec(
            "integration-tasks",
            ("/usr/bin/python3", "-I", "-B", "-c", script),
            limits=_limits(tasks_max=4),
        )
    )

    count, error_number = result.stdout.decode("ascii").strip().split(":")
    assert result.returncode == 0 and result.stderr == b""
    assert 1 <= int(count) <= 3
    assert int(error_number) == 11
    settle_systemd_unit("codev-runtime-integration-tasks.service", 2.0)


@_SYSTEMD_ONLY
def test_RuntimeMax到期后整组终止且不遗留后台子进程(tmp_path: Path) -> None:
    marker = tmp_path / "runtime-max-child.pid"
    script = r"""
import os
import sys
import time

pid = os.fork()
if pid == 0:
    with open(sys.argv[1], 'w', encoding='ascii') as stream:
        stream.write(str(os.getpid()))
    time.sleep(30)
    os._exit(0)
time.sleep(30)
"""
    started = time.monotonic()

    with pytest.raises(RuntimeManagedProcessError):
        run_managed_process(
            _spec(
                "integration-runtime-max",
                ("/usr/bin/python3", "-I", "-B", "-c", script, str(marker)),
                limits=_limits(runtime_sec=1.0),
            )
        )

    assert time.monotonic() - started < 5.0
    assert marker.is_file()
    child_pid = int(marker.read_text(encoding="ascii"))
    deadline = time.monotonic() + 2.0
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not Path(f"/proc/{child_pid}").exists()
    settle_systemd_unit("codev-runtime-integration-runtime-max.service", 2.0)
