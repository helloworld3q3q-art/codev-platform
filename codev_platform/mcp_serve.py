"""平台 MCP 端点编排 (P3 + P5) —— 把三套 MCP 以**服务地址**常驻起来。

服务化目标:业务仓 `.mcp.json` 连平台的 HTTP/SSE 端点,不再走文件路径 launcher。
本模块负责平台侧把这些端点拉起 + 健康探测:

| 端点 | 起法 | 多租户 |
|---|---|---|
| platform-docs (chroma) | 现有 daemon 自带 SSE (首个会话经 launcher 自 spawn) | ?project_id= contextvar |
| cross-link | `python -m codev_platform.cross_link.server --http` (本仓) | ?project_id= contextvar |
| codegraph (per project) | `mcp-proxy --port P -- codegraph serve --mcp` (cwd=repo) | 每项目一端口 (port 即选择器) |

codegraph 是外部 stdio-only 工具 (`codegraph serve --mcp` 无 HTTP 选项),用 **mcp-proxy
server 模式** 包成 SSE,保全工具集 (callers/impact/context...),不退化成 codegraph-api REST。

纯函数 (端点枚举 / 命令构造 / 探测) 与副作用 (spawn) 分离,前者可单测。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from codev_platform.core.config import get as _cfg_get, load_config


# 默认端口 (config 可覆盖)。chroma 沿用 daemon.port;cross-link / codegraph 走 mcp.*。
DEFAULT_CHROMA_PORT = 18083
DEFAULT_CROSS_LINK_PORT = 18086
DEFAULT_CODEGRAPH_BASE_PORT = 18090  # 自动分配时的起始端口 (项目无显式端口时按序分配)


@dataclass
class MCPEndpoint:
    """一个 MCP 服务端点 (平台侧拉起 + 业务侧连接)。"""
    name: str                       # 展示名: platform-docs / cross-link / codegraph:<pid>
    kind: str                       # chroma | cross_link | codegraph
    port: int
    project_id: str | None = None   # codegraph 是 per-project;chroma/cross-link 多租户为 None
    cmd: list[str] | None = None    # spawn 命令 (None = 自 spawn / 外部托管, 如 chroma daemon)
    cwd: str | None = None
    self_spawned: bool = False      # True = 不由本编排器 spawn (chroma 由 launcher 按会话拉起)

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def sse_url(self) -> str:
        base = f"http://{self.host}:{self.port}/sse"
        # chroma / cross-link 多租户需 ?project_id=;codegraph 端口即项目, 不带
        return base

    @property
    def health_url(self) -> str | None:
        # chroma / cross-link 自带 /health;codegraph 经 mcp-proxy 无 /health → 用 TCP 探
        return None if self.kind == "codegraph" else f"http://{self.host}:{self.port}/health"


def _resolve_venv_scripts(cfg: dict) -> Path:
    """venv Scripts/bin 目录: env > config runtime.chroma_venv > 本仓 .venv。"""
    env = os.environ.get("PLATFORM_DOCS_VENV")
    cand = None
    if env:
        cand = Path(env).expanduser()
    else:
        v = _cfg_get(cfg, "runtime.chroma_venv")
        if v:
            cand = Path(v).expanduser()
    if cand is None:
        cand = Path(__file__).resolve().parents[1] / ".venv"
    return cand / ("Scripts" if sys.platform == "win32" else "bin")


def _venv_python(cfg: dict) -> Path:
    scripts = _resolve_venv_scripts(cfg)
    return scripts / ("python.exe" if sys.platform == "win32" else "python")


def _mcp_proxy_exe(cfg: dict) -> Path:
    scripts = _resolve_venv_scripts(cfg)
    return scripts / ("mcp-proxy.exe" if sys.platform == "win32" else "mcp-proxy")


def build_codegraph_proxy_cmd(mcp_proxy: str | Path, port: int) -> list[str]:
    """构造 mcp-proxy server 模式命令: 把 `codegraph serve --mcp` 包成 SSE。

    `--` 分隔符让 argparse 把后续当 stdio 命令的位置参数 (否则 --mcp 被当 proxy 自己的选项)。
    cwd 由调用方设为项目 repo (codegraph 据此找 .codegraph)。
    """
    return [str(mcp_proxy), "--port", str(port), "--host", "127.0.0.1",
            "--pass-environment", "--", "codegraph", "serve", "--mcp"]


def build_cross_link_cmd(python: str | Path, port: int) -> list[str]:
    """构造 cross-link HTTP 端点启动命令。"""
    return [str(python), "-m", "codev_platform.cross_link.server", "--http", "--port", str(port)]


def iter_endpoints(cfg: dict) -> list[MCPEndpoint]:
    """从 config 枚举应常驻的 MCP 端点 (纯函数, 不 spawn)。

    - chroma: daemon.port (self-spawned, 不由本编排器拉起)
    - cross-link: mcp.cross_link_sse_port
    - codegraph: 每个 projects.<id> 配了 codegraph_sse_port 且 repo_path 存在的项目一个端点;
      未显式配端口的项目, 从 DEFAULT_CODEGRAPH_BASE_PORT 按注册顺序自动分配。
    """
    venv_py = _venv_python(cfg)
    mcp_proxy = _mcp_proxy_exe(cfg)
    out: list[MCPEndpoint] = []

    # chroma daemon。业务仓 .mcp.json 走 type:sse 直连后, 失去 launcher 的 per-session
    # auto-spawn → `serve-mcp start` 负责把它拉起作常驻 (residency)。self_spawned=True 仅
    # 表示它也可被业务仓 Claude 会话经 launcher 拉起 (两条路径幂等: 已起则都跳过)。
    chroma_port = int(_cfg_get(cfg, "daemon.port") or DEFAULT_CHROMA_PORT)
    out.append(MCPEndpoint(
        name="platform-docs", kind="chroma", port=chroma_port,
        cmd=[str(venv_py), "-m", "codev_platform.chroma.server", "--http"],
        self_spawned=True,
    ))

    # cross-link (本仓, 本编排器拉起)
    cl_port = int(_cfg_get(cfg, "mcp.cross_link_sse_port") or DEFAULT_CROSS_LINK_PORT)
    out.append(MCPEndpoint(name="cross-link", kind="cross_link", port=cl_port,
                           cmd=build_cross_link_cmd(venv_py, cl_port)))

    # codegraph per-project。auto 分配端口时跳过所有显式端口 + chroma/cross-link 端口, 防撞
    # (审计 Concern 4: 显式 18090 与 auto base 18090 会撞)。注: auto 端口随"有几个 repo 存在"
    # 浮动 → 业务仓 .mcp.json 要连固定 URL 必须显式 pin codegraph_sse_port(config.example 已说明)。
    projects = _cfg_get(cfg, "projects") or {}
    reserved: set[int] = {chroma_port, cl_port}
    for p in projects:
        pc = projects.get(p)
        if isinstance(pc, dict) and pc.get("codegraph_sse_port"):
            reserved.add(int(pc["codegraph_sse_port"]))
    auto_port = DEFAULT_CODEGRAPH_BASE_PORT

    def _next_auto() -> int:
        nonlocal auto_port
        while auto_port in reserved:
            auto_port += 1
        p = auto_port
        reserved.add(p)
        auto_port += 1
        return p

    for pid in sorted(p for p in projects if isinstance(projects.get(p), dict)):
        pconf = projects[pid]
        repo = pconf.get("repo_path")
        explicit_port = pconf.get("codegraph_sse_port")
        if not repo:
            continue
        repo_path = Path(repo).expanduser()
        if not repo_path.exists():
            continue
        port = int(explicit_port) if explicit_port else _next_auto()
        out.append(MCPEndpoint(
            name=f"codegraph:{pid}", kind="codegraph", port=port, project_id=pid,
            cmd=build_codegraph_proxy_cmd(mcp_proxy, port), cwd=str(repo_path),
        ))
    return out


def _http_health(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, ConnectionRefusedError, TimeoutError, OSError):
        return False


def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe(ep: MCPEndpoint) -> str:
    """探一个端点: 'ok' / 'down'。chroma/cross-link 用 /health, codegraph 用 TCP。"""
    if ep.health_url:
        return "ok" if _http_health(ep.health_url) else "down"
    return "ok" if _tcp_open(ep.host, ep.port) else "down"


def probe_all(cfg: dict | None = None) -> list[dict[str, Any]]:
    """探测所有登记端点, 返回 [{name, kind, port, status, sse_url, project_id}]。P5 健康用。"""
    cfg = cfg if cfg is not None else load_config()
    rows: list[dict[str, Any]] = []
    for ep in iter_endpoints(cfg):
        rows.append({
            "name": ep.name, "kind": ep.kind, "port": ep.port,
            "project_id": ep.project_id, "status": probe(ep),
            "sse_url": ep.sse_url, "self_spawned": ep.self_spawned,
        })
    return rows


def _spawn_detached(cmd: list[str], cwd: str | None, log_path: Path) -> int:
    """detached spawn (会话关了仍活), 复用 chroma launcher 的 Windows creationflags。"""
    if sys.platform == "win32":
        creationflags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        creationflags = 0
    log_handle = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=log_handle, stderr=log_handle,
            creationflags=creationflags, close_fds=False, env=os.environ.copy(),
        )
        return proc.pid
    finally:
        log_handle.close()


def ensure_serving(cfg: dict | None = None) -> list[dict[str, Any]]:
    """幂等拉起所有可由本编排器 spawn 的端点 (已 reachable 则跳过)。

    chroma (self_spawned) 不在此拉起 —— 它由业务仓首个 Claude 会话经 launcher 自 spawn;
    本函数只报告其状态。返回每端点 {name, action, status, pid?}。
    """
    cfg = cfg if cfg is not None else load_config()
    log_dir = Path(__file__).resolve().parent / "mcp_serve_logs"
    log_dir.mkdir(exist_ok=True)
    results: list[dict[str, Any]] = []
    for ep in iter_endpoints(cfg):
        status = probe(ep)
        if status == "ok":
            results.append({"name": ep.name, "action": "already-up", "status": "ok"})
            continue
        if ep.cmd is None:
            results.append({"name": ep.name, "action": "skip", "status": status,
                            "note": "无 spawn 命令(外部托管)"})
            continue
        # 缺 mcp-proxy / venv python 时给清晰错误, 不静默
        exe = Path(ep.cmd[0])
        if not exe.exists():
            results.append({"name": ep.name, "action": "fail", "status": "down",
                            "error": f"可执行不存在: {exe}"})
            continue
        pid = _spawn_detached(ep.cmd, ep.cwd, log_dir / f"{ep.name.replace(':', '_')}.log")
        note = "chroma daemon 预热模型 ~30-60s" if ep.kind == "chroma" else ""
        results.append({"name": ep.name, "action": "spawned", "status": "starting",
                        "pid": pid, "note": note})
    return results
