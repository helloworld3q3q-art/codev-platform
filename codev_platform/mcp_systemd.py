"""平台 systemd 常驻单元渲染 + 安装 (从 mcp_serve.py 抽出, file-discipline §1: 单文件 ≤600 行)。

职责边界:
- `mcp_serve.py` —— 端点枚举 / 命令构造 / 健康探测 / spawn (运行态)。
- 本模块 —— 把这些端点(+ reindex / webhook / agent / clock 等非端点常驻服务)渲染成
  systemd unit 文件, 并生成单条 `sudo bash install.sh` 一键安装脚本 (部署态)。

systemd 常驻 (Linux): 按 config 生成 unit, 开机自起 + 挂了自动重启。复用
`iter_endpoints` 的 cmd/cwd, 路径/端口全来自 config —— 任意 Linux 部署可复现。
chroma 也纳入 (GPU daemon): server.py 端口走 PLATFORM_DOCS_DAEMON_PORT env, prewarm
让 systemd 起来即加载模型 —— 由 render_systemd_units 按 ep.kind=='chroma' 注入。

循环 import 规避: mcp_serve.py 末尾 re-export 本模块的 render_*/install_systemd 作向后
兼容 (cli.py / tests 仍 `mcp_serve.install_systemd`), 故本模块对 mcp_serve 的 helper
(iter_endpoints / systemd_unit_name / _venv_python / _resolve_venv_scripts) 采用**函数内
import**, 不在模块顶部依赖 mcp_serve。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from codev_platform.core.config import get as _cfg_get


SYSTEMD_KINDS = ("chroma", "codegraph", "agent_memory", "graph")


def _environment_file_line(cfg: dict) -> str:
    env_file = str(_cfg_get(cfg, "systemd.env_file") or "").strip()
    if not env_file:
        return ""
    return f"EnvironmentFile=-{env_file}\n"


def render_systemd_units(cfg: dict, user: str, *, kinds=SYSTEMD_KINDS) -> dict[str, str]:
    """生成 {unit文件名: 内容}。ExecStart/WorkingDirectory 直接取自 iter_endpoints。"""
    import shlex
    from codev_platform.mcp_serve import (
        _resolve_venv_scripts, iter_endpoints, systemd_unit_name,
    )
    venv_bin = str(_resolve_venv_scripts(cfg))
    home = str(Path.home())
    units: dict[str, str] = {}
    for ep in iter_endpoints(cfg):
        if ep.kind not in kinds or not ep.cmd:
            continue
        execstart = " ".join(shlex.quote(c) for c in ep.cmd)
        env_lines = _environment_file_line(cfg)
        env_lines += f"Environment=PATH=/usr/local/bin:/usr/bin:/bin:{venv_bin}\n"
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
    from codev_platform.mcp_serve import _resolve_venv_scripts, _venv_python
    venv_py = _venv_python(cfg)
    venv_bin = str(_resolve_venv_scripts(cfg))
    home = str(Path.home())
    execstart = f"{shlex.quote(str(venv_py))} -m codev_platform.cli reindex-queue worker"
    env_lines = _environment_file_line(cfg)
    env_lines += f"Environment=PATH=/usr/local/bin:/usr/bin:/bin:{venv_bin}\n"
    content = (
        "[Unit]\n"
        "Description=codev reindex worker (写队列串行消费者)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={home}\n"
        f"{env_lines}"
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
    from codev_platform.mcp_serve import _resolve_venv_scripts, _venv_python
    venv_py = _venv_python(cfg)
    venv_bin = str(_resolve_venv_scripts(cfg))
    home = str(Path.home())
    execstart = f"{shlex.quote(str(venv_py))} -m codev_platform.cli webhook serve"
    env_lines = _environment_file_line(cfg)
    env_lines += f"Environment=PATH=/usr/local/bin:/usr/bin:/bin:{venv_bin}\n"
    content = (
        "[Unit]\n"
        "Description=codev webhook receiver (VCS push -> enqueue reindex)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={home}\n"
        f"{env_lines}"
        f"ExecStart={execstart}\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    return "codev-webhook.service", content


def render_agent_unit(cfg: dict, user: str) -> tuple[str, str]:
    """E16: agent HTTP 服务 (codev-agent) 的 systemd unit —— 常驻 + 崩溃重启。

    ExecStart 走平台 venv python -m codev_platform.cli agent serve。
    注意: agent HTTP 面依赖可选 extra [agent] (fastapi/uvicorn/anthropic);
    平台 venv 未装时服务起不来属预期 —— `pip install -e <repo>[agent]` 后即可。
    绑 127.0.0.1 (本地)。host/port 取 config.agent.{host,port}, 缺省回退 CLI 默认。
    """
    import shlex
    from codev_platform.mcp_serve import _resolve_venv_scripts, _venv_python
    venv_py = _venv_python(cfg)
    venv_bin = str(_resolve_venv_scripts(cfg))
    repo_root = str(Path(__file__).resolve().parent.parent)
    host = _cfg_get(cfg, "agent.host") or "127.0.0.1"
    port = _cfg_get(cfg, "agent.port") or 8848
    execstart = (
        f"{shlex.quote(str(venv_py))} -m codev_platform.cli agent serve "
        f"--host {host} --port {port}"
    )
    env_lines = _environment_file_line(cfg)
    env_lines += f"Environment=PATH=/usr/local/bin:/usr/bin:/bin:{venv_bin}\n"
    content = (
        "[Unit]\n"
        "Description=codev agent HTTP service (FastAPI; needs venv extra [agent])\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"WorkingDirectory={repo_root}\n"
        f"{env_lines}"
        f"ExecStart={execstart}\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    return "codev-agent.service", content


def render_clock_resync_units(user: str) -> dict[str, str]:
    """B8: 时钟重同步 service + timer —— 缓解 WSL2 睡眠后系统时钟漂移。

    WSL2 从睡眠/挂起恢复后系统时钟可能落后真实时间, 导致 daemon health/证书等抖动
    (本会话 chroma flap 根因)。hwclock --hctosys 从硬件时钟回灌系统时钟纠偏。
    best-effort: 不支持 hwclock (无 RTC / 容器化) 时退化为 no-op, 不报错阻塞;
    彻底失效时仍可 `wsl --shutdown` 重同步。
    """
    service = (
        "[Unit]\n"
        "Description=codev clock resync (hwclock -> system; WSL2 sleep drift fix)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        # best-effort: hwclock 不可用时不让 unit 进 failed 态 (|| true 兜底)
        "ExecStart=/bin/sh -c '/sbin/hwclock --hctosys || hwclock -s || true'\n"
    )
    timer = (
        "[Unit]\n"
        "Description=codev clock resync timer (boot + every 5min)\n\n"
        "[Timer]\n"
        "OnBootSec=1min\n"
        "OnUnitActiveSec=5min\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {
        "codev-clock-resync.service": service,
        "codev-clock-resync.timer": timer,
    }


def render_memory_maintenance_units(cfg: dict, user: str) -> dict[str, str]:
    """B2 (M4 cron): memory 维护 service + timer —— 每日 TTL 归档(轻、无 LLM、安全)。

    定时只跑 `run_memory_maintenance.py`(无 args)= TTL 归档 + (recall_backend=vector 时)压缩归档
    原条的向量 GC。**压缩(LLM 融合)需 scope 参数 + provider 仍手动跑**, 定时不碰 LLM。单实例锁在
    脚本内(advisory_lock)与手动跑互斥。oneshot + timer, 不常驻。
    """
    import shlex
    from codev_platform.mcp_serve import _resolve_venv_scripts, _venv_python
    venv_py = _venv_python(cfg)
    venv_bin = str(_resolve_venv_scripts(cfg))
    repo_root = Path(__file__).resolve().parent.parent  # codev_platform 包的上一级 = 仓根
    script = repo_root / "scripts" / "run_memory_maintenance.py"
    execstart = f"{shlex.quote(str(venv_py))} {shlex.quote(str(script))}"
    env_lines = _environment_file_line(cfg)
    env_lines += f"Environment=PATH=/usr/local/bin:/usr/bin:/bin:{venv_bin}\n"
    service = (
        "[Unit]\n"
        "Description=codev memory maintenance (TTL 归档 + 向量 GC; oneshot)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"User={user}\n"
        f"WorkingDirectory={repo_root}\n"
        f"{env_lines}"
        f"ExecStart={execstart}\n"
    )
    timer = (
        "[Unit]\n"
        "Description=codev memory maintenance timer (daily 04:00 + 开机补跑)\n\n"
        "[Timer]\n"
        "OnCalendar=*-*-* 04:00:00\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {
        "codev-memory-maintenance.service": service,
        "codev-memory-maintenance.timer": timer,
    }


def install_systemd(cfg: dict, user: str) -> dict[str, Any]:
    """以普通用户生成 unit 到 ~/codev-systemd/(config 读对), 返回唯一一条 sudo 安装命令。

    不在此直接 sudo: sudo 会切到 root 的 HOME → 读错 config。生成归生成(用户态),
    装到 /etc/systemd/system + enable 归 root(打印命令让用户跑)。
    """
    import shlex
    units = render_systemd_units(cfg, user)
    # 非 MCP 端点的常驻服务, 单独并入: reindex worker (串行消费写队列) + webhook 接收器 (push 触发)
    # + agent HTTP 服务 (E16, 需 venv extra [agent])。
    for _rname, _rcontent in (
        render_reindex_unit(cfg, user),
        render_webhook_unit(cfg, user),
        render_agent_unit(cfg, user),
    ):
        units[_rname] = _rcontent
    # B8: 时钟重同步 service + timer (WSL2 睡眠漂移纠偏)。
    units.update(render_clock_resync_units(user))
    # B2: memory 维护 service + timer (每日 TTL 归档 + 向量 GC)。
    units.update(render_memory_maintenance_units(cfg, user))
    out_dir = Path.home() / "codev-systemd"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fname, content in units.items():
        p = out_dir / fname
        p.write_text(content, encoding="utf-8")
        paths.append(p)
    svc_names = [p.name for p in paths]
    # .timer 必须按全名 enable/start (用 stem 会落到同名 .service); oneshot 的
    # clock-resync.service 由 timer 触发, 既不 enable 也不 restart (无 [Install] + oneshot)。
    timers = [p.name for p in paths if p.suffix == ".timer"]
    long_running = [p.stem for p in paths
                    if p.suffix == ".service" and p.name != "codev-clock-resync.service"]
    enable_names = long_running + timers
    # 同时写一个 install.sh: 单条 `sudo bash <dir>/install.sh` 即可装, 避免长命令多行
    # 粘贴在终端被换行拆断 (cp 多文件 + && 链很容易折行)。用 restart 而非 enable --now,
    # 这样重复执行 / unit 内容变更时能真正重启生效; enable(不带 --now)只设开机自起。
    if paths:
        lines = ["#!/usr/bin/env bash", "set -e"]
        lines += [f"cp {shlex.quote(str(p))} /etc/systemd/system/" for p in paths]
        lines.append("systemctl daemon-reload")
        lines.append("systemctl enable " + " ".join(enable_names))
        # reset-failed 清掉可能的 start-limit (崩溃重启过多会进 failed 态, 否则 restart 被拒)
        lines.append("systemctl reset-failed " + " ".join(long_running) + " 2>/dev/null || true")
        lines.append("systemctl restart " + " ".join(long_running))
        # timer 用 start (restart 对未 active timer 也可, 但 start 语义更清晰)
        if timers:
            lines.append("systemctl restart " + " ".join(timers))
        lines.append('echo "[install.sh] done"')
        install_sh = out_dir / "install.sh"
        install_sh.write_text("\n".join(lines) + "\n", encoding="utf-8")
        install_sh.chmod(0o755)
        sudo_cmd = f"sudo bash {install_sh}"
    else:
        sudo_cmd = "(无可安装的端点: 检查 config.projects 是否配了 repo_path)"
    return {"dir": str(out_dir), "units": svc_names, "sudo_cmd": sudo_cmd}
