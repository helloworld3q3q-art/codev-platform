"""运行时发布测试共享的具体构造辅助。"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

from codev_platform.core.runtime_models import (
    BaseMetadata,
    ReleaseMetadata,
    write_base_metadata_atomic,
    write_release_metadata_atomic,
)
from codev_platform.gateway.auth import Identity, Unauthorized


def write_json(path: Path, data: Mapping[str, object]) -> Path:
    """写入供严格解码测试使用的 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, sort_keys=True),
        encoding="utf-8",
    )
    return path


def init_git_repo(root: Path) -> Path:
    """建立带单次提交的最小 Git 仓并返回仓库根。"""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "runtime@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Runtime Test"], cwd=root, check=True)
    (root / "README.md").write_text("运行时测试\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "初始化"], cwd=root, check=True)
    return root


def minimal_config(root: Path) -> dict[str, object]:
    """返回运行时测试可复用的最小平台配置。"""
    return {
        "runtime": {
            "release_root": str(root),
            "chroma_venv": str(root / "legacy" / ".venv"),
        },
        "projects": {},
    }


class AcceptingAuthenticator:
    """始终返回固定身份的认证器。"""

    def authenticate(self, headers: Mapping[str, str], query: str = "") -> Identity:
        return Identity(
            user_id="runtime-user",
            org_id="runtime-org",
            via="test",
            all_projects=True,
        )


class RejectingAuthenticator:
    """始终拒绝请求的认证器。"""

    def authenticate(self, headers: Mapping[str, str], query: str = "") -> Identity:
        raise Unauthorized("测试认证器拒绝请求")


def write_fake_base(root: Path, metadata: BaseMetadata) -> Path:
    """创建最小基座目录和类型化元数据。"""
    base_dir = root / "bases" / metadata.base_id
    python_path = base_dir / metadata.python_relative
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("", encoding="utf-8")
    (base_dir / metadata.purelib_relative).mkdir(parents=True, exist_ok=True)
    (base_dir / metadata.bin_relative).mkdir(parents=True, exist_ok=True)
    lock_path = base_dir / metadata.lock_relative
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("demo==1.0\n", encoding="utf-8")
    write_base_metadata_atomic(base_dir / "base.json", metadata)
    return base_dir


def write_fake_release(root: Path, metadata: ReleaseMetadata) -> Path:
    """创建最小版本目录和类型化元数据。"""
    release_dir = root / "releases" / metadata.release_id
    python_path = release_dir / metadata.python_relative
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("", encoding="utf-8")
    (release_dir / metadata.purelib_relative).mkdir(parents=True, exist_ok=True)
    (release_dir / metadata.base_link_relative).mkdir(parents=True, exist_ok=True)
    pth_path = release_dir / metadata.base_pth_relative
    pth_path.parent.mkdir(parents=True, exist_ok=True)
    pth_path.write_text("base\n", encoding="utf-8")
    write_release_metadata_atomic(release_dir / "release.json", metadata)
    return release_dir


def assert_path_inside(path: Path, parent: Path) -> None:
    """断言路径解析后仍位于给定根目录内。"""
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise AssertionError(f"路径逃逸根目录：{path}") from exc
