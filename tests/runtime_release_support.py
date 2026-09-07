"""内容寻址运行时发布测试的共享构造器。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform.runtime_release as runtime_release


POSIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="符号链接切换仅在 POSIX 上执行")

RELEASE_A = "a" * 64
RELEASE_B = "b" * 64
RELEASE_C = "d" * 64
BASE_ID = "c" * 64


def runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    (root / "releases" / RELEASE_A).mkdir(parents=True)
    (root / "releases" / RELEASE_B).mkdir(parents=True)
    (root / "releases" / RELEASE_C).mkdir(parents=True)
    return root


def install_verifier(
    monkeypatch: pytest.MonkeyPatch,
    base_by_release: dict[str, str] | None = None,
) -> None:
    mapping = base_by_release or {
        RELEASE_A: BASE_ID,
        RELEASE_B: BASE_ID,
        RELEASE_C: BASE_ID,
    }

    def read_base_id(root: Path, release_id: str) -> str:
        assert (root / "releases" / release_id).is_dir()
        return mapping[release_id]

    def verify_base(_root: Path, base_id: str) -> SimpleNamespace:
        return SimpleNamespace(base_id=base_id)

    def verify(
        root: Path,
        release_id: str,
        *,
        verified_base: SimpleNamespace,
    ) -> SimpleNamespace:
        assert (root / "releases" / release_id).is_dir()
        assert verified_base.base_id == mapping[release_id]
        return SimpleNamespace(release_id=release_id, base_id=mapping[release_id])

    monkeypatch.setattr(runtime_release, "_read_release_base_id_locked", read_base_id)
    monkeypatch.setattr(runtime_release, "_verify_base_locked", verify_base)
    monkeypatch.setattr(runtime_release, "_verify_release_locked", verify)


@contextmanager
def unlocked(_root: Path):
    yield


def link_target(root: Path, name: str) -> str | None:
    path = root / name
    return os.readlink(path) if path.is_symlink() else None
