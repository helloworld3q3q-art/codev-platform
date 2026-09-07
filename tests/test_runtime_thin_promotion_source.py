"""日常薄发布源码门禁的脱敏输入测试。"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest

import codev_platform.runtime_thin_promotion_source as source
from codev_platform.runtime_thin_promotion import ThinPromotionError


_REVISION = "a" * 40
_WHEEL_SHA = "b" * 64
_BASELINE_REVISION = "c" * 40
_BASE_ID = "d" * 64
_BASELINE_RELEASE_ID = "e" * 64
_TARGET_RELEASE_ID = "f" * 64


def _candidate_payload() -> bytes:
    return json.dumps(
        {
            "kind": "candidate",
            "result": {
                "schema_version": 1,
                "runtime_revision": _REVISION,
                "wheel_name": "codev_platform-1.0-py3-none-any.whl",
                "wheel_sha256": _WHEEL_SHA,
            },
            "status": "ok",
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_候选构建回执必须是唯一且完整的类型化JSON() -> None:
    candidate = source._decode_candidate_receipt(_candidate_payload())

    assert candidate.runtime_revision == _REVISION
    assert candidate.wheel_sha256 == _WHEEL_SHA

    duplicate = (
        b'{"kind":"candidate","kind":"candidate","result":{},"status":"ok"}'
    )
    with pytest.raises(ThinPromotionError, match="回执无效"):
        source._decode_candidate_receipt(duplicate)


@pytest.mark.parametrize(
    ("returncode", "expected"),
    ((0, None), (1, "不接受依赖漂移"), (2, "Git 身份无法证明")),
)
def test_依赖差异只能明确拒绝或Git失败关闭(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    expected: str | None,
) -> None:
    monkeypatch.setattr(
        source,
        "_git_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess((), returncode),
    )

    if expected is None:
        source._require_no_diff(
            SimpleNamespace(),
            _REVISION,
            "c" * 40,
            ("pyproject.toml",),
            "依赖",
        )
        return
    with pytest.raises(ThinPromotionError, match=expected):
        source._require_no_diff(
            SimpleNamespace(),
            _REVISION,
            "c" * 40,
            ("pyproject.toml",),
            "依赖",
        )


def test_root候选构建使用服务账号Git桥且不降权写入候选目录(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=_candidate_payload())

    monkeypatch.setattr(source.subprocess, "run", run)
    candidate = source._build_candidate_as_root(
        SimpleNamespace(interpreter_path=Path("/runtime/current/venv/bin/python")),
        source=Path("/home/service/work/codev-platform"),
        target=_REVISION,
        output=Path("/var/lib/codev-platform/runtime/candidates"),
        account=SimpleNamespace(name="service"),
    )

    assert candidate.runtime_revision == _REVISION
    assert observed["command"] == (
        "/runtime/current/venv/bin/python",
        "-B",
        "-I",
        "-m",
        "codev_platform.cli",
        "runtime",
        "build",
        "--repo",
        "/home/service/work/codev-platform",
        "--out-dir",
        "/var/lib/codev-platform/runtime/candidates",
        "--revision",
        _REVISION,
        "--source-user",
        "service",
    )
    assert observed["kwargs"]["cwd"] == Path("/")
    assert observed["kwargs"]["env"]["PYTHONPATH"] == ""


@pytest.mark.parametrize(
    ("mode", "uid", "message"),
    (
        (0o700, 0, None),
        (0o720, 0, "不安全"),
        (0o700, 1000, "不安全"),
    ),
)
def test_日常候选根必须是既有root私有目录(
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    uid: int,
    message: str | None,
) -> None:
    root = Path(os.path.abspath("runtime-root"))
    metadata = SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=uid)
    monkeypatch.setattr(source, "_candidate_directory_metadata", lambda _path: metadata)

    if message is None:
        assert source._candidate_root(root) == root / "candidates"
        return
    with pytest.raises(ThinPromotionError, match=message):
        source._candidate_root(root)


def test_日常暂存固定复用运行时私有候选根(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = Path(os.path.abspath("runtime"))
    source_root = Path(os.path.abspath("source"))
    baseline = SimpleNamespace(
        release_id=_BASELINE_RELEASE_ID,
        runtime_revision=_BASELINE_REVISION,
        base_id=_BASE_ID,
    )
    account = SimpleNamespace(name="service")
    candidate = source._decode_candidate_receipt(_candidate_payload())
    observed: dict[str, object] = {}

    monkeypatch.setattr(source, "_require_linux_root", lambda: None)
    monkeypatch.setattr(source, "_service_account", lambda _user: account)
    monkeypatch.setattr(source, "_baseline", lambda _root: baseline)
    monkeypatch.setattr(source, "_source_repository", lambda _repo: source_root)
    monkeypatch.setattr(source, "_refresh_production_tracking_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(source, "_git_context", lambda _source, _account: SimpleNamespace())
    monkeypatch.setattr(source, "_verify_daily_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        source,
        "_candidate_root",
        lambda runtime_root: observed.setdefault("candidate_root", runtime_root / "candidates"),
    )

    def build(_baseline, *, source: Path, target: str, output: Path, account: object):
        observed["build"] = (source, target, output, account)
        return candidate

    def stage(_root, *, baseline, candidate, output):
        observed["stage"] = (baseline, candidate, output)
        return SimpleNamespace(release_id=_TARGET_RELEASE_ID)

    monkeypatch.setattr(source, "_build_candidate_as_root", build)
    monkeypatch.setattr(source, "_stage_candidate", stage)
    monkeypatch.setattr(source, "_require_unchanged_baseline", lambda *_args: None)

    result = source.stage_daily_target_release(
        runtime_root,
        repo=source_root,
        target_revision=_REVISION,
        service_user="service",
    )

    assert result.target_release_id == _TARGET_RELEASE_ID
    assert observed["candidate_root"] == runtime_root / "candidates"
    assert observed["build"] == (source_root, _REVISION, runtime_root / "candidates", account)
    assert observed["stage"] == (baseline, candidate, runtime_root / "candidates")


def test_日常薄发布先刷新生产跟踪引用再读取Git上下文(monkeypatch) -> None:
    runtime_root = Path(os.path.abspath("runtime"))
    source_root = Path(os.path.abspath("source"))
    baseline = SimpleNamespace(
        release_id=_BASELINE_RELEASE_ID,
        runtime_revision=_BASELINE_REVISION,
        base_id=_BASE_ID,
    )
    account = SimpleNamespace(name="service")
    candidate = source._decode_candidate_receipt(_candidate_payload())
    events: list[str] = []

    monkeypatch.setattr(source, "_require_linux_root", lambda: None)
    monkeypatch.setattr(source, "_service_account", lambda _user: account)
    monkeypatch.setattr(source, "_baseline", lambda _root: baseline)
    monkeypatch.setattr(source, "_source_repository", lambda _repo: source_root)
    monkeypatch.setattr(
        source,
        "_refresh_production_tracking_target",
        lambda _source, **_kwargs: events.append("refresh"),
    )
    monkeypatch.setattr(
        source,
        "_git_context",
        lambda _source, _account: events.append("context") or SimpleNamespace(),
    )
    monkeypatch.setattr(source, "_verify_daily_target", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(source, "_candidate_root", lambda root: root / "candidates")
    monkeypatch.setattr(source, "_build_candidate_as_root", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(
        source,
        "_stage_candidate",
        lambda *_args, **_kwargs: SimpleNamespace(release_id=_TARGET_RELEASE_ID),
    )
    monkeypatch.setattr(source, "_require_unchanged_baseline", lambda *_args: None)

    source.stage_daily_target_release(
        runtime_root,
        repo=source_root,
        target_revision=_REVISION,
        service_user="service",
    )

    assert events[:2] == ["refresh", "context"]


def test_生产跟踪刷新失败时不构建候选(monkeypatch) -> None:
    source_root = Path(os.path.abspath("source"))
    baseline = SimpleNamespace(
        release_id=_BASELINE_RELEASE_ID,
        runtime_revision=_BASELINE_REVISION,
        base_id=_BASE_ID,
    )

    monkeypatch.setattr(source, "_require_linux_root", lambda: None)
    monkeypatch.setattr(source, "_service_account", lambda _user: SimpleNamespace(name="service"))
    monkeypatch.setattr(source, "_baseline", lambda _root: baseline)
    monkeypatch.setattr(source, "_source_repository", lambda _repo: source_root)
    monkeypatch.setattr(
        source,
        "_refresh_production_tracking_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ThinPromotionError("日常薄发布生产 Git 目标刷新失败"),
        ),
    )
    monkeypatch.setattr(
        source,
        "_build_candidate_as_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得构建候选")),
    )

    with pytest.raises(ThinPromotionError, match="生产 Git"):
        source.stage_daily_target_release(
            Path(os.path.abspath("runtime")),
            repo=source_root,
            target_revision=_REVISION,
            service_user="service",
        )


def test_生产跟踪刷新异常映射为薄发布失败(monkeypatch) -> None:
    monkeypatch.setattr(
        source,
        "refresh_production_git_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fetch failed")),
    )

    with pytest.raises(ThinPromotionError, match="生产 Git"):
        source._refresh_production_tracking_target(
            Path("/home/service/work/codev-platform"),
            account=SimpleNamespace(name="service"),
            target=_REVISION,
        )
