"""WSL 正式部署脚本的可信启动与薄入口契约。"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


_ROOT = Path(__file__).resolve().parents[1]
_SERVER_INSTALLER = _ROOT / "scripts" / "install-wsl-runtime.sh"
_WINDOWS_LAUNCHER = _ROOT / "scripts" / "install-wsl-runtime.bat"


def test_服务器脚本固定只跟随fuwuqi_dev且拒绝github() -> None:
    content = _SERVER_INSTALLER.read_text(encoding="utf-8")

    assert 'PRODUCTION_REMOTE="fuwuqi"' in content
    assert 'PRODUCTION_BRANCH="dev"' in content
    assert "refs/remotes/${PRODUCTION_REMOTE}/${PRODUCTION_BRANCH}" in content
    assert "github.com" in content
    assert "--ff-only" in content
    assert "origin" not in content


def test_root只执行目标提交生成的只读控制器快照() -> None:
    content = _SERVER_INSTALLER.read_text(encoding="utf-8")

    assert 'RUNTIME_ROOT="/var/lib/codev-platform/runtime"' in content
    assert 'CONTROLLER_PARENT="$RUNTIME_ROOT/deployers"' in content
    assert "git" in content and "archive" in content
    assert "--no-same-owner" in content
    assert "chown -R root:root" in content
    assert "chmod -R u=rwX,go=rX" in content
    assert "PYTHONDONTWRITEBYTECODE=1" in content
    assert "sys.path.insert(0, controller)" in content
    assert 'sys.path.insert(0, "$SOURCE_REPO")' not in content


def test_脚本只准备制品和计划并调用唯一生产部署入口() -> None:
    content = _SERVER_INSTALLER.read_text(encoding="utf-8")

    assert "runtime lock" in content
    assert "runtime deploy --plan" in content
    assert "requirements/wsl-runtime.freeze" in content
    assert "requirements-runtime.txt" in content
    assert "/srv/codev-artifacts/wheelhouse" in content
    assert "/srv/codev-artifacts/wsl-runtime.lock" in content
    assert "pip install" not in content
    assert "systemctl" not in content
    assert "reindex-queue" not in content
    assert "migrate-database" not in content


def test_计划按目标提交持久化且首次部署有独立回滚基线() -> None:
    content = _SERVER_INSTALLER.read_text(encoding="utf-8")

    assert 'PLAN_PATH="$PLAN_ROOT/$TARGET_COMMIT.json"' in content
    assert "snapshot_current_release" in content
    assert '"${TARGET_COMMIT}^"' in content
    assert "baseline_revision" in content
    assert "existing.baseline_revision" in content
    assert "require_cuda" in content
    assert "--cpu" in content


def test_服务器脚本加双层锁并拒绝服务账号工作树漂移() -> None:
    content = _SERVER_INSTALLER.read_text(encoding="utf-8")
    main_body = content[content.index("main() {") :]

    assert main_body.index("flock -n 9") < main_body.index("prepare_source_repository")
    assert "runuser" in content
    assert "--untracked-files=no" in content
    assert "必须以 root 运行" in content
    assert "已有正式部署任务正在执行" in content
    assert "require_command setpriv" in content


def test_git网络访问必须无交互且有总超时() -> None:
    content = _SERVER_INSTALLER.read_text(encoding="utf-8")

    assert 'GIT_FETCH_TIMEOUT_SEC="120"' in content
    assert '"GIT_TERMINAL_PROMPT=0"' in content
    assert '"GCM_INTERACTIVE=Never"' in content
    assert '"GIT_ASKPASS=/bin/false"' in content
    assert 'run_git_bounded "$GIT_FETCH_TIMEOUT_SEC" fetch' in content
    assert "timeout --foreground --signal=TERM --kill-after=5" in content
    assert "require_command timeout" in content


def test_windows入口只负责把参数转交给服务器脚本() -> None:
    content = _WINDOWS_LAUNCHER.read_text(encoding="utf-8-sig")
    lowered = content.lower()

    assert "chcp 65001" in lowered
    assert "安装完成" in content
    assert "install-wsl-runtime.sh" in lowered
    assert "wslpath" in lowered
    assert "wsl.exe" in lowered
    assert "-d ubuntu" in lowered
    assert "%*" in content
    assert "pip install" not in lowered
    assert "systemctl" not in lowered


def test_windows入口保持utf8_bom与crlf避免cmd误解析() -> None:
    content = _WINDOWS_LAUNCHER.read_bytes()

    assert content.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in content.replace(b"\r\n", b"")


def test_linux服务器脚本通过bash语法和帮助入口() -> None:
    if os.name == "nt":
        pytest.skip("Windows 的 bash.exe 是 WSL 转发器，不接受 Windows 脚本路径")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("当前平台没有 bash")

    subprocess.run([bash, "-n", str(_SERVER_INSTALLER)], check=True)
    result = subprocess.run(
        [bash, str(_SERVER_INSTALLER), "--help"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert "用法：" in result.stdout
    assert "--baseline" in result.stdout
    assert "--cpu" in result.stdout
