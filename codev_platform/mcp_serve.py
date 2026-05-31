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
DEFAULT_CODEGRAPH_PORT = 18091  # codegraph 多租户代理端点 (单端点, mcp.codegraph_sse_port 覆盖)


@dataclass
class MCPEndpoint:
    """一个 MCP 服务端点 (平台侧拉起 + 业务侧连接)。"""
    name: str                       # 展示名: platform-docs / cross-link / codegraph
    kind: str                       # chroma | cross_link | codegraph
    port: int
    project_id: str | None = None   # 三套均多租户单端点, 统一为 None (业务仓走 ?project_id=)
    cmd: list[str] | None = None    # spawn 命令 (None = 自 spawn / 外部托管, 如 chroma daemon)
    cwd: str | None = None
    self_spawned: bool = False      # True = 不由本编排器 spawn (chroma 由 launcher 按会话拉起)

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def sse_url(self) -> str:
        # 三套均多租户: 业务仓 .mcp.json 在此基址后接 ?project_id=<id>
        return f"http://{self.host}:{self.port}/sse"

    @property
    def health_url(self) -> str | None:
        # 三套均自带 PUBLIC /healthz 最小存活探针 (审计 #4: 详情面 /platform/status 改鉴权)。
        # 探活只需 200, 不带 token 也能打 (codegraph 改平台自写多租户代理后也有, 见 codegraph.server)。
        return f"http://{self.host}:{self.port}/healthz"


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


def build_codegraph_cmd(python: str | Path, port: int) -> list[str]:
    """构造 codegraph 多租户代理 HTTP 端点启动命令 (方案 B, 平台自写, 不再用 mcp-proxy)。

    代理内部按 ?project_id= 懒启动 per-repo `codegraph serve --mcp` stdio 后端并转发,
    与 cross-link / chroma 同构 (单端点多租户)。repo_path 由代理从 config.projects 解析。
    """
    return [str(python), "-m", "codev_platform.codegraph.server", "--http", "--port", str(port)]


def build_cross_link_cmd(python: str | Path, port: int) -> list[str]:
    """构造 cross-link HTTP 端点启动命令。"""
    return [str(python), "-m", "codev_platform.cross_link.server", "--http", "--port", str(port)]


# ----------------------------------------------------------------------
# 客户端可切换源 (双实例): 业务仓 .mcp.json 的 URL 指向哪个实例。
#   local    = 本机本地实例 (索引 working tree, 含未提交; 默认 18xxx)
#   platform = 平台基线服务器 (索引 HEAD, 跨项目/团队共享; 默认 19xxx, 远程则改 host)
# config.mcp_sources.<target> 覆盖 host + 各 tool 端口 —— 换远程平台只改 host, 不改代码。
# 详见 docs/plans/roadmap-2026-05-29/dual-instance-codeindex-2026-05-30.md §四。
# ----------------------------------------------------------------------
MCP_SOURCE_TOOLS = ("platform-docs", "cross-link", "codegraph")
DEFAULT_MCP_SOURCES: dict[str, dict[str, Any]] = {
    "local": {
        "host": "127.0.0.1", "platform-docs": DEFAULT_CHROMA_PORT,
        "cross-link": DEFAULT_CROSS_LINK_PORT, "codegraph": DEFAULT_CODEGRAPH_PORT,
    },
    "platform": {
        "host": "127.0.0.1", "platform-docs": 19083, "cross-link": 19086, "codegraph": 19091,
    },
}


def mcp_source_endpoint(cfg: dict, target: str, tool: str) -> tuple[str, int]:
    """(host, port) for 源 target + tool; config.mcp_sources.<target> 覆盖默认。"""
    src = dict(DEFAULT_MCP_SOURCES.get(target) or {})
    override = _cfg_get(cfg, f"mcp_sources.{target}") or {}
    if isinstance(override, dict):
        src.update(override)
    port = src.get(tool)
    if port is None:
        raise ValueError(f"源 '{target}' 未定义 tool '{tool}' 的端口 (config.mcp_sources.{target}.{tool})")
    return str(src.get("host", "127.0.0.1")), int(port)


def mcp_source_url(cfg: dict, target: str, tool: str, project_id: str) -> str:
    """业务仓 .mcp.json 用的 SSE URL (三套均多租户 ?project_id=)。"""
    host, port = mcp_source_endpoint(cfg, target, tool)
    return f"http://{host}:{port}/sse?project_id={project_id}"


def iter_endpoints(cfg: dict) -> list[MCPEndpoint]:
    """从 config 枚举应常驻的 MCP 端点 (纯函数, 不 spawn)。

    - chroma: daemon.port (self-spawned, 不由本编排器拉起)
    - cross-link: mcp.cross_link_sse_port
    - codegraph: mcp.codegraph_sse_port (单端点多租户代理, 内部按 project_id 路由 per-repo 后端)
    """
    venv_py = _venv_python(cfg)
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

    # codegraph 多租户单端点 (方案 B): 平台自写代理, 按 ?project_id= 懒启动 per-repo 后端 +
    # 转发。端口固定 mcp.codegraph_sse_port (不再 per-project 浮动); repo_path 由代理自行
    # 从 config.projects 解析。一个进程 / 一个 systemd unit (codev-mcp-codegraph) 服务所有项目。
    cg_port = int(_cfg_get(cfg, "mcp.codegraph_sse_port") or DEFAULT_CODEGRAPH_PORT)
    out.append(MCPEndpoint(name="codegraph", kind="codegraph", port=cg_port,
                           cmd=build_codegraph_cmd(venv_py, cg_port)))
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
    """探一个端点: 'ok' / 'down'。三套均自带 PUBLIC /healthz(走 _http_health);无 health_url 才退回 TCP。"""
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


def _spawn_detached(cmd: list[str], cwd: str | None, log_path: Path,
                    env: dict[str, str] | None = None) -> int:
    """detached spawn (会话关了仍活), 复用 chroma launcher 的 Windows creationflags。

    env=None → 继承 os.environ.copy(); 传入则用调用方覆盖后的环境 (如 chroma 注入
    PLATFORM_DOCS_DAEMON_PORT, 与 systemd unit 对齐)。
    """
    if sys.platform == "win32":
        creationflags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        creationflags = 0
    spawn_env = env if env is not None else os.environ.copy()
    log_handle = open(log_path, "ab", buffering=0)
    try:
        # Linux: start_new_session=True (setsid) 让子进程脱离当前会话, 避免拉起它的
        # shell / wsl 调用退出时 SIGHUP 连带杀掉 daemon (Windows 用 creationflags 已脱离)。
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=log_handle, stderr=log_handle,
            creationflags=creationflags, close_fds=False, env=spawn_env,
            start_new_session=(sys.platform != "win32"),
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
        # chroma: server.py 只认 env PLATFORM_DOCS_DAEMON_PORT (不读 config.daemon.port),
        # 须显式注入 ep.port, 否则配了 daemon.port=19083 时 spawn 的 chroma 仍绑默认 18083 →
        # 探测 ep.port 报 down。prewarm 让 daemon 起来即加载模型 (与 systemd unit 对齐)。
        spawn_env = None
        if ep.kind == "chroma":
            spawn_env = {**os.environ, "PLATFORM_DOCS_DAEMON_PORT": str(ep.port),
                         "PLATFORM_DOCS_PREWARM": "true"}
        pid = _spawn_detached(ep.cmd, ep.cwd, log_dir / f"{ep.name.replace(':', '_')}.log",
                              env=spawn_env)
        note = "chroma daemon 预热模型 ~30-60s" if ep.kind == "chroma" else ""
        results.append({"name": ep.name, "action": "spawned", "status": "starting",
                        "pid": pid, "note": note})
    return results


# ----------------------------------------------------------------------
# systemd 常驻 (Linux): 按 config 生成 unit, 开机自起 + 挂了自动重启。
# 复用 iter_endpoints 的 cmd/cwd, 路径/端口全来自 config —— 任意 Linux 部署可复现。
# chroma 也纳入 (GPU daemon): server.py 端口走 PLATFORM_DOCS_DAEMON_PORT env, prewarm
# 让 systemd 起来即加载模型 —— 两者由 render_systemd_units 按 ep.kind=='chroma' 注入。
# ----------------------------------------------------------------------
SYSTEMD_KINDS = ("chroma", "cross_link", "codegraph")


def systemd_unit_name(ep: MCPEndpoint) -> str:
    return "codev-mcp-" + ep.name.replace(":", "-")


def render_systemd_units(cfg: dict, user: str, *, kinds=SYSTEMD_KINDS) -> dict[str, str]:
    """生成 {unit文件名: 内容}。ExecStart/WorkingDirectory 直接取自 iter_endpoints。"""
    import shlex
    venv_bin = str(_resolve_venv_scripts(cfg))
    home = str(Path.home())
    units: dict[str, str] = {}
    for ep in iter_endpoints(cfg):
        if ep.kind not in kinds or not ep.cmd:
            continue
        execstart = " ".join(shlex.quote(c) for c in ep.cmd)
        env_lines = f"Environment=PATH=/usr/local/bin:/usr/bin:/bin:{venv_bin}\n"
        workdir = ep.cwd or home
        # chroma daemon 的监听端口走 PLATFORM_DOCS_DAEMON_PORT env (server.py 不读
        # config.daemon.port, 见 server.py:main); prewarm 让 unit 起来即加载 GPU 模型,
        # 避免业务仓首个 search_docs 冷启动 60s 超时。端口仍来自 config (ep.port)。
        # 启动时 server.py resolve_local() 需解析 project_id; systemd 默认 cwd(home)无
        # .claude/project.json → 把 WorkingDirectory 指到平台仓根(本包上一级, 该处 project.json
        # = codev-platform), daemon 以此为 home 项目, 业务仓仍按 ?project_id= 多租户路由。
        if ep.kind == "chroma":
            env_lines += f"Environment=PLATFORM_DOCS_DAEMON_PORT={ep.port}\n"
            env_lines += "Environment=PLATFORM_DOCS_PREWARM=true\n"
            workdir = ep.cwd or str(Path(__file__).resolve().parent.parent)
        units[f"{systemd_unit_name(ep)}.service"] = (
            "[Unit]\n"
            f"Description=codev MCP endpoint {ep.name} (port {ep.port})\n"
            "After=network.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"User={user}\n"
            f"WorkingDirectory={workdir}\n"
            f"{env_lines}"
            f"ExecStart={execstart}\n"
            "Restart=always\n"
            "RestartSec=3\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
    return units


def render_reindex_unit(cfg: dict, user: str) -> tuple[str, str]:
    """reindex worker (codev-reindex) 的 systemd unit —— 非 MCP 端点 (无端口/health), 单独生成。

    写队列的常驻串行消费者 (写侧串行化, 读并发不受影响); 开机自起 + 崩溃重启。
    """
    import shlex
    venv_py = _venv_python(cfg)
    venv_bin = str(_resolve_venv_scripts(cfg))
    home = str(Path.home())
    execstart = f"{shlex.quote(str(venv_py))} -m codev_platform.cli reindex-queue worker"
    content = (
        "[Unit]\n"
        "Description=codev reindex worker (写队列串行消费者)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={home}\n"
        f"Environment=PATH=/usr/local/bin:/usr/bin:/bin:{venv_bin}\n"
        f"ExecStart={execstart}\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    return "codev-reindex.service", content


def render_webhook_unit(cfg: dict, user: str) -> tuple[str, str]:
    """webhook 接收器 (codev-webhook) 的 systemd unit —— 非 MCP 端点, 单独生成。

    收 VCS push → enqueue reindex (push 即触发); 开机自起 + 崩溃重启。绑 127.0.0.1
    (本机 Gitea 可达); 远程 VCS 需自行反代 + 配 webhook.secret 验签。
    """
    import shlex
    venv_py = _venv_python(cfg)
    venv_bin = str(_resolve_venv_scripts(cfg))
    home = str(Path.home())
    execstart = f"{shlex.quote(str(venv_py))} -m codev_platform.cli webhook serve"
    content = (
        "[Unit]\n"
        "Description=codev webhook receiver (VCS push -> enqueue reindex)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={home}\n"
        f"Environment=PATH=/usr/local/bin:/usr/bin:/bin:{venv_bin}\n"
        f"ExecStart={execstart}\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    return "codev-webhook.service", content


def install_systemd(cfg: dict, user: str) -> dict[str, Any]:
    """以普通用户生成 unit 到 ~/codev-systemd/(config 读对), 返回唯一一条 sudo 安装命令。

    不在此直接 sudo: sudo 会切到 root 的 HOME → 读错 config。生成归生成(用户态),
    装到 /etc/systemd/system + enable 归 root(打印命令让用户跑)。
    """
    import shlex
    units = render_systemd_units(cfg, user)
    # 非 MCP 端点的常驻服务, 单独并入: reindex worker (串行消费写队列) + webhook 接收器 (push 触发)
    for _rname, _rcontent in (render_reindex_unit(cfg, user), render_webhook_unit(cfg, user)):
        units[_rname] = _rcontent
    out_dir = Path.home() / "codev-systemd"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fname, content in units.items():
        p = out_dir / fname
        p.write_text(content, encoding="utf-8")
        paths.append(p)
    svc_names = [p.name for p in paths]
    stems = [p.stem for p in paths]
    # 同时写一个 install.sh: 单条 `sudo bash <dir>/install.sh` 即可装, 避免长命令多行
    # 粘贴在终端被换行拆断 (cp 多文件 + && 链很容易折行)。用 restart 而非 enable --now,
    # 这样重复执行 / unit 内容变更时能真正重启生效; enable(不带 --now)只设开机自起。
    if paths:
        lines = ["#!/usr/bin/env bash", "set -e"]
        lines += [f"cp {shlex.quote(str(p))} /etc/systemd/system/" for p in paths]
        lines.append("systemctl daemon-reload")
        lines.append("systemctl enable " + " ".join(stems))
        # reset-failed 清掉可能的 start-limit (崩溃重启过多会进 failed 态, 否则 restart 被拒)
        lines.append("systemctl reset-failed " + " ".join(stems) + " 2>/dev/null || true")
        lines.append("systemctl restart " + " ".join(stems))
        lines.append('echo "[install.sh] done"')
        install_sh = out_dir / "install.sh"
        install_sh.write_text("\n".join(lines) + "\n", encoding="utf-8")
        install_sh.chmod(0o755)
        sudo_cmd = f"sudo bash {install_sh}"
    else:
        sudo_cmd = "(无可安装的端点: 检查 config.projects 是否配了 repo_path)"
    return {"dir": str(out_dir), "units": svc_names, "sudo_cmd": sudo_cmd}
