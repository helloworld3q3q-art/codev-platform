"""MCP / daemon 编排子命令 (daemon / serve-mcp / mcp-source) —— 从 cli.py 拆出。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from codev_platform.cli_cmds._shared import _eprint, _print
from codev_platform.core.project_id import CONFIG_RELPATH, ProjectIdError, validate
from codev_platform.core.wsl_data_owner import WslDataOwner, wsl_data_owner
from codev_platform.mcp_systemd_unit_registry import managed_systemd_unit


def _start_wsl_systemd_unit(owner: WslDataOwner, unit: str) -> dict[str, str]:
    """Start one allow-listed WSL systemd unit without exposing subprocess output."""
    managed_systemd_unit(unit)
    argv = [
        "wsl.exe",
        "-d",
        owner.distro,
        "-u",
        "root",
        "--",
        "systemctl",
        "start",
        unit,
    ]
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except FileNotFoundError:
        return {"action": "failed", "error": "wsl.exe unavailable"}
    except subprocess.TimeoutExpired:
        return {"action": "failed", "error": "WSL systemd start timeout"}
    except OSError as exc:
        return {"action": "failed", "error": type(exc).__name__}
    if completed.returncode != 0:
        return {"action": "failed", "error": f"systemctl rc={completed.returncode}"}
    return {"action": "started"}


def cmd_daemon(args: argparse.Namespace) -> int:
    """chroma daemon 生命周期: status (查 /health) / stop (按 pid 杀)。

    spawn 由业务项目首次 Claude session 经 launcher 自动完成, 不在此处 start —
    避免脱离 project_id 上下文起一个无主 daemon。stop 后下次 session 会重新拉起。
    """
    from codev_platform.chroma import launcher  # 复用 health/port 逻辑

    health = launcher._fetch_daemon_health()
    if args.action == "status":
        if health is None:
            _print(f"daemon: DOWN ({launcher.DAEMON_URL})")
            _print("提示: 打开业务项目任一 Claude Code 会话会自动拉起 daemon。")
            return 1
        proc = health.get("process") or {}
        _print(
            f"daemon: {health.get('status', '?').upper()} pid={proc.get('pid')} "
            f"uptime={proc.get('uptime_sec')}s rss={proc.get('rss_mb')}MiB"
        )
        _print(
            f"  model={health.get('model')} reranker={health.get('reranker')} "
            f"sse_sessions={health.get('sse_sessions')}"
        )
        for p in health.get("loaded_projects", []):
            _print(
                f"  [{p.get('project_id')}] chunks={p.get('chunks')} "
                f"bm25={p.get('bm25')} last_indexed={p.get('last_indexed_at')}"
            )
        return 0
    if args.action == "stop":
        if health is None:
            _print("daemon 未运行, 无需 stop。")
            return 0
        pid = (health.get("process") or {}).get("pid")
        if not pid:
            _eprint("daemon /health 未返回 pid (旧版 daemon?), 无法自动 stop。请手动结束进程。")
            return 1
        import subprocess

        if sys.platform == "win32":
            rc = subprocess.call(["taskkill", "/PID", str(pid), "/F"])
        else:
            rc = subprocess.call(["kill", str(pid)])
        _print(f"stop pid={pid} rc={rc}。下次 Claude session 会重新拉起。")
        return rc
    return 1


def cmd_serve_mcp(args: argparse.Namespace) -> int:
    """平台 MCP 端点编排: status (探测) / start (幂等拉起 codegraph / graph / agent-memory 端点)。

    chroma daemon 由业务仓 Claude 会话经 launcher 自 spawn, 本命令不拉起它, 只报状态。
    """
    if args.action == "install-systemd":
        return _cmd_install_systemd(args)

    from codev_platform import mcp_serve
    from codev_platform import mcp_source_client
    from codev_platform.core.config import load_config

    cfg = load_config()
    owner = wsl_data_owner(cfg)
    if args.action == "status":
        rows = (
            mcp_source_client.probe_source_all(cfg, "platform")
            if owner is not None
            else mcp_serve.probe_all(cfg, diagnose=True)
        )
        _print(
            f"{'endpoint'.ljust(22)} {'kind'.ljust(11)} {'port'.ljust(6)} status   reason / sse_url"
        )
        _print("-" * 90)
        any_down = False
        for r in rows:
            mark = "OK  " if r["status"] == "ok" else "DOWN"
            if r["status"] != "ok" and not r["self_spawned"]:
                any_down = True
            tail = r["sse_url"] if r["status"] == "ok" else (r.get("reason") or r["sse_url"])
            _print(
                f"{r['name'].ljust(22)} {r['kind'].ljust(11)} {str(r['port']).ljust(6)} "
                f"{mark}     {tail}"
            )
        if owner is None:
            for w in mcp_serve.check_port_consistency(cfg):  # 显式 local 端口 ≠ bind 口 → 提示(不阻断)
                _print(f"WARN  {w}")
            mark, msg = _reindex_worker_status_line()
        else:
            mark, msg = "INFO", f"WSL data owner={owner.distro}; worker 由 WSL systemd 管理"
        _print("")
        _print(f"{'reindex-worker'.ljust(22)} {'queue'.ljust(11)} {'-'.ljust(6)} {mark}     {msg}")
        return 1 if any_down else 0
    if args.action == "start":
        if owner is None:
            results = mcp_serve.ensure_serving(cfg)
        else:
            results = mcp_source_client.ensure_source_serving(
                cfg,
                "platform",
                start_unit=lambda unit: _start_wsl_systemd_unit(owner, unit),
            )
        any_failed = False
        for r in results:
            extra = r.get("error") or r.get("note") or ""
            pid = f" pid={r['pid']}" if r.get("pid") else ""
            _print(f"  {r['name'].ljust(22)} {r['action']}{pid}  {extra}")
            if r.get("action") in {"fail", "failed"}:
                any_failed = True
        _print()
        if getattr(args, "wait", False):
            timeout = float(getattr(args, "timeout", 60) or 60)
            _print(f"等待端点就绪 (--wait, 超时 {int(timeout)}s) ...")
            waited = (
                mcp_serve.wait_until_serving(cfg, timeout=timeout)
                if owner is None
                else mcp_source_client.wait_until_source_serving(
                    cfg,
                    "platform",
                    timeout=timeout,
                )
            )
            any_timeout = False
            for w in waited:
                if w["status"] == "ok":
                    _print(f"  {w['name'].ljust(22)} OK")
                else:
                    any_timeout = True
                    _print(f"  {w['name'].ljust(22)} 超时  {w.get('reason') or ''}")
            return 1 if any_timeout else 0
        target_note = "WSL platform source" if owner is not None else "local source"
        _print(
            f"提示: {target_note} 端点可能仍在预热; "
            "再跑 `codev-platform serve-mcp status` 确认。"
        )
        return 1 if any_failed else 0
    _eprint(f"unknown action: {args.action}")
    return 1


class _SystemdConfigContextError(ValueError):
    """root 生成 systemd manifest 时，服务账号配置上下文不满足边界。"""


def _cmd_install_systemd(args: argparse.Namespace) -> int:
    """生成受管 systemd manifest；root 模式可显式采用目标服务账号的配置默认值。"""
    import getpass

    from codev_platform import mcp_serve
    from codev_platform.core.config import load_config
    from codev_platform.core.runtime_models import SystemdRuntime
    from codev_platform.runtime_release import release_root

    try:
        user = _systemd_target_user(args, getpass.getuser)
        cfg = _systemd_install_config(args, user=user, load_config=load_config)
    except _SystemdConfigContextError as error:
        _eprint(str(error))
        return 1

    no_restart = bool(getattr(args, "no_restart", False))
    runtime = SystemdRuntime(release_root(cfg, explicit=getattr(args, "runtime_root", None)))
    result = mcp_serve.install_systemd(
        cfg,
        user,
        runtime=runtime,
        no_restart=no_restart,
    )
    _print(f"生成 {len(result['units'])} 个 unit 到 {result['dir']} (User={user}):")
    for unit in result["units"]:
        _print(f"  - {unit}")
    _print()
    if no_restart:
        _print("运行以下预装事务不会启动、停止或重启任何受管服务(需 root):")
    else:
        _print("先执行 reindex-maintenance prepare --yes 进入维护窗口，再跑这一条(需 root):")
    _print(f"  {result['sudo_cmd']}")
    _print()
    if no_restart:
        _print("事务仍会先以目标 User 执行权限探针，再复制、reload 和 enable unit。")
    else:
        _print("该步骤不会启动 reindex/CodeGraph；继续按 restore/rebuild/resume 状态机恢复。")
    return 0


def _systemd_target_user(args: argparse.Namespace, current_user) -> str:
    """显式 config-user 时以它为默认目标，拒绝两个账号发生漂移。"""
    config_user = getattr(args, "config_user", None)
    requested_user = getattr(args, "user", None)
    if config_user is not None and (type(config_user) is not str or not config_user):
        raise _SystemdConfigContextError("--config-user 必须是非空服务账号")
    if requested_user is not None and (type(requested_user) is not str or not requested_user):
        raise _SystemdConfigContextError("--user 必须是非空服务账号")
    if config_user is not None:
        if requested_user is None:
            raise _SystemdConfigContextError("--config-user 必须与显式 --user 一起使用")
        if requested_user != config_user:
            raise _SystemdConfigContextError("--config-user 必须与 --user 一致")
        return requested_user
    if requested_user is not None:
        return requested_user
    sudo_user = os.environ.get("SUDO_USER")
    return sudo_user or current_user()


def _systemd_install_config(args: argparse.Namespace, *, user: str, load_config):
    """普通调用保持当前上下文；root 专用模式仅替换配置默认 home。"""
    config_user = getattr(args, "config_user", None)
    if config_user is None:
        return load_config()
    if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise _SystemdConfigContextError("--config-user 仅允许 Linux root 使用")
    from codev_platform.runtime_service_process import resolve_service_account

    try:
        account = resolve_service_account(config_user)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise _SystemdConfigContextError("--config-user 服务账号无法解析") from None
    if account.name != user or not account.home.is_absolute():
        raise _SystemdConfigContextError("--config-user 服务账号身份不一致")
    return load_config(home=account.home)


def _reindex_worker_status_line() -> tuple[str, str]:
    from codev_platform.reindex.status import format_summary, summarize

    summary = summarize()
    severity = summary.get("severity") or "WARN"
    mark = severity if severity in {"OK", "WARN", "FAIL"} else "WARN"
    return mark, format_summary(summary)


def cmd_mcp_source(args: argparse.Namespace) -> int:
    """切换业务仓 .mcp.json 各 MCP 的源 (local 本机本地实例 / platform 平台基线服务器)。

    可统一切 (默认全部 3 套) 或只切指定 tool。host/port 来自 config.mcp_sources.<target>
    (缺省 local=18xxx / platform=19xxx)。project_id 取自 <repo>/.claude/project.json。
    """
    from codev_platform.core.config import load_config
    from codev_platform import mcp_serve

    repo = Path(args.repo).expanduser().resolve() if args.repo else Path.cwd()
    mcp_json = repo / ".mcp.json"
    if not mcp_json.is_file():
        _eprint(f"FATAL: 未找到 {mcp_json} (在业务仓根跑, 或 --repo 指定)")
        return 1
    pj = repo / CONFIG_RELPATH
    if not pj.is_file():
        _eprint(f"FATAL: 未找到 {pj} (先 codev-platform init)")
        return 1
    try:
        pid = validate(json.loads(pj.read_text(encoding="utf-8")).get("project_id"))
    except (json.JSONDecodeError, ProjectIdError) as exc:
        _eprint(f"FATAL: 解析 project_id 失败: {exc!s}")
        return 1

    alias = {
        "docs": "platform-docs",
        "chromadb": "platform-docs",
        "chroma": "platform-docs",
        "platform-docs": "platform-docs",
        "graph": "graph",
        "codegraph": "codegraph",
        "agent-memory": "agent-memory",
        "memory": "agent-memory",
    }
    cfg = load_config()
    try:
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _eprint(f"FATAL: {mcp_json} 解析失败: {exc!s}")
        return 1
    servers = data.get("mcpServers", {})
    sel = [alias.get(t, t) for t in args.tools] if args.tools else list(mcp_serve.MCP_SOURCE_TOOLS)

    changed: list[tuple[str, str]] = []
    for tool in sel:
        if tool not in servers:
            _print(f"  跳过 {tool} (.mcp.json 无此项)")
            continue
        try:
            url = mcp_serve.mcp_source_url(cfg, args.target, tool, pid)
        except ValueError as exc:
            _eprint(f"FATAL: {exc!s}")
            return 1
        servers[tool]["type"] = "sse"
        servers[tool]["url"] = url
        changed.append((tool, url))
    if not changed:
        _print("无改动 (选中的 tool 都不在 .mcp.json)")
        return 0
    mcp_json.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print(f"OK: {mcp_json}  ->  源 = {args.target}  (project_id={pid})")
    for tool, url in changed:
        _print(f"  {tool.ljust(14)} {url}")
    _print()
    _print("提示: 重启 Claude Code 让新 .mcp.json 生效。")
    return 0
