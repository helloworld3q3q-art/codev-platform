"""codev_platform.ops.logs -- 集中查看平台侧日志末 N 行。

  codev-platform logs [--service chroma|codegraph|webhook|reindex|audit|all] [--tail N]

纯定位 (log_sources) + 薄 IO (tail_file/run_logs)。只读, 从不写。日志本身已是 prod
脱敏的 (见 obslog), 原样打印, 不额外读 config secret。

source 归属:
  chroma/codegraph : data_root/logs/<prefix>_mcp_server.log (MCP server 进程日志)
  serve-mcp                   : mcp_serve_logs/*.log (serve-mcp spawn 的端点日志)
  audit                       : core.audit.audit_log_path()
  webhook/reindex             : systemd 服务, 输出走 journal (journalctl -u codev-webhook /
                                codev-reindex); per-repo reindex.log 在业务仓, 用 ai-health 看。
                                本命令聚合平台侧文件日志, 故这两项以 journal 提示呈现。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import codev_platform.core.runtime_artifacts as runtime_artifacts

# 走 journal 的常驻服务 (无平台侧文件日志聚合点)。
_JOURNAL_SERVICES = {
    "webhook": "codev-webhook",
    "reindex": "codev-reindex",
}


def log_sources() -> dict[str, Path]:
    """name -> 平台侧日志文件路径 (延迟 import 避免依赖运行态)。

    chroma/codegraph 各取 data_root/logs/<prefix>_mcp_server.log (前缀防多
    daemon 同名碰撞; 与各 daemon 的 _log_file() 同源); audit 取审计 jsonl。
    serve-mcp spawn 日志是一个**目录** (mcp_serve_logs/), 不在此返回单文件 ——
    由 run_logs 单独展开列其 *.log (每端点一文件)。
    """
    from codev_platform.core.audit import audit_log_path

    return {
        "chroma": runtime_artifacts.chroma_mcp_log_path(),
        "codegraph": runtime_artifacts.codegraph_mcp_log_path(),
        "audit": audit_log_path(),
    }


def serve_log_dir() -> Path:
    """serve-mcp spawn 日志目录 (mcp_serve_logs/, 每端点一 .log)。"""
    return runtime_artifacts.serve_mcp_log_dir()


def tail_file(path: Path, n: int) -> list[str]:
    """读 path 末 n 行 (含换行剥离)。缺失 / 读失败 -> [] (不报错)。"""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return []
    tail = lines[-n:] if n > 0 else lines
    return [ln.rstrip("\n") for ln in tail]


def _print_block(label: str, path: Path, n: int) -> None:
    print(f"===== [{label}] {path} =====")
    lines = tail_file(path, n)
    if not lines:
        print(f"  ({'缺失' if not Path(path).exists() else '空'})")
    else:
        for ln in lines:
            print(f"[{label}] {ln}")
    print()


def run_logs(service: str, tail: int) -> int:
    """打印各源末 tail 行, 带 [service] 前缀。service='all' 含所有源 + serve-mcp 端点日志。"""
    sources = log_sources()
    want = (
        list(sources) + ["serve-mcp"] + list(_JOURNAL_SERVICES) if service == "all" else [service]
    )

    for name in want:
        if name in sources:
            _print_block(name, sources[name], tail)
        elif name == "serve-mcp":
            d = serve_log_dir()
            logs = sorted(d.glob("*.log")) if d.is_dir() else []
            if not logs:
                print(f"===== [serve-mcp] {d} =====")
                print("  (无端点日志)")
                print()
            for lf in logs:
                _print_block(f"serve-mcp:{lf.stem}", lf, tail)
        elif name in _JOURNAL_SERVICES:
            unit = _JOURNAL_SERVICES[name]
            print(f"===== [{name}] systemd journal =====")
            print(f"  {name} 走 journal, 看: journalctl -u {unit} -n {tail} --no-pager")
            print()
        else:
            print(f"logs: 未知 service '{name}'")
            return 2
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    return run_logs(args.service, args.tail)


def register(subparsers) -> None:
    lp = subparsers.add_parser(
        "logs", help="集中查看平台侧日志末 N 行 (MCP server / serve-mcp / audit)"
    )
    lp.add_argument(
        "--service",
        default="all",
        choices=["chroma", "codegraph", "serve-mcp", "webhook", "reindex", "audit", "all"],
        help="看哪个源 (默认 all)",
    )
    lp.add_argument("--tail", type=int, default=50, help="末 N 行 (默认 50)")
    lp.set_defaults(func=cmd_logs)
