"""平台 systemd 常驻单元渲染 + 安装 (从 mcp_serve.py 抽出, file-discipline §1: 单文件 ≤600 行)。

职责边界:
- `mcp_serve.py` —— 端点枚举 / 命令构造 / 健康探测 / spawn (运行态)。
- 本模块 —— 把这些端点(+ reindex / webhook / agent / clock 等非端点常驻服务)渲染成
  systemd unit 文件，并生成直接调用不可变 release Python 的固定提权命令（部署态）。

systemd 常驻 (Linux): 按 config 生成 unit, 开机自起 + 挂了自动重启。复用
`iter_endpoints` 的命令与端口真值，解释器和工作目录统一由版本运行时策略接管。
chroma 也纳入 (GPU daemon): server.py 端口走 PLATFORM_DOCS_DAEMON_PORT env, prewarm
让 systemd 起来即加载模型 —— 由 render_systemd_units 按 ep.kind=='chroma' 注入。

循环 import 规避: mcp_serve.py 末尾 re-export 本模块的 render_*/install_systemd 作向后
兼容 (cli.py / tests 仍 `mcp_serve.install_systemd`), 故本模块对 mcp_serve 的端点
helper 采用**函数内 import**，不在模块顶部依赖 mcp_serve。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from codev_platform.core.config import get as _cfg_get
from codev_platform.core.runtime_models import SystemdRuntime
from codev_platform.core.systemd_environment_file import (
    require_systemd_environment_file_path,
)
from codev_platform.core.systemd_maintenance_contract import (
    CODEGRAPH_MAINTENANCE_CONDITION_DIRECTIVE,
)
from codev_platform.reindex.owner_readiness import (
    OWNER_BOOTSTRAP_EXIT_CODE,
    OWNER_OPERATOR_BLOCKED_EXIT_CODE,
)
from codev_platform.mcp_systemd_unit_registry import (
    MANAGED_SYSTEMD_UNIT_NAMES,
    REINDEX_SYSTEMD_UNIT_NAME,
    managed_systemd_unit,
)
from codev_platform.mcp_systemd_install_contract import SystemdRuntimeBinding
from codev_platform.runtime_release_binding import BoundRelease
from codev_platform.runtime_service_process import resolve_service_account
from codev_platform.mcp_systemd_runtime import (
    render_runtime_environment_lines,
    resolve_systemd_runtime as _systemd_runtime,
    rewrite_python_argv,
)


SYSTEMD_KINDS = ("chroma", "codegraph", "agent_memory", "graph")


def _environment_file_line(cfg: dict) -> str:
    value = _environment_file_path(cfg)
    return "" if value is None else f"EnvironmentFile={value}\n"


def _environment_file_path(cfg: dict) -> str | None:
    value = _cfg_get(cfg, "systemd.env_file")
    if value in (None, ""):
        return None
    return require_systemd_environment_file_path(value)


def _python_runtime_environment_lines(
    cfg: dict,
    runtime: SystemdRuntime,
    *,
    working_directory: str = "%h",
) -> str:
    """生成 Python 服务共用的版本环境，顺序与目标用户探针保持一致。"""
    return render_runtime_environment_lines(
        _environment_file_line(cfg),
        runtime,
        working_directory=working_directory,
    )


def _render_python_daemon_content(
    cfg: dict,
    user: str,
    *,
    description: str,
    runtime: SystemdRuntime,
    argv: Sequence[str | Path],
    extra_service_lines: str = "",
    working_directory: str = "%h",
) -> str:
    """渲染普通常驻 Python 服务，统一 release 环境隔离与重启策略。"""
    import shlex

    execstart = shlex.join(rewrite_python_argv(argv, runtime))
    return (
        "[Unit]\n"
        f"Description={description}\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"User={user}\n"
        f"{_python_runtime_environment_lines(cfg, runtime, working_directory=working_directory)}"
        f"{extra_service_lines}"
        f"ExecStart={execstart}\n"
        "Restart=always\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def render_systemd_units(
    cfg: dict,
    user: str,
    *,
    kinds=SYSTEMD_KINDS,
    runtime: SystemdRuntime | None = None,
    runtime_binding: SystemdRuntimeBinding | None = None,
    working_directory: str = "%h",
) -> dict[str, str]:
    """从端点真值生成 MCP unit，并统一绑定同一版本运行时。"""
    import shlex
    from codev_platform.mcp_serve import iter_endpoints, systemd_unit_name

    selected = _systemd_runtime(cfg, runtime)
    protected_interpreter = _protected_interpreter(runtime_binding, user)
    units: dict[str, str] = {}
    for ep in iter_endpoints(cfg):
        if ep.kind not in kinds or not ep.cmd:
            continue
        command = rewrite_python_argv(ep.cmd, selected)
        if ep.kind == "codegraph" and protected_interpreter is not None:
            command[0] = protected_interpreter
        execstart = shlex.join(command)
        env_lines = _python_runtime_environment_lines(
            cfg,
            selected,
            working_directory=working_directory,
        )
        lifecycle_lines = ""
        # chroma daemon 的监听端口走 PLATFORM_DOCS_DAEMON_PORT env (server.py 不读
        # config.daemon.port, 见 server.py:main); prewarm 让 unit 起来即加载 GPU 模型,
        # 避免业务仓首个 search_docs 冷启动 60s 超时。端口仍来自 config (ep.port)。
        # 所有受管服务固定在目标用户主目录运行，不再依赖源码仓工作目录；业务项目仍按
        # ?project_id= 多租户路由。
        if ep.kind == "chroma":
            env_lines += f"Environment=PLATFORM_DOCS_DAEMON_PORT={ep.port}\n"
            env_lines += "Environment=PLATFORM_DOCS_PREWARM=true\n"
        if ep.kind == "codegraph":
            # 常态关闭 watcher 与共享 daemon；首次查询的追赶同步仍非只读。
            env_lines += "Environment=CODEGRAPH_NO_WATCH=1\n"
            env_lines += "Environment=CODEGRAPH_NO_DAEMON=1\n"
            lifecycle_lines = "KillMode=control-group\nTimeoutStopSec=30\nSendSIGKILL=yes\n"
        maintenance_condition = (
            f"{CODEGRAPH_MAINTENANCE_CONDITION_DIRECTIVE}\n" if ep.kind == "codegraph" else ""
        )
        units[f"{systemd_unit_name(ep)}.service"] = (
            "[Unit]\n"
            f"Description=codev MCP endpoint {ep.name} (port {ep.port})\n"
            "After=network.target\n\n"
            f"{maintenance_condition}"
            "[Service]\n"
            "Type=simple\n"
            f"{lifecycle_lines}"
            f"User={user}\n"
            f"{env_lines}"
            f"ExecStart={execstart}\n"
            "Restart=always\n"
            "RestartSec=3\n\n"
            "[Install]\n"
            "WantedBy=multi-user.target\n"
        )
    return units


def render_reindex_unit(
    cfg: dict,
    user: str,
    *,
    runtime: SystemdRuntime | None = None,
    runtime_binding: SystemdRuntimeBinding | None = None,
    working_directory: str = "%h",
) -> tuple[str, str]:
    """reindex worker (codev-reindex) 的 systemd unit —— 非 MCP 端点 (无端口/health), 单独生成。

    写队列的常驻串行消费者 (写侧串行化, 读并发不受影响); 开机自起 + 崩溃重启。
    启动限流允许短暂故障自愈，同时阻止确定性缺陷形成无限快速重启风暴。
    Delegate 允许 worker 管理每次 attempt 的子 cgroup；停止服务时由 systemd 回收整个控制组。
    """
    import shlex

    selected = _systemd_runtime(cfg, runtime)
    command = rewrite_python_argv(
        (
            "python",
            "-m",
            "codev_platform.cli",
            "reindex-queue",
            "worker",
            "--require-execution-mode",
            "isolated",
        ),
        selected,
    )
    protected_interpreter = _protected_interpreter(runtime_binding, user)
    if protected_interpreter is not None:
        command[0] = protected_interpreter
    execstart = shlex.join(command)
    env_lines = _python_runtime_environment_lines(
        cfg,
        selected,
        working_directory=working_directory,
    )
    content = (
        "[Unit]\n"
        "Description=codev reindex worker (写队列串行消费者)\n"
        "After=network.target\n"
        "StartLimitIntervalSec=120\n"
        "StartLimitBurst=5\n\n"
        "[Service]\n"
        "Type=simple\n"
        "Delegate=yes\n"
        "KillMode=control-group\n"
        "TimeoutStopSec=30\n"
        "SendSIGKILL=yes\n"
        f"User={user}\n"
        f"{env_lines}"
        f"ExecStart={execstart}\n"
        "Restart=always\n"
        "RestartPreventExitStatus="
        f"{OWNER_BOOTSTRAP_EXIT_CODE} {OWNER_OPERATOR_BLOCKED_EXIT_CODE}\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    return REINDEX_SYSTEMD_UNIT_NAME, content


def render_webhook_unit(
    cfg: dict,
    user: str,
    *,
    runtime: SystemdRuntime | None = None,
    working_directory: str = "%h",
) -> tuple[str, str]:
    """webhook 接收器 (codev-webhook) 的 systemd unit —— 非 MCP 端点, 单独生成。

    收 VCS push → enqueue reindex (push 即触发); 开机自起 + 崩溃重启。绑 127.0.0.1
    (本机 Gitea 可达); 远程 VCS 需自行反代 + 配 webhook.secret 验签。
    """
    selected = _systemd_runtime(cfg, runtime)
    content = _render_python_daemon_content(
        cfg,
        user,
        description="codev webhook receiver (VCS push -> enqueue reindex)",
        runtime=selected,
        argv=("python", "-m", "codev_platform.cli", "webhook", "serve"),
        working_directory=working_directory,
    )
    return "codev-webhook.service", content


def render_agent_unit(
    cfg: dict,
    user: str,
    *,
    runtime: SystemdRuntime | None = None,
    working_directory: str = "%h",
) -> tuple[str, str]:
    """E16: agent HTTP 服务 (codev-agent) 的 systemd unit —— 常驻 + 崩溃重启。

    ExecStart 走 current release 的 Python 模块入口。
    注意: agent HTTP 面依赖可选 extra [agent] (fastapi/uvicorn/anthropic);
    正式运行时必须在构建阶段纳入这些依赖，预检不允许临时从源码仓补装。
    绑 127.0.0.1 (本地)。host/port 取 config.agent.{host,port}, 缺省回退 CLI 默认。
    """
    selected = _systemd_runtime(cfg, runtime)
    host = _cfg_get(cfg, "agent.host") or "127.0.0.1"
    port = _cfg_get(cfg, "agent.port") or 8848
    content = _render_python_daemon_content(
        cfg,
        user,
        description="codev agent HTTP service (FastAPI; needs venv extra [agent])",
        runtime=selected,
        argv=(
            "python",
            "-m",
            "codev_platform.cli",
            "agent",
            "serve",
            "--host",
            str(host),
            "--port",
            str(port),
        ),
        working_directory=working_directory,
    )
    return "codev-agent.service", content


def render_web_unit(
    cfg: dict,
    user: str,
    *,
    runtime: SystemdRuntime | None = None,
    working_directory: str = "%h",
) -> tuple[str, str]:
    """管理后台 Web 服务；与其它 Python 常驻服务绑定同一 release。"""
    from codev_platform.web.config import web_host, web_port

    selected = _systemd_runtime(cfg, runtime)
    host = web_host(cfg)
    port = web_port(cfg)
    return "codev-web.service", _render_python_daemon_content(
        cfg,
        user,
        description="codev web backend (FastAPI)",
        runtime=selected,
        argv=(
            "python",
            "-m",
            "codev_platform.cli",
            "web",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
        ),
        working_directory=working_directory,
    )


def render_clock_resync_units() -> dict[str, str]:
    """B8: 时钟重同步 service + timer —— 缓解 WSL2 睡眠后系统时钟漂移。

    WSL2 从睡眠/挂起恢复后系统时钟可能落后真实时间, 导致 daemon health/证书等抖动
    (本会话 chroma flap 根因)。hwclock --hctosys 从硬件时钟回灌系统时钟纠偏。
    best-effort: 不支持 hwclock (无 RTC / 容器化) 时退化为 no-op, 不报错阻塞;
    彻底失效时仍可 `wsl --shutdown` 重同步。
    """
    import shlex

    command = shlex.join(("/bin/sh", "-c", "/sbin/hwclock --hctosys || hwclock -s || true"))
    service = (
        "[Unit]\n"
        "Description=codev clock resync (hwclock -> system; WSL2 sleep drift fix)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        # 纯操作系统单元不依赖 Python 或源码检出；失败按既有 best-effort 语义降级。
        f"ExecStart={command}\n"
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


def render_memory_maintenance_units(
    cfg: dict,
    user: str,
    *,
    runtime: SystemdRuntime | None = None,
    working_directory: str = "%h",
) -> dict[str, str]:
    """B2 (M4 cron): memory 维护 service + timer —— 每日 TTL 归档(轻、无 LLM、安全)。

    定时只跑打包模块入口(无 args)= TTL 归档 + (recall_backend=vector 时)压缩归档原条的
    向量 GC。**压缩(LLM 融合)需 scope 参数 + provider 仍手动跑**，定时不碰 LLM。
    单实例锁在模块内(advisory_lock)与手动跑互斥。oneshot + timer, 不常驻。
    """
    import shlex

    selected = _systemd_runtime(cfg, runtime)
    execstart = shlex.join(
        rewrite_python_argv(
            ("python", "-m", "codev_platform.ops.memory_maintenance"),
            selected,
        )
    )
    env_lines = _python_runtime_environment_lines(
        cfg,
        selected,
        working_directory=working_directory,
    )
    service = (
        "[Unit]\n"
        "Description=codev memory maintenance (TTL 归档 + 向量 GC; oneshot)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"User={user}\n"
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


def _write_install_manifest(
    paths: list[Path],
    *,
    runtime_revision: str,
    runtime_binding: SystemdRuntimeBinding,
) -> Path:
    """把渲染结果收敛为安装事务唯一接受的受限声明。"""
    import hashlib
    import json

    from codev_platform.mcp_systemd_install_contract import SystemdUnitActivationMode
    from codev_platform.mcp_systemd_install_transaction import (
        SystemdInstallManifest,
        SystemdUnitInstallSpec,
    )

    manifest = SystemdInstallManifest(
        units=tuple(
            SystemdUnitInstallSpec(
                source=path,
                content_digest=hashlib.sha256(path.read_bytes()).hexdigest(),
                enable=managed_systemd_unit(path.name).enable,
                restart=managed_systemd_unit(path.name).restart,
                activation_mode=SystemdUnitActivationMode(
                    managed_systemd_unit(path.name).activation_mode
                ),
            )
            for path in paths
        ),
        runtime_revision=runtime_revision,
        runtime_binding=runtime_binding,
    )
    manifest_path = paths[0].parent / "install-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_mapping(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _install_transaction_argv(
    manifest_path: Path,
    runtime_binding: SystemdRuntimeBinding,
    *,
    install_only: bool,
) -> tuple[str, ...]:
    """生成显式维护态事务参数；root 不执行用户可写 Shell。"""
    return (
        str(runtime_binding.immutable_python),
        "-I",
        "-m",
        "codev_platform.mcp_systemd_install_transaction",
        "--manifest",
        str(manifest_path),
        "--install-only" if install_only else "--maintenance-stage",
    )


def render_managed_systemd_units(
    cfg: dict,
    user: str,
    *,
    runtime: SystemdRuntime,
    runtime_binding: SystemdRuntimeBinding | None = None,
    working_directory: str = "%h",
) -> dict[str, str]:
    """从单一配置快照聚合固定 unit；不写文件、不读取第二份配置。"""
    units = render_systemd_units(
        cfg,
        user,
        runtime=runtime,
        runtime_binding=runtime_binding,
        working_directory=working_directory,
    )
    for name, content in (
        render_reindex_unit(
            cfg,
            user,
            runtime=runtime,
            runtime_binding=runtime_binding,
            working_directory=working_directory,
        ),
        render_webhook_unit(cfg, user, runtime=runtime, working_directory=working_directory),
        render_agent_unit(cfg, user, runtime=runtime, working_directory=working_directory),
        render_web_unit(cfg, user, runtime=runtime, working_directory=working_directory),
    ):
        units[name] = content
    units.update(render_clock_resync_units())
    units.update(
        render_memory_maintenance_units(
            cfg,
            user,
            runtime=runtime,
            working_directory=working_directory,
        )
    )
    if frozenset(units) != MANAGED_SYSTEMD_UNIT_NAMES:
        raise ValueError("受管 systemd unit 集合漂移")
    return units


def _protected_interpreter(
    runtime_binding: SystemdRuntimeBinding | None,
    user: str,
) -> str | None:
    """只为 stage receipt 受保护的写服务提供本次绑定的不可变解释器。"""
    if runtime_binding is None:
        return None
    if type(runtime_binding) is not SystemdRuntimeBinding or runtime_binding.target_user != user:
        raise ValueError("受保护 systemd unit 运行时绑定无效")
    return runtime_binding.immutable_python.as_posix()


def install_systemd(
    cfg: dict,
    user: str,
    *,
    runtime: SystemdRuntime | None = None,
    no_restart: bool = False,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """以普通用户生成受限声明，返回直达不可变 Python 的 sudo 安装命令。

    不在此直接 sudo: sudo 会切到 root 的 HOME → 读错 config。生成归生成(用户态),
    装到 /etc/systemd/system + enable 归 root（打印固定参数命令让用户跑）。
    """
    import shlex
    import sys

    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise RuntimeError("systemd 安装包必须在 WSL/Linux 内生成")

    if type(no_restart) is not bool:
        raise TypeError("no_restart 必须是布尔值")
    selected = _systemd_runtime(cfg, runtime)
    bound = _snapshot_systemd_release(selected.release_root)
    runtime_binding = _systemd_runtime_binding(
        bound,
        user=user,
        environment_file=_environment_file_path(cfg),
    )
    working_directory = _service_working_directory(user)
    units = render_managed_systemd_units(
        cfg,
        user,
        runtime=selected,
        runtime_binding=runtime_binding,
        working_directory=working_directory,
    )
    out_dir = Path.home() / "codev-systemd"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fname, content in units.items():
        p = out_dir / fname
        p.write_text(content, encoding="utf-8")
        p.chmod(0o644)
        paths.append(p)
    svc_names = [p.name for p in paths]
    if paths:
        manifest_path = _write_install_manifest(
            paths,
            runtime_revision=bound.runtime_revision,
            runtime_binding=runtime_binding,
        )
        transaction_argv = _install_transaction_argv(
            manifest_path,
            runtime_binding,
            install_only=no_restart,
        )
        sudo_argv = ("sudo", *transaction_argv)
        sudo_cmd = shlex.join(sudo_argv)
    else:
        transaction_argv = ()
        sudo_argv = ()
        sudo_cmd = "(无可安装的端点: 检查 config.projects 是否配了 repo_path)"
    return {
        "dir": str(out_dir),
        "units": svc_names,
        "transaction_argv": transaction_argv,
        "sudo_argv": sudo_argv,
        "sudo_cmd": sudo_cmd,
        "no_restart": no_restart,
    }


def _snapshot_systemd_release(root: Path) -> BoundRelease:
    from codev_platform.runtime_release_binding import snapshot_current_release

    return snapshot_current_release(root)


def _systemd_runtime_binding(
    bound: BoundRelease,
    *,
    user: str,
    environment_file: str | None,
) -> SystemdRuntimeBinding:
    return SystemdRuntimeBinding(
        release_root=bound.root.as_posix(),
        expected_release_id=bound.release_id,
        target_user=user,
        environment_file=environment_file,
    )


def _service_working_directory(user: str) -> str:
    """生产 manifest 必须绑定目标服务账号 home，禁止 system unit 使用 root 的 %h。"""
    try:
        return resolve_service_account(user).home.as_posix()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeError("systemd 服务账号工作目录无法解析") from None
