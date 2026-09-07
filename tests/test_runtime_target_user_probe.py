"""真实目标服务用户运行时探针的锁、身份与有界协议测试。"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from codev_platform.core.runtime_models import RUNTIME_ACCESS_PROFILE, sha256_file
from codev_platform.runtime_managed_process import ManagedProcessResult
from codev_platform.runtime_service_process import ServiceAccount
import codev_platform.runtime_target_user_probe as target_probe
from tests.runtime_target_user_probe_support import build_target_probe_runtime


_BASE_ID, _RELEASE_ID = "b" * 64, "a" * 64
_SUCCESS = b"CODEV_PLATFORM_TARGET_USER_PROBE_OK_V1\n"


def _account() -> ServiceAccount:
    return ServiceAccount("codev-worker", 1001, 1002, Path("/home/codev-worker"))


def _runtime_layout(tmp_path: Path) -> tuple[Path, object, object]:
    root = (tmp_path / "runtime").resolve()
    release_dir = root / "releases" / _RELEASE_ID
    base_dir = root / "bases" / _BASE_ID
    python = release_dir / "venv/bin/python"
    app_purelib = release_dir / "venv/lib/python/site-packages"
    base_purelib = base_dir / "venv/lib/python/site-packages"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"python")
    app_purelib.mkdir(parents=True)
    base_purelib.mkdir(parents=True)
    base_link = release_dir / "base"
    base_link.symlink_to(Path("..") / ".." / "bases" / _BASE_ID, target_is_directory=True)
    base_pth = app_purelib / "codev_platform_base.pth"
    base_pth.write_text(base_purelib.as_posix() + "\n", encoding="utf-8")
    (release_dir / "release.json").write_text("release-v1\n", encoding="ascii")
    (base_dir / "base.json").write_text("base-v1\n", encoding="ascii")
    base = SimpleNamespace(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=_BASE_ID,
        requirements_sha256="c" * 64,
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
    )
    release = SimpleNamespace(
        schema_version=1,
        release_id=_RELEASE_ID,
        runtime_revision="d" * 40,
        wheel_sha256="e" * 64,
        base_id=_BASE_ID,
        base_requirements_sha256=base.requirements_sha256,
        python_relative="venv/bin/python",
        purelib_relative="venv/lib/python/site-packages",
        base_link_relative="base",
        base_pth_relative="venv/lib/python/site-packages/codev_platform_base.pth",
    )
    return root, base, release


def _ports(
    root: Path,
    base: object,
    release: object,
    events: list[object],
    *,
    output: ManagedProcessResult | None = None,
) -> object:
    @contextmanager
    def id_lock(selected: Path, kind: str, object_id: str, *, shared: bool):
        events.append(("lock-enter", kind, object_id, shared))
        assert selected == root
        try:
            yield
        finally:
            events.append(("lock-exit", kind, object_id, shared))

    def read_base(selected: Path, release_id: str) -> str:
        events.append(("read-base", release_id))
        assert selected == root
        assert release_id == release.release_id
        return release.base_id

    def verify_base(selected: Path, base_id: str) -> object:
        events.append(("verify-base", base_id))
        assert selected == root
        assert base_id == base.base_id
        return base

    def verify_release(
        selected: Path,
        release_id: str,
        *,
        verified_base: object,
    ) -> object:
        events.append(("verify-release", release_id))
        assert selected == root
        assert verified_base is base
        assert release_id == release.release_id
        return release

    def build_argv(
        account: ServiceAccount,
        command: tuple[str, ...],
        environment: dict[str, str],
    ) -> tuple[str, ...]:
        events.append(("build", account, command, environment))
        return ("/usr/bin/setpriv", "fixed")

    def run_probe(argv: tuple[str, ...]) -> ManagedProcessResult:
        events.append(("run", argv))
        return output or ManagedProcessResult(0, _SUCCESS, b"")

    return SimpleNamespace(
        id_lock=id_lock,
        read_release_base_id_locked=read_base,
        verify_base_locked=verify_base,
        verify_release_locked=verify_release,
        sha256_file=sha256_file,
        build_service_process_argv=build_argv,
        run_probe=run_probe,
    )


def _install_public_fakes(monkeypatch: pytest.MonkeyPatch, ports: object) -> None:
    monkeypatch.setattr(target_probe, "_require_linux_root", lambda: None)
    monkeypatch.setattr(target_probe, "_default_ports", lambda: ports)


def test_探针按release再base共享锁执行一次静态深验和事后快照(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, release = _runtime_layout(tmp_path)
    events: list[object] = []
    ports = _ports(root, base, release, events)
    _install_public_fakes(monkeypatch, ports)
    proof = target_probe.probe_runtime_target_user(
        root,
        account=_account(),
        base_id=_BASE_ID,
        release_id=_RELEASE_ID,
    )
    assert [event[:2] for event in events if isinstance(event, tuple)] == [
        ("lock-enter", "release"),
        ("read-base", _RELEASE_ID),
        ("lock-enter", "base"),
        ("verify-base", _BASE_ID),
        ("verify-release", _RELEASE_ID),
        ("build", _account()),
        ("run", ("/usr/bin/setpriv", "fixed")),
        ("read-base", _RELEASE_ID),
        ("verify-base", _BASE_ID),
        ("verify-release", _RELEASE_ID),
        ("lock-exit", "base"),
        ("lock-exit", "release"),
    ]
    assert sum(event[0] == "verify-base" for event in events) == 2
    assert sum(event[0] == "verify-release" for event in events) == 2
    build = next(event for event in events if event[0] == "build")
    command = build[2]
    environment = build[3]
    assert command[0] == os.fspath(root / "releases" / _RELEASE_ID / "venv/bin/python")
    assert command[1:4] == ("-I", "-B", "-c")
    assert environment == {
        "CODEV_PLATFORM_RELEASE_FILE": os.fspath(root / "releases" / _RELEASE_ID / "release.json"),
        "HOME": "/",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }
    assert proof.access_profile == RUNTIME_ACCESS_PROFILE
    assert proof.service_uid == 1001
    assert proof.service_gid == 1002
    assert proof.base_id == _BASE_ID
    assert proof.release_id == _RELEASE_ID
    assert proof.runtime_revision == "d" * 40
    assert len(proof.evidence_sha256) == 64
    assert not any(isinstance(value, Path) for value in dataclasses.asdict(proof).values())


def test_release引用基座不一致时不得获取base锁或启动进程(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, release = _runtime_layout(tmp_path)
    events: list[object] = []
    ports = _ports(root, base, release, events)

    def wrong_base(_root: Path, release_id: str) -> str:
        events.append(("read-base", release_id))
        return "f" * 64

    ports.read_release_base_id_locked = wrong_base
    _install_public_fakes(monkeypatch, ports)
    with pytest.raises(target_probe.RuntimeTargetUserProbeError) as caught:
        target_probe.probe_runtime_target_user(
            root,
            account=_account(),
            base_id=_BASE_ID,
            release_id=_RELEASE_ID,
        )
    assert str(caught.value) == "目标服务用户运行时探针失败"
    assert [event[:2] for event in events] == [
        ("lock-enter", "release"),
        ("read-base", _RELEASE_ID),
        ("lock-exit", "release"),
    ]


@pytest.mark.parametrize(
    ("root_factory", "base_id", "release_id"),
    [
        (lambda root: Path("relative-runtime"), _BASE_ID, _RELEASE_ID),
        (lambda root: root, "0" * 64, _RELEASE_ID),
        (lambda root: root, _BASE_ID, "short"),
    ],
)
def test_非法根或对象身份在创建端口和获取锁前失败(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_factory: object,
    base_id: str,
    release_id: str,
) -> None:
    root, _base, _release = _runtime_layout(tmp_path)
    monkeypatch.setattr(target_probe, "_require_linux_root", lambda: None)
    monkeypatch.setattr(
        target_probe,
        "_default_ports",
        lambda: pytest.fail("非法输入不得创建探针端口"),
    )
    with pytest.raises(target_probe.RuntimeTargetUserProbeError):
        target_probe.probe_runtime_target_user(
            root_factory(root),  # type: ignore[operator]
            account=_account(),
            base_id=base_id,
            release_id=release_id,
        )


def test_静态模型不支持时不得构造或执行子进程(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, release = _runtime_layout(tmp_path)
    base.access_profile = "legacy-owner-only"
    events: list[object] = []
    ports = _ports(root, base, release, events)
    _install_public_fakes(monkeypatch, ports)
    with pytest.raises(target_probe.RuntimeTargetUserProbeError):
        target_probe.probe_runtime_target_user(
            root,
            account=_account(),
            base_id=_BASE_ID,
            release_id=_RELEASE_ID,
        )
    assert not any(event[0] in {"build", "run"} for event in events)


@pytest.mark.skipif(
    os.name != "posix" or not Path("/usr/bin/python3").is_file(),
    reason="需要 POSIX 词法解释器符号链接",
)
def test_venv解释器为符号链接时argv仍使用release内词法路径(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, release = _runtime_layout(tmp_path)
    lexical = root / "releases" / _RELEASE_ID / "venv/bin/python"
    lexical.unlink()
    lexical.symlink_to("/usr/bin/python3")
    events: list[object] = []
    ports = _ports(root, base, release, events)
    _install_public_fakes(monkeypatch, ports)
    target_probe.probe_runtime_target_user(
        root,
        account=_account(),
        base_id=_BASE_ID,
        release_id=_RELEASE_ID,
    )
    command = next(event[2] for event in events if event[0] == "build")
    assert command[0] == os.fspath(lexical)
    assert Path(command[0]) != Path(command[0]).resolve(strict=True)


def test_探针执行后元数据或关键词法路径漂移均失败关闭(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, release = _runtime_layout(tmp_path)
    events: list[object] = []
    ports = _ports(root, base, release, events)

    def drift(_argv: tuple[str, ...]) -> ManagedProcessResult:
        (root / "releases" / _RELEASE_ID / "release.json").write_text(
            "secret-drift\n",
            encoding="ascii",
        )
        return ManagedProcessResult(0, _SUCCESS, b"")

    ports.run_probe = drift
    _install_public_fakes(monkeypatch, ports)

    with pytest.raises(target_probe.RuntimeTargetUserProbeError) as caught:
        target_probe.probe_runtime_target_user(
            root,
            account=_account(),
            base_id=_BASE_ID,
            release_id=_RELEASE_ID,
        )

    assert str(caught.value) == "目标服务用户运行时探针失败"
    assert "secret" not in str(caught.value)


def test_探针执行后完整静态深验能发现嵌套模块同长度漂移(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, release = _runtime_layout(tmp_path)
    managed = root / "releases" / _RELEASE_ID / release.purelib_relative / "managed.py"
    managed.write_text("VALUE=1\n", encoding="ascii")
    ports = _ports(root, base, release, [])
    original_verify = ports.verify_release_locked
    verify_count = 0

    def verify_release(*args: object, **kwargs: object) -> object:
        nonlocal verify_count
        verify_count += 1
        selected = original_verify(*args, **kwargs)
        if managed.read_text(encoding="ascii") != "VALUE=1\n":
            raise RuntimeError("release 内容漂移")
        return selected

    def drift(argv: tuple[str, ...]) -> ManagedProcessResult:
        managed.write_text("VALUE=2\n", encoding="ascii")
        return ManagedProcessResult(0, _SUCCESS, b"")

    ports.verify_release_locked = verify_release
    ports.run_probe = drift
    _install_public_fakes(monkeypatch, ports)

    with pytest.raises(target_probe.RuntimeTargetUserProbeError):
        target_probe.probe_runtime_target_user(
            root,
            account=_account(),
            base_id=_BASE_ID,
            release_id=_RELEASE_ID,
        )
    assert verify_count == 2


@pytest.mark.parametrize("selected", ["base-link", "base-pth"])
def test_探针执行后基座链接或路径注入文件漂移均失败关闭(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selected: str,
) -> None:
    root, base, release = _runtime_layout(tmp_path)
    ports = _ports(root, base, release, [])
    mutations: list[str] = []

    def drift(argv: tuple[str, ...]) -> ManagedProcessResult:
        release_root = root / "releases" / _RELEASE_ID
        if selected == "base-pth":
            (release_root / release.base_pth_relative).write_text(
                "/private/drift\n",
                encoding="utf-8",
            )
        else:
            link = release_root / release.base_link_relative
            link.unlink()
            link.symlink_to("/private/drift", target_is_directory=True)
        mutations.append(selected)
        return ManagedProcessResult(0, _SUCCESS, b"")

    ports.run_probe = drift
    _install_public_fakes(monkeypatch, ports)

    with pytest.raises(target_probe.RuntimeTargetUserProbeError):
        target_probe.probe_runtime_target_user(
            root,
            account=_account(),
            base_id=_BASE_ID,
            release_id=_RELEASE_ID,
        )
    assert mutations == [selected]


@pytest.mark.parametrize(
    "completed",
    [
        ManagedProcessResult(1, _SUCCESS, b""),
        ManagedProcessResult(0, b"", b""),
        ManagedProcessResult(0, _SUCCESS + b"\n", b""),
        ManagedProcessResult(0, _SUCCESS.replace(b"\n", b"\r\n"), b""),
        ManagedProcessResult(0, _SUCCESS, b"warning"),
        ManagedProcessResult(0, _SUCCESS[:-2] + b"\xff\n", b""),
    ],
)
def test_只接受唯一固定ASCII成功行且stderr必须为空(
    completed: ManagedProcessResult,
) -> None:
    with pytest.raises(target_probe.RuntimeTargetUserProbeError) as caught:
        target_probe._require_success_output(completed)

    assert str(caught.value) == "目标服务用户运行时探针失败"


def test_任意底层错误统一脱敏且不保留cause正文(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, base, release = _runtime_layout(tmp_path)
    ports = _ports(root, base, release, [])
    ports.run_probe = lambda _argv: (_ for _ in ()).throw(
        RuntimeError("TOKEN=secret argv=/runtime/private stderr=leak")
    )
    _install_public_fakes(monkeypatch, ports)

    with pytest.raises(target_probe.RuntimeTargetUserProbeError) as caught:
        target_probe.probe_runtime_target_user(
            root,
            account=_account(),
            base_id=_BASE_ID,
            release_id=_RELEASE_ID,
        )

    assert str(caught.value) == "目标服务用户运行时探针失败"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert all(word not in str(caught.value) for word in ("TOKEN", "secret", "argv", "stderr"))


def test_执行器固定空父环境根工作目录和120秒截止时间(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    def run(spec: object) -> object:
        calls.append(spec)
        return ManagedProcessResult(0, _SUCCESS, b"")

    monkeypatch.setattr(target_probe, "run_managed_process", run)

    completed = target_probe._run_probe_process(("/runtime/venv/bin/python",))

    assert completed.stdout == _SUCCESS
    spec = calls[0]
    assert spec.argv == ("/runtime/venv/bin/python",)
    assert spec.unit_slot == "target-user-probe"
    assert spec.working_directory == "/"
    assert spec.environment == ()
    assert spec.limits.runtime_sec == 120.0
    assert spec.limits.stdout_limit_bytes == 4096
    assert spec.limits.stderr_limit_bytes == 4096
    assert spec.limits.tasks_max == 64
    assert spec.limits.memory_max_bytes == 4 * 1024**3


def test_探针职责拆分为布局快照与子进程协议模块() -> None:
    import codev_platform.runtime_target_user_layout as layout
    import codev_platform.runtime_target_user_protocol as protocol

    assert layout.TargetProbeRequest.__module__.endswith("runtime_target_user_layout")
    assert layout.TargetProbeSnapshot.__module__.endswith("runtime_target_user_layout")
    assert protocol.PROBE_SUCCESS_OUTPUT == _SUCCESS


@pytest.mark.skipif(
    os.name != "posix"
    or not sys.platform.startswith("linux")
    or not hasattr(os, "geteuid")
    or os.geteuid() != 0
    or not Path("/usr/bin/setpriv").is_file(),
    reason="需要 Linux root、setpriv 与真实 venv",
)
def test_真实目标用户通过词法venv证明全部导入与runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = build_target_probe_runtime(tmp_path)
    events: list[object] = []
    ports = _ports(runtime.root, runtime.base, runtime.release, events)
    ports.build_service_process_argv = target_probe.build_service_process_argv
    ports.run_probe = target_probe._run_probe_process
    _install_public_fakes(monkeypatch, ports)

    proof = target_probe.probe_runtime_target_user(
        runtime.root,
        account=runtime.account,
        base_id=runtime.base.base_id,
        release_id=runtime.release.release_id,
    )

    assert proof.release_id == runtime.release.release_id
    assert proof.base_id == runtime.base.base_id
    assert proof.service_uid == runtime.account.uid
    assert proof.service_gid == runtime.account.gid


@pytest.mark.skipif(
    os.name != "posix"
    or not sys.platform.startswith("linux")
    or not hasattr(os, "geteuid")
    or os.geteuid() != 0
    or not Path("/usr/bin/setpriv").is_file(),
    reason="需要 Linux root、setpriv 与真实 venv",
)
@pytest.mark.parametrize(
    "options",
    [
        {
            "module_sources": {
                "codev_platform.cli": "import os\nos.environ['EVIL'] = '1'\n",
            },
        },
        {"managed_from_base": "codev_platform.cli"},
        {"preload_managed": "codev_platform.cli"},
        {"torch_from_app": True},
    ],
    ids=["导入后环境漂移", "managed来自base", "pth预加载managed", "app抢占torch"],
)
def test_真实目标用户拒绝导入副作用预加载或错误来源(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, object],
) -> None:
    runtime = build_target_probe_runtime(tmp_path, **options)  # type: ignore[arg-type]
    ports = _ports(runtime.root, runtime.base, runtime.release, [])
    ports.build_service_process_argv = target_probe.build_service_process_argv
    ports.run_probe = target_probe._run_probe_process
    _install_public_fakes(monkeypatch, ports)

    with pytest.raises(target_probe.RuntimeTargetUserProbeError) as caught:
        target_probe.probe_runtime_target_user(
            runtime.root,
            account=runtime.account,
            base_id=runtime.base.base_id,
            release_id=runtime.release.release_id,
        )

    assert str(caught.value) == "目标服务用户运行时探针失败"
    assert caught.value.__cause__ is None
