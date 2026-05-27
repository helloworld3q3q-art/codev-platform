"""platform-docs MCP daemon launcher (uses mcp-proxy for stdio<->SSE bridge).

被 platform-docs-mcp.cmd 当作每个 Claude Code 会话的 stdio MCP server 启动。
内部职责:

1. 检测 127.0.0.1:<port>/health 上的 daemon 是否活
2. 不活 -> spawn 一个 detached daemon (mcp_server.py --http) 加载 Qwen 模型
3. 等 daemon ready (轮询 /health 最多 90s)
4. exec mcp-proxy: mcp-proxy 当 stdio MCP server, 透明转发 stdio<->SSE 到 daemon

为什么需要这个 launcher:
- 8GB GPU 上每个 mcp_server 进程独立 load Qwen 模型 = 3GB. 多个 Claude Code
  会话同时开就 CUDA OOM. daemon 模式让所有会话共享 1 份 GPU 模型.
- 对 Claude Code 透明 (.mcp.json 仍是 stdio 类型), 不依赖 client 端的 HTTP
  支持成熟度.

stdio <-> SSE 桥接复用开源 mcp-proxy (https://github.com/sparfenyuk/mcp-proxy),
不再自己实现 forward 逻辑 (~80 行自维护代码减少为 0).

daemon 生命周期:
- 第一个会话起来 -> spawn daemon (detached, 会话关了 daemon 仍活)
- 后续会话起来 -> 检测 daemon 已活, 直接 hand off to mcp-proxy
- 系统重启 / 手动 kill daemon process -> 下一个会话起来自动重 spawn
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

from codev_platform.core.project_id import ProjectIdError, resolve_local


DAEMON_PORT = int(os.getenv("PLATFORM_DOCS_DAEMON_PORT", "18083"))
DAEMON_URL = f"http://127.0.0.1:{DAEMON_PORT}"
DAEMON_HEALTH_URL = f"{DAEMON_URL}/health"
# DAEMON_SSE_URL 在 main() 里按 resolved project_id 拼 ?project_id= (multi-tenant)
DAEMON_SSE_URL_BASE = f"{DAEMON_URL}/sse"
DAEMON_READY_TIMEOUT = float(os.getenv("PLATFORM_DOCS_DAEMON_READY_TIMEOUT", "90"))
SCRIPT_DIR = Path(__file__).parent.resolve()
# daemon 进程入口模块: codev_platform.chroma.server (通过 -m 启动)
SERVER_MODULE = "codev_platform.chroma.server"
DAEMON_LOG = SCRIPT_DIR / "daemon.log"


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
SPAWN_LOCK_PATH = SCRIPT_DIR / ".daemon.spawn.lock"
SPAWN_LOCK_STALE_SEC = 120  # 超过 120s 视为 stale (覆盖 90s prewarm timeout)
SPAWN_LOCK_WAIT_TIMEOUT = 120  # 等其他 launcher spawn 最多 120s


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
    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def _spawn_daemon() -> None:
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

    log_handle = open(DAEMON_LOG, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            [exe, "-m", SERVER_MODULE, "--http"],
            cwd=str(SCRIPT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creationflags,
            close_fds=False,
            env=os.environ.copy(),
        )
        _flog(f"spawned daemon pid={proc.pid} log={DAEMON_LOG}")
    finally:
        log_handle.close()


def _acquire_spawn_lock():
    """原子文件锁: 只允许一个 launcher 同时 spawn daemon。

    多 Claude Code session 同时 cold start 时, 没有这个锁两个 launcher 都会
    spawn daemon, 第二个 port bind 失败浪费几十秒 GPU。

    返回 (fd, path) 表示拿到锁; None 表示放弃 (其他 launcher 已经在 spawn,
    本 launcher 应等其完成再 hand off)。stale lock (> SPAWN_LOCK_STALE_SEC)
    会被抢占。
    """
    try:
        # O_EXCL: 原子创建, 已存在就抛 FileExistsError
        fd = os.open(str(SPAWN_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
        return (fd, SPAWN_LOCK_PATH)
    except FileExistsError:
        # 检查 stale: 看 lock 文件 mtime
        try:
            age = time.time() - SPAWN_LOCK_PATH.stat().st_mtime
            if age > SPAWN_LOCK_STALE_SEC:
                _flog(f"removing stale spawn lock (age={int(age)}s > {SPAWN_LOCK_STALE_SEC}s)")
                SPAWN_LOCK_PATH.unlink(missing_ok=True)
                # 重试一次
                try:
                    fd = os.open(str(SPAWN_LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
                    return (fd, SPAWN_LOCK_PATH)
                except FileExistsError:
                    pass
        except OSError:
            pass
        return None


def _release_spawn_lock(lock) -> None:
    """释放锁: 关 fd + 删文件。任一步失败不抛, 让 launcher 继续。"""
    if lock is None:
        return
    fd, path = lock
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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
        # 多 launcher race: 用 spawn 锁串行化, 避免两个都 spawn 浪费 GPU
        lock = _acquire_spawn_lock()
        if lock is None:
            # 没拿到锁 = 另一个 launcher 正在 spawn, 我们等它好
            _flog(f"another launcher is spawning daemon, waiting up to {SPAWN_LOCK_WAIT_TIMEOUT}s")
            if not _wait_daemon_ready(SPAWN_LOCK_WAIT_TIMEOUT):
                _flog(f"other launcher did not finish in {SPAWN_LOCK_WAIT_TIMEOUT}s; see {DAEMON_LOG}")
                sys.exit(1)
            _flog("daemon ready (spawned by another launcher), handing off to mcp-proxy")
        else:
            try:
                # 拿锁后 double-check: 另一个 launcher 可能刚 spawn 完释放锁
                if _check_daemon():
                    _flog("daemon was spawned by another launcher in the meantime")
                else:
                    _flog(f"daemon not running, spawning on port {DAEMON_PORT} (lock acquired)")
                    try:
                        _spawn_daemon()
                    except Exception as exc:  # noqa: BLE001
                        _flog(f"spawn raised: {exc!s}")
                    if not _wait_daemon_ready(DAEMON_READY_TIMEOUT):
                        _flog(
                            f"daemon did not become ready in {DAEMON_READY_TIMEOUT}s; "
                            f"check {DAEMON_LOG} for errors. aborting."
                        )
                        sys.exit(1)
                    _flog("daemon ready, handing off to mcp-proxy")
            finally:
                _release_spawn_lock(lock)
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
