"""服务账号解析与唯一 setpriv 启动链契约测试。"""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import codev_platform.runtime_service_process as service_process


def _account() -> service_process.ServiceAccount:
    return service_process.ServiceAccount(
        name="codev-worker",
        uid=1001,
        gid=1002,
        home=Path("/home/codev-worker"),
    )


def test_服务账号是冻结值对象并拒绝特权或越界身份() -> None:
    account = _account()

    assert account.name == "codev-worker"
    assert account.uid == 1001
    assert account.gid == 1002
    with pytest.raises(FrozenInstanceError):
        account.uid = 1003  # type: ignore[misc]

    invalid = (
        {"name": "root", "uid": 1001, "gid": 1002, "home": Path("/root")},
        {"name": "worker", "uid": 0, "gid": 1002, "home": Path("/home/worker")},
        {"name": "worker", "uid": 1001, "gid": 0, "home": Path("/home/worker")},
        {
            "name": "worker",
            "uid": 2**32 - 1,
            "gid": 1002,
            "home": Path("/home/worker"),
        },
        {"name": "bad/name", "uid": 1001, "gid": 1002, "home": Path("/home/worker")},
        {"name": "..", "uid": 1001, "gid": 1002, "home": Path("/home/worker")},
        {"name": "BadWorker", "uid": 1001, "gid": 1002, "home": Path("/home/worker")},
        {"name": "worker", "uid": 1001, "gid": 1002, "home": Path("relative")},
    )
    for values in invalid:
        with pytest.raises(service_process.RuntimeServiceProcessError, match="服务账号"):
            service_process.ServiceAccount(**values)


def test_服务账号解析严格绑定pwd返回的名称与身份(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = SimpleNamespace(
        pw_name="codev-worker",
        pw_uid=1001,
        pw_gid=1002,
        pw_dir="/home/codev-worker",
    )
    monkeypatch.setattr(service_process, "_lookup_account", lambda name: (name, record)[1])

    account = service_process.resolve_service_account("codev-worker")

    assert account == _account()


@pytest.mark.parametrize("user", ["", "root", "..", "bad/name", " worker", None, 1001])
def test_服务账号解析在查询系统账号前拒绝非法名称(
    user: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_process,
        "_lookup_account",
        lambda _name: pytest.fail("非法用户名不得查询系统账号"),
    )

    with pytest.raises(service_process.RuntimeServiceProcessError, match="服务账号"):
        service_process.resolve_service_account(user)  # type: ignore[arg-type]


def test_setpriv启动链固定清组清能力并按键排序最终环境(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_process, "_require_linux_root", lambda: None)
    monkeypatch.setattr(
        service_process,
        "_verified_fixed_launchers",
        lambda: (Path("/usr/bin/setpriv"), Path("/usr/bin/env")),
    )
    command = ("/runtime/releases/a/venv/bin/python", "-I", "-B", "-c", "pass")

    argv = service_process.build_service_process_argv(
        _account(),
        command,
        {"Z_LAST": "2", "A_FIRST": "1", "PATH": "/usr/bin:/bin"},
    )

    assert argv == (
        "/usr/bin/setpriv",
        "--reuid=1001",
        "--regid=1002",
        "--clear-groups",
        "--no-new-privs",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "/usr/bin/env",
        "-i",
        "A_FIRST=1",
        "PATH=/usr/bin:/bin",
        "Z_LAST=2",
        *command,
    )


@pytest.mark.parametrize(
    ("command", "environment"),
    [
        ((), {"A": "1"}),
        (("relative-python",), {"A": "1"}),
        (("/usr/bin/python\x00",), {"A": "1"}),
        (("/usr/bin/python", 1), {"A": "1"}),
        (("/usr/bin/python",), {"": "1"}),
        (("/usr/bin/python",), {"A=B": "1"}),
        (("/usr/bin/python",), {"A": "1\x00"}),
        (("/usr/bin/python",), {"A": 1}),
    ],
)
def test_setpriv构造器拒绝可歧义命令与环境(
    command: tuple[object, ...],
    environment: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_process, "_require_linux_root", lambda: None)
    monkeypatch.setattr(
        service_process,
        "_verified_fixed_launchers",
        lambda: (Path("/usr/bin/setpriv"), Path("/usr/bin/env")),
    )

    with pytest.raises(service_process.RuntimeServiceProcessError, match="服务进程"):
        service_process.build_service_process_argv(  # type: ignore[arg-type]
            _account(),
            command,
            environment,
        )


def test_异常Mapping实现不会泄露底层迭代错误(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenMapping(dict[str, str]):
        def items(self):  # type: ignore[override]
            return iter((("SAFE", "1"), ("broken",)))

    monkeypatch.setattr(service_process, "_require_linux_root", lambda: None)
    monkeypatch.setattr(
        service_process,
        "_verified_fixed_launchers",
        lambda: (Path("/usr/bin/setpriv"), Path("/usr/bin/env")),
    )

    with pytest.raises(service_process.RuntimeServiceProcessError) as caught:
        service_process.build_service_process_argv(
            _account(),
            ("/usr/bin/python3",),
            BrokenMapping(),
        )

    assert str(caught.value) == "服务进程环境无效"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_setpriv构造器要求Linux_root且验证两个固定启动器(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified: list[Path] = []
    monkeypatch.setattr(service_process.os, "geteuid", lambda: 1001, raising=False)

    with pytest.raises(service_process.RuntimeServiceProcessError, match="Linux root"):
        service_process.build_service_process_argv(_account(), ("/usr/bin/python3",), {})

    monkeypatch.setattr(service_process, "_require_linux_root", lambda: None)
    monkeypatch.setattr(
        service_process,
        "verify_root_controlled_executable",
        lambda path: verified.append(path) or path,
    )

    service_process.build_service_process_argv(_account(), ("/usr/bin/python3",), {})

    assert verified == [Path("/usr/bin/setpriv"), Path("/usr/bin/env")]


@pytest.mark.skipif(
    os.name != "posix"
    or not sys.platform.startswith("linux")
    or not hasattr(os, "geteuid")
    or os.geteuid() != 0
    or not Path("/usr/bin/python3").is_file(),
    reason="需要 Linux root 与系统 Python",
)
def test_真实setpriv子进程身份环境与五类能力全部收敛() -> None:
    account = service_process.resolve_service_account("nobody")
    script = """
import os

status = {}
with open('/proc/self/status', encoding='ascii') as stream:
    for line in stream:
        key, separator, value = line.partition(':')
        if separator:
            status[key] = value.strip()
assert os.getuid() == os.geteuid() == int(os.environ['EXPECTED_UID'])
assert os.getgid() == os.getegid() == int(os.environ['EXPECTED_GID'])
assert os.getresuid() == (int(os.environ['EXPECTED_UID']),) * 3
assert os.getresgid() == (int(os.environ['EXPECTED_GID']),) * 3
assert os.getgroups() == []
assert status['NoNewPrivs'] == '1'
assert status['Uid'].split() == [os.environ['EXPECTED_UID']] * 4
assert status['Gid'].split() == [os.environ['EXPECTED_GID']] * 4
assert all(int(status[key], 16) == 0 for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'))
assert set(os.environ) == {'EXPECTED_UID', 'EXPECTED_GID', 'HOME', 'LANG', 'LC_ALL', 'PATH'}
print('ok')
"""
    environment = {
        "EXPECTED_UID": str(account.uid),
        "EXPECTED_GID": str(account.gid),
        "HOME": "/",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }
    argv = service_process.build_service_process_argv(
        account,
        ("/usr/bin/python3", "-I", "-B", "-c", script),
        environment,
    )

    completed = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=10,
        env={},
    )

    assert completed.returncode == 0
    assert completed.stdout == b"ok\n"
    assert completed.stderr == b""
