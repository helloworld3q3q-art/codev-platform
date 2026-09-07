"""平台 MCP 端点编排 (P3 + P5) —— 把三套 MCP 以**服务地址**常驻起来。

服务化目标:业务仓 `.mcp.json` 连平台的 HTTP/SSE 端点,不再走文件路径 launcher。
本模块负责平台侧把这些端点拉起 + 健康探测:

| 端点 | 起法 | 多租户 |
|---|---|---|
| platform-docs (chroma) | 现有 daemon 自带 SSE (首个会话经 launcher 自 spawn) | ?project_id= contextvar |
| codegraph (多租户) | 平台 HTTP 代理按项目转发 `codegraph serve --mcp` | 单端口按 `project_id` 路由 |
| graph (统一图谱) | `python -I -m codev_platform.graph.mcp_server --http` (替代退役的 cross-link) | ?project_id= contextvar |

codegraph 后端仍是外部 stdio-only 工具 (`codegraph serve --mcp` 无 HTTP 选项)，由平台
多租户代理包成 SSE，保全工具集 (callers/impact/context...)，不退化成 codegraph-api REST。

纯函数 (端点枚举 / 命令构造 / 探测) 与副作用 (spawn) 分离,前者可单测。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from codev_platform import mcp_endpoint_catalog as _endpoint_catalog
from codev_platform.core.config import get as _cfg_get, load_config
from codev_platform.mcp_endpoint_catalog import (
    DEFAULT_AGENT_MEMORY_PORT,
    DEFAULT_CHROMA_PORT,
    DEFAULT_CODEGRAPH_PORT,
    DEFAULT_GRAPH_PORT,
    DEFAULT_MCP_SOURCES,
    MCPEndpoint,
    MCP_SOURCE_TOOLS,
    build_agent_memory_cmd,
    build_codegraph_cmd,
    build_graph_cmd,
    mcp_bind_host,
)
from codev_platform.mcp_runtime import default_log_dir, spawn_endpoint
from codev_platform.mcp_systemd_unit_registry import mcp_systemd_unit_for_kind


_warned_deprecated = _endpoint_catalog._warned_deprecated


def _sync_deprecation_warning_state() -> None:
    """把旧门面可重绑定的告警集合注入唯一实现模块。"""
    _endpoint_catalog._warned_deprecated = _warned_deprecated


def _bind_port(cfg: dict, kind: str) -> int:
    """兼容旧门面的一次性告警状态，同时委托目录模块解析端口。"""
    _sync_deprecation_warning_state()
    return _endpoint_catalog._bind_port(cfg, kind)


def mcp_source_endpoint(cfg: dict, target: str, tool: str) -> tuple[str, int]:
    """通过旧门面解析客户端源，并保持一次性告警状态兼容。"""
    _sync_deprecation_warning_state()
    return _endpoint_catalog.mcp_source_endpoint(cfg, target, tool)


def check_port_consistency(cfg: dict) -> list[str]:
    """通过旧门面检查端口一致性，并保持一次性告警状态兼容。"""
    _sync_deprecation_warning_state()
    return _endpoint_catalog.check_port_consistency(cfg)


def mcp_source_url(cfg: dict, target: str, tool: str, project_id: str) -> str:
    """通过旧门面生成客户端 URL，并保持一次性告警状态兼容。"""
    _sync_deprecation_warning_state()
    return _endpoint_catalog.mcp_source_url(cfg, target, tool, project_id)


def build_mcp_servers(cfg: dict, target: str, project_id: str) -> dict:
    """通过旧门面生成客户端配置，并保持一次性告警状态兼容。"""
    _sync_deprecation_warning_state()
    return _endpoint_catalog.build_mcp_servers(cfg, target, project_id)


def _resolve_chroma_venv_scripts(cfg: dict) -> Path:
    """解析 Chroma 专用 venv：环境变量 > 配置 > 本仓兜底。"""
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


def _chroma_venv_python(cfg: dict) -> Path:
    scripts = _resolve_chroma_venv_scripts(cfg)
    return scripts / ("python.exe" if sys.platform == "win32" else "python")


def _platform_runtime_python() -> Path:
    """平台 Python 服务绑定当前调用解释器，不继承 Chroma 专用配置。"""
    candidate = Path(sys.executable)
    if not candidate.is_absolute():
        raise RuntimeError("平台服务当前解释器路径无效")
    return candidate


def iter_endpoints(cfg: dict) -> list[MCPEndpoint]:
    """从 config 枚举应常驻的 MCP 端点 (纯函数, 不 spawn)。

    - chroma: daemon.port (self-spawned, 不由本编排器拉起)
    - codegraph: mcp.codegraph_sse_port (单端点多租户代理, 内部按 project_id 路由 per-repo 后端)
    - graph: mcp.graph_sse_port (统一图谱 impact + A1 业务域, 替代 cross-link)
    """
    chroma_python = _chroma_venv_python(cfg)
    platform_python = _platform_runtime_python()
    out: list[MCPEndpoint] = []

    # chroma daemon。业务仓 .mcp.json 走 type:sse 直连后, 失去 launcher 的 per-session
    # auto-spawn → `serve-mcp start` 负责把它拉起作常驻 (residency)。self_spawned=True 仅
    # 表示它也可被业务仓 Claude 会话经 launcher 拉起 (两条路径幂等: 已起则都跳过)。
    chroma_port = _bind_port(cfg, "chroma")
    out.append(
        MCPEndpoint(
            name="platform-docs",
            kind="chroma",
            port=chroma_port,
            cmd=[
                str(chroma_python),
                "-I",
                "-m",
                "codev_platform.chroma.daemon_entry",
                "--http",
            ],
            self_spawned=True,
        )
    )

    # codegraph 多租户单端点 (方案 B): 平台自写代理, 按 ?project_id= 懒启动 per-repo 后端 +
    # 转发。端口固定 mcp.codegraph_sse_port (不再 per-project 浮动); repo_path 由代理自行
    # 从 config.projects 解析。一个进程 / 一个 systemd unit (codev-mcp-codegraph) 服务所有项目。
    cg_port = _bind_port(cfg, "codegraph")
    out.append(
        MCPEndpoint(
            name="codegraph",
            kind="codegraph",
            port=cg_port,
            cmd=build_codegraph_cmd(platform_python, cg_port),
        )
    )

    # agent-memory (本仓, 本编排器拉起)。同一 PG 靠 org_id 列隔离, 多租户单端点 (?project_id=
    # 仅用于 project-scope 记忆 + 项目 ACL 闸); org/user 走认证身份 (见 agent/memory_mcp.py)。
    mem_port = _bind_port(cfg, "agent_memory")
    out.append(
        MCPEndpoint(
            name="agent-memory",
            kind="agent_memory",
            port=mem_port,
            cmd=build_agent_memory_cmd(platform_python, mem_port),
        )
    )

    # graph (本仓, 本编排器拉起)。统一图谱 MCP: impact + A1 业务域查询, 多租户单端点
    # (?project_id= 路由 data/graph_store/<pid>.sqlite)。让开发端 agent 查整个统一图谱 + 业务域。
    graph_port = _bind_port(cfg, "graph")
    out.append(
        MCPEndpoint(
            name="graph",
            kind="graph",
            port=graph_port,
            cmd=build_graph_cmd(platform_python, graph_port),
        )
    )
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
    """探一个端点: 'ok' / 'down'。

    三套均自带 PUBLIC /healthz(走 _http_health)。但旧 daemon(P3 引入 /healthz 之前)只有
    /health, 探 /healthz 会 404 → 误判 DOWN。故 /healthz 失败时回退探 /health(同样只看
    HTTP status 2xx, 不解析 body, 不把详情当 public 合规结果)。两者皆失败再退回 TCP 存活。
    """
    if ep.health_url:
        if _http_health(ep.health_url):
            return "ok"
        # 回退: 旧 daemon 仅有 /health alias。只看 2xx, 不读 body。
        legacy_url = f"http://{ep.host}:{ep.port}/health"
        if legacy_url != ep.health_url and _http_health(legacy_url):
            return "ok"
    return "ok" if _tcp_open(ep.host, ep.port) else "down"


def probe_all(cfg: dict | None = None, *, diagnose: bool = False) -> list[dict[str, Any]]:
    """探测所有登记端点, 返回 [{name, kind, port, status, sse_url, project_id}]。P5 健康用。

    diagnose=True 时对每个 DOWN 端点补一列 reason (调 collect_facts + diagnose_down),
    供 status 命令多打"原因"列。OK 端点 reason=""(不浪费 IO 探依赖/db)。
    """
    cfg = cfg if cfg is not None else load_config()
    rows: list[dict[str, Any]] = []
    for ep in iter_endpoints(cfg):
        status = probe(ep)
        row = {
            "name": ep.name,
            "kind": ep.kind,
            "port": ep.port,
            "project_id": ep.project_id,
            "status": status,
            "sse_url": ep.sse_url,
            "self_spawned": ep.self_spawned,
        }
        if diagnose and status != "ok":
            facts = collect_facts(ep, cfg)
            row["reason"] = diagnose_down(ep, **facts)
        rows.append(row)
    return rows


# ----------------------------------------------------------------------
# DOWN 原因诊断 (P1, fullchain-audit-2026-06-01): status 不再只报 OK/DOWN,
# 而是对 DOWN 端点给**具体原因**。纯函数 diagnose_down 按已探测事实线性 early-return,
# 事实采集 collect_facts 是 IO 薄层 (探 port / healthz / systemctl / 依赖 / db)。
# ----------------------------------------------------------------------

# 每 kind 缺依赖时的人类可读提示 (diagnose_down dep_ok=False 用)。
_DEP_HINT = {
    "chroma": "chromadb + 模型 (Qwen embedding/reranker)",
    "codegraph": "codegraph 命令",
    "agent_memory": "psycopg (PG 驱动) + extra [agent]",
    "graph": "无重依赖 (纯 sqlite3)",
}
# 每 kind 缺数据时的人类可读提示 (diagnose_down db_present=False 用)。
_DB_HINT = {
    "chroma": "chroma collection",
    "codegraph": "codegraph 索引",
    "agent_memory": "memory.pg_dsn 未配 (PG 库未就绪?)",
    "graph": "graph_store/<pid>.sqlite (未 ingest?)",
}


def diagnose_down(
    ep: MCPEndpoint,
    *,
    port_open: bool,
    healthz_ok: bool,
    unit_active: bool | None = None,
    dep_ok: bool | None = None,
    db_present: bool | None = None,
) -> str:
    """按已探测到的事实给 DOWN 的**具体原因** (纯函数, 零 IO, 线性 early-return)。

    入参皆为"已经探到的事实", 本函数不做任何探测。返回空串 = 其实是 OK。
    优先解释最根因: 端口未监听 (进程没起) > 依赖缺 > 数据缺 > healthz 异常。
    """
    if not port_open:
        if unit_active is False:
            return "进程未启动 (systemd unit 未 active)"
        if unit_active is True:
            return "unit active 但端口未监听 (启动中 / 崩溃重启中)"
        return "端口未监听 (进程未起 / 未 serve-mcp start)"
    # port 已开: 进程在跑, 但可能依赖/数据/预热问题
    if dep_ok is False:
        return f"依赖缺失 ({_DEP_HINT.get(ep.kind, ep.kind)})"
    if db_present is False:
        return f"数据缺失 ({_DB_HINT.get(ep.kind, ep.kind)})"
    if not healthz_ok:
        return "端口在但 /healthz 异常 (可能预热中)"
    return ""  # 都正常, 其实是 OK


def _systemctl_is_active(unit_name: str) -> bool | None:
    """systemctl is-active <unit> -> True/False; 非 systemd 环境 (无 systemctl) 返回 None。"""
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", unit_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() == "active"


def _dep_ok(ep: MCPEndpoint) -> bool:
    """该 kind 的关键依赖是否可用 (best-effort, 失败即视为缺)。"""
    if ep.kind == "chroma":
        import importlib.util

        return importlib.util.find_spec("chromadb") is not None
    if ep.kind == "codegraph":
        import shutil

        return shutil.which("codegraph") is not None
    if ep.kind == "agent_memory":
        import importlib.util

        return importlib.util.find_spec("psycopg") is not None
    return True


def _db_present(ep: MCPEndpoint, cfg: dict) -> bool:
    """该 kind 的数据是否就绪 (graph_store / chroma collection / codegraph 索引)。

    多租户单端点 → 按 config.projects 任一项目有数据即视为 present (端点本身可服务)。
    """
    try:
        from codev_platform.core.paths import (
            codegraph_db_path,
            chroma_dir,
        )
    except Exception:
        return True  # 解析失败不误报数据缺
    projects = _cfg_get(cfg, "projects") or {}
    pids = list(projects.keys()) if isinstance(projects, dict) else []
    if ep.kind == "codegraph":
        return any(codegraph_db_path(p).exists() for p in pids)
    if ep.kind == "chroma":
        try:
            return chroma_dir().exists()
        except Exception:
            return True
    if ep.kind == "agent_memory":
        # 同一 PG, 不按 project 分库; memory.pg_dsn 配了即视为数据面就绪 (连通性由 healthz 兜)。
        return bool(_cfg_get(cfg, "memory.pg_dsn") or os.environ.get("CODEV_PLATFORM_MEMORY_DSN"))
    if ep.kind == "graph":
        from codev_platform.graph.store import graph_store_path

        return any(graph_store_path(p).exists() for p in pids)
    return True


def collect_facts(ep: MCPEndpoint, cfg: dict | None = None) -> dict[str, Any]:
    """IO 薄层: 采集 diagnose_down 所需事实 (探 port / healthz / systemctl / 依赖 / db)。

    纯探测, 不下结论 (结论交给 diagnose_down 纯函数)。返回的 dict 直接 **facts 传 diagnose_down。
    """
    cfg = cfg if cfg is not None else load_config()
    port_open = _tcp_open(ep.host, ep.port)
    healthz_ok = _http_health(ep.health_url) if ep.health_url else False
    unit_active = _systemctl_is_active(systemd_unit_name(ep) + ".service")
    return {
        "port_open": port_open,
        "healthz_ok": healthz_ok,
        "unit_active": unit_active,
        "dep_ok": _dep_ok(ep),
        "db_present": _db_present(ep, cfg),
    }


# ----------------------------------------------------------------------
# start --wait: 拉起后有界轮询直到全 OK 或超时。
# 轮询判定抽成纯函数 should_keep_waiting 便于单测。
# ----------------------------------------------------------------------
def should_keep_waiting(elapsed: float, timeout: float, all_ok: bool) -> bool:
    """是否继续轮询: 未全 OK 且未超时才继续 (纯函数)。"""
    if all_ok:
        return False
    return elapsed < timeout


def wait_until_serving(
    cfg: dict | None = None,
    *,
    timeout: float = 60.0,
    interval: float = 2.0,
    require_current_round: bool = False,
) -> list[dict[str, Any]]:
    """有界轮询所有端点直到全 OK 或超时 (总时长 <= timeout)。

    返回每端点 {name, kind, port, status, reason?}: status=ok / timeout。
    超时的端点带 reason (调 collect_facts + diagnose_down)。
    require_current_round 为真时，必须在同一轮探测中确认全部端点为 OK。
    """
    import time

    cfg = cfg if cfg is not None else load_config()
    endpoints = iter_endpoints(cfg)
    start = time.monotonic()
    statuses: dict[str, str] = {ep.name: "down" for ep in endpoints}
    while True:
        all_ok = True
        for ep in endpoints:
            if not require_current_round and statuses[ep.name] == "ok":
                continue
            status = probe(ep)
            statuses[ep.name] = status
            if status != "ok":
                all_ok = False
        elapsed = time.monotonic() - start
        if not should_keep_waiting(elapsed, timeout, all_ok):
            break
        time.sleep(min(interval, max(0.0, timeout - elapsed)))
    rows: list[dict[str, Any]] = []
    for ep in endpoints:
        ok = statuses[ep.name] == "ok"
        row = {
            "name": ep.name,
            "kind": ep.kind,
            "port": ep.port,
            "status": "ok" if ok else "timeout",
        }
        if not ok:
            facts = collect_facts(ep, cfg)
            row["reason"] = diagnose_down(ep, **facts)
        rows.append(row)
    return rows


def ensure_serving(cfg: dict | None = None) -> list[dict[str, Any]]:
    """幂等拉起所有可由本编排器 spawn 的端点 (已 reachable 则跳过)。

    chroma 也可由本编排器拉起; self_spawned 仅表示旧 launcher 路径仍能自拉起。
    返回每端点 {name, action, status, pid?}。
    """
    cfg = cfg if cfg is not None else load_config()
    log_dir = default_log_dir()
    results: list[dict[str, Any]] = []
    for ep in iter_endpoints(cfg):
        status = probe(ep)
        if status == "ok":
            results.append({"name": ep.name, "action": "already-up", "status": "ok"})
            continue
        result = spawn_endpoint(ep, log_dir=log_dir)
        if result["action"] == "skip":
            result["status"] = status
        results.append(result)
    return results


# systemd 端点单元名 (collect_facts 探 unit 状态 + mcp_systemd 渲染都用; 故留在本模块)。
def systemd_unit_name(ep: MCPEndpoint) -> str:
    return mcp_systemd_unit_for_kind(ep.kind).removesuffix(".service")


# systemd 单元渲染 + 安装已抽到 mcp_systemd.py (file-discipline §1: 单文件 ≤600 行)。
# 此处 re-export 保持向后兼容: cli.py / tests 仍 `mcp_serve.install_systemd` / `.render_*` /
# `.SYSTEMD_KINDS`。mcp_systemd 对本模块 helper 用函数内 import, 故无循环依赖。
from codev_platform.mcp_systemd import (  # noqa: E402
    SYSTEMD_KINDS,
    rewrite_python_argv,
    render_systemd_units,
    render_reindex_unit,
    render_webhook_unit,
    render_agent_unit,
    render_web_unit,
    render_clock_resync_units,
    render_memory_maintenance_units,
    install_systemd,
)
