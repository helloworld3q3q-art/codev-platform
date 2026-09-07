"""platform-docs MCP 后台进程启动器，使用 mcp-proxy 桥接 stdio 与 SSE。

由 platform-docs-mcp.cmd 作为每个 Claude Code 会话的 stdio MCP server 启动。
内部职责:

1. 检测 `127.0.0.1:<port>/health` 上的后台进程是否存活
2. 不活则启动一个脱离会话的后台进程，通过 `--http` 加载 Qwen 模型
3. 等待后台进程就绪，轮询 `/health` 最多 90 秒
4. 执行 mcp-proxy，使其作为 stdio MCP server，透明转发 stdio 与 SSE 到后台进程

为什么需要这个启动器:
- 8GB GPU 上每个 mcp_server 进程独立加载 Qwen 模型约占 3GB；多个 Claude Code
  会话同时启动会耗尽 CUDA 显存，后台模式让所有会话共享一份 GPU 模型。
- 对 Claude Code 透明，`.mcp.json` 仍为 stdio 类型，不依赖客户端的 HTTP 支持成熟度。

stdio 与 SSE 桥接复用开源 mcp-proxy（https://github.com/sparfenyuk/mcp-proxy），
不再自行实现转发逻辑，减少约 80 行自维护代码。

后台进程生命周期:
- 第一个会话启动时创建脱离会话的后台进程，会话关闭后该进程仍存活
- 后续会话检测到后台进程存活后，直接交给 mcp-proxy
- 系统重启或手动终止后台进程后，下一个会话会自动重新启动它
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import codev_platform.core.runtime_artifacts as runtime_artifacts
from codev_platform.chroma.spawn_lock import acquire_spawn_lock, release_spawn_lock
from codev_platform.core.runtime_artifact_io import open_runtime_artifact_binary
from codev_platform.core.project_id import ProjectIdError, resolve_local


DAEMON_PORT = int(os.getenv("PLATFORM_DOCS_DAEMON_PORT", "18083"))
DAEMON_URL = f"http://127.0.0.1:{DAEMON_PORT}"
# 审计 #4: 探活/就绪走 PUBLIC /healthz (最小, 不泄敏, token 模式也无需 Bearer)。
# /healthz 仍返回 200(ready) / 503(prewarming) + tenant_mode(非敏感常量), 满足 launcher 就绪门控。
DAEMON_HEALTH_URL = f"{DAEMON_URL}/healthz"
# DAEMON_SSE_URL 在 main() 里按 resolved project_id 拼 ?project_id= (multi-tenant)
DAEMON_SSE_URL_BASE = f"{DAEMON_URL}/sse"
DAEMON_READY_TIMEOUT = float(os.getenv("PLATFORM_DOCS_DAEMON_READY_TIMEOUT", "90"))
SCRIPT_DIR = Path(__file__).parent.resolve()
# 轻量入口先竞争 daemon 生命周期锁，失败竞争者不加载 Chroma/Torch。
DAEMON_ENTRY_MODULE = "codev_platform.chroma.daemon_entry"
DAEMON_LOG = runtime_artifacts.chroma_daemon_log_path()


def _resolve_venv() -> Path:
    """venv 解析: env PLATFORM_DOCS_VENV > config runtime.chroma_venv > 硬失败。

    venv 是 machine-local 资源 (含 chromadb / sentence-transformers / torch ~5GB),
    不在 codev-platform 代码仓内. 装在哪里由用户决定 (典型: 业务仓 tools/chroma/.venv).
    """
    env = os.environ.get("PLATFORM_DOCS_VENV")
    if env:
        return Path(env).expanduser().resolve()
    try:
        from codev_platform.core.config import load_config, get as _cfg_get

        v = _cfg_get(load_config(), "runtime.chroma_venv")
        if v:
            return Path(v).expanduser().resolve()
    except Exception:
        pass
    raise RuntimeError(
        "无法解析 chroma daemon venv 路径。请二选一:\n"
        "  (a) env: $env:PLATFORM_DOCS_VENV='D:/path/to/venv'\n"
        "  (b) ~/.codev-platform/config.json runtime.chroma_venv 字段 (codev-platform config init / edit)\n"
        "  venv 内应含 Scripts/python.exe + Scripts/mcp-proxy.exe"
    )


_VENV = _resolve_venv()
VENV_SCRIPTS = _VENV / "Scripts"
MCP_PROXY_EXE = VENV_SCRIPTS / "mcp-proxy.exe"
SPAWN_LOCK_PATH = runtime_artifacts.chroma_spawn_lock_path()
DAEMON_LIFECYCLE_LOCK_PATH = runtime_artifacts.chroma_daemon_lifecycle_lock_path()
SPAWN_LOCK_WAIT_TIMEOUT = 120  # 等其他 launcher spawn 最多 120s
DAEMON_HANDOFF_TIMEOUT = float(os.getenv("PLATFORM_DOCS_DAEMON_HANDOFF_TIMEOUT", "10"))


def _flog(msg: str) -> None:
    """stderr 日志: launcher 自己不写文件, Claude Code 会收到这些行用于诊断。"""
    print(f"[platform-docs-launcher] {msg}", file=sys.stderr, flush=True)


def _check_daemon() -> bool:
    """GET /health, 200 OK 视为 fully ready.

    daemon 启动期 (prewarm 加载模型 ~30-60s) /health 返回 503 status=starting,
    launcher 会继续等; 真不通 (connection refused) urlopen 抛异常返回 False。
    """
    try:
        with urllib.request.urlopen(DAEMON_HEALTH_URL, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError):
        return False


def _fetch_daemon_health() -> dict | None:
    """GET /health, 返回 JSON 字典 (None 表示 daemon 未起或解析失败)。"""
    try:
        with urllib.request.urlopen(DAEMON_HEALTH_URL, timeout=2) as resp:
            body = resp.read()
        return json.loads(body.decode("utf-8"))
    except (
        urllib.error.URLError,
        ConnectionRefusedError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
    ):
        return None


def _spawn_daemon() -> subprocess.Popen:
    """spawn 一个 detached daemon process. python.exe + CREATE_NO_WINDOW 避免
    PyTorch 依赖的 Intel Fortran 运行时报 forrtl error 200 abort。

    pythonw.exe + DETACHED_PROCESS 会让 Fortran lib 误判 "console window closed"
    -> 直接 abort daemon 进程。python.exe + CREATE_NO_WINDOW 既隐藏窗口又保留
    console handle (空 console), Fortran 不报错。
    """
    python = VENV_SCRIPTS / "python.exe"
    pythonw = VENV_SCRIPTS / "pythonw.exe"
    exe = str(python if python.exists() else pythonw)

    if sys.platform == "win32":
        # CREATE_NO_WINDOW (隐藏 console) | CREATE_NEW_PROCESS_GROUP (脱离父进程组)
        creationflags = 0x08000000 | 0x00000200
    else:
        creationflags = 0

    log_handle = open_runtime_artifact_binary(DAEMON_LOG)
    try:
        proc = subprocess.Popen(
            [exe, "-m", DAEMON_ENTRY_MODULE, "--http"],
            cwd=str(SCRIPT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creationflags,
            close_fds=True,
            env=os.environ.copy(),
        )
        _flog(f"spawned daemon pid={proc.pid} log={DAEMON_LOG}")
        return proc
    finally:
        log_handle.close()


def _acquire_spawn_lock():
    """非阻塞获取原生咨询锁，只允许一个 launcher 同时 spawn daemon。

    多 Claude Code session 同时 cold start 时, 没有这个锁两个 launcher 都会
    spawn daemon, 第二个 port bind 失败浪费几十秒 GPU。

    返回不透明锁句柄表示拿到锁；None 表示其他 launcher 正在 spawn，本实例
    应等待其完成再 hand off。锁文件长期保留，进程退出后由内核释放锁状态。
    """
    return acquire_spawn_lock(SPAWN_LOCK_PATH)


def _release_spawn_lock(lock) -> None:
    """释放咨询锁；锁文件保留，供后续实例安全复用。"""
    release_spawn_lock(lock)


def _daemon_lifecycle_lock_is_held() -> bool:
    """以原生锁状态验证 daemon 已接管生命周期，不信任路径或 mtime。"""
    probe = acquire_spawn_lock(DAEMON_LIFECYCLE_LOCK_PATH)
    if probe is None:
        return True
    release_spawn_lock(probe)
    return False


def _wait_daemon_handoff(process: subprocess.Popen, timeout_sec: float) -> bool:
    """有界等待子进程持有生命周期锁；子进程先退出则立即失败。"""
    deadline = time.monotonic() + timeout_sec
    while True:
        if _daemon_lifecycle_lock_is_held():
            return True
        if process.poll() is not None:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.02, remaining))


def _wait_daemon_ready(timeout_sec: float) -> bool:
    """轮询 /health 直到 200 或超时. daemon prewarm 模型 ~30-60s 是正常的。"""
    deadline = time.monotonic() + timeout_sec
    last_log = 0.0
    while time.monotonic() < deadline:
        if _check_daemon():
            return True
        now = time.monotonic()
        if now - last_log >= 10:
            remaining = int(deadline - now)
            _flog(f"daemon not ready yet, waiting ({remaining}s left)")
            last_log = now
        time.sleep(0.5)
    return False


def _ensure_daemon_ready() -> bool:
    """等待现有启动者或在其退出后接管，整个竞争阶段受固定时限约束。"""
    deadline = time.monotonic() + SPAWN_LOCK_WAIT_TIMEOUT
    announced = False
    while True:
        if _check_daemon():
            return True
        lock = _acquire_spawn_lock()
        if lock is not None:
            return _start_daemon_under_lock(lock)
        if not announced:
            _flog(
                "another launcher is spawning daemon; "
                f"waiting or taking over within {SPAWN_LOCK_WAIT_TIMEOUT}s"
            )
            announced = True
        now = time.monotonic()
        if now >= deadline:
            return False
        time.sleep(min(0.5, deadline - now))


def _start_daemon_under_lock(lock) -> bool:
    """短期门内完成 daemon 锁交接，再释放门并等待服务就绪。"""
    gate = lock

    def release_gate() -> None:
        nonlocal gate
        if gate is None:
            return
        _release_spawn_lock(gate)
        gate = None

    try:
        if _check_daemon():
            _flog("daemon was spawned by another launcher in the meantime")
            return True
        if _daemon_lifecycle_lock_is_held():
            _flog("daemon 生命周期锁已被持有，等待预热完成")
            release_gate()
            return _wait_daemon_ready(DAEMON_READY_TIMEOUT)
        _flog(f"daemon not running, spawning on port {DAEMON_PORT} (lock acquired)")
        try:
            process = _spawn_daemon()
        except Exception:  # noqa: BLE001
            _flog("daemon spawn failed; see stable daemon log")
            return False
        if not _wait_daemon_handoff(process, DAEMON_HANDOFF_TIMEOUT):
            _flog("daemon 未在交接时限内取得生命周期锁")
            return False
        release_gate()
        if not _wait_daemon_ready(DAEMON_READY_TIMEOUT):
            _flog(
                f"daemon did not become ready in {DAEMON_READY_TIMEOUT}s; "
                f"check {DAEMON_LOG} for errors"
            )
            return False
        _flog("daemon ready, handing off to mcp-proxy")
        return True
    finally:
        release_gate()


def main() -> None:
    # 1. 解析 project_id (env / .claude/project.json), 失败立即退出
    try:
        project_id = resolve_local()
    except ProjectIdError as exc:
        _flog(f"FATAL: {exc!s}")
        sys.exit(1)
    _flog(f"project_id={project_id}")
    # 透传到 daemon (spawn 时 env=os.environ.copy() 继承)
    os.environ["PLATFORM_PROJECT_ID"] = project_id

    # 2. daemon 多租户能力探测: tenant_mode 字段标示新版 daemon 支持并发多 project
    if _check_daemon():
        health = _fetch_daemon_health() or {}
        tenant_mode = health.get("tenant_mode")
        if tenant_mode != "multi":
            # 旧版 daemon (单租户或多项目改造前) — 拒绝 hand off, 让用户 kill 升级。
            _flog(
                f"FATAL: daemon (port {DAEMON_PORT}) tenant_mode={tenant_mode!r} 非 multi, 判定旧版 daemon。\n"
                f"  本次会话 project_id={project_id!r} 无法在旧 daemon 上隔离。\n"
                f"  请手动 kill 该 daemon, 下次会话起来会自动 spawn 新 daemon:\n"
                f"    Get-NetTCPConnection -LocalPort {DAEMON_PORT} | "
                f"Select-Object -ExpandProperty OwningProcess | ForEach-Object {{ Stop-Process -Id $_ -Force }}"
            )
            sys.exit(1)

    if not _check_daemon():
        # 多 launcher race: 等待当前启动者；其退出时由下一实例接管，不空等到超时。
        if not _ensure_daemon_ready():
            _flog(f"daemon startup handoff failed within the bounded wait; see {DAEMON_LOG}")
            sys.exit(1)
    else:
        _flog(f"daemon already running on port {DAEMON_PORT}, handing off to mcp-proxy")

    # multi-tenant daemon 二次核对 tenant_mode (防 race 中 spawn 出旧版 daemon)
    final_health = _fetch_daemon_health() or {}
    if final_health.get("tenant_mode") != "multi":
        _flog(
            f"FATAL: daemon /health 二次核对失败, tenant_mode={final_health.get('tenant_mode')!r}, "
            f"非 multi (race?)。请重启。"
        )
        sys.exit(1)

    if not MCP_PROXY_EXE.exists():
        _flog(
            f"mcp-proxy.exe not found at {MCP_PROXY_EXE}. "
            f"install: uv pip install --python {VENV_SCRIPTS / 'python.exe'} mcp-proxy"
        )
        sys.exit(1)

    # hand off to mcp-proxy: it becomes the stdio MCP server (Claude Code side)
    # and bidirectionally forwards to daemon SSE. We block until it exits
    # (= Claude Code closes the session). 不用 os.execv 因为 Windows 上 os.execv
    # 不真替换进程, 行为是 spawn + 杀自己, stdio 继承可能错乱。
    # multi-tenant: SSE URL 带 ?project_id=<pid>, daemon 据此把 session 绑定到对应 collection
    sse_url = f"{DAEMON_SSE_URL_BASE}?project_id={project_id}"
    _flog(f"hand off to mcp-proxy: {sse_url}")
    try:
        rc = subprocess.call(
            [str(MCP_PROXY_EXE), sse_url],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except KeyboardInterrupt:
        rc = 0
    sys.exit(rc)


if __name__ == "__main__":
    main()
