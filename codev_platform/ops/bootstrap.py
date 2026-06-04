"""`codev-platform bootstrap [--dry-run]` —— 换机器 / 重 clone 后按 config 一键拉起平台。

D15: 把"换机后要手动跑的几条命令"压成一个有序、幂等、fail-soft 的编排入口。

纯逻辑 / 副作用分离 (便于单测, 不在 import / plan 期触发重活):
- plan_bootstrap(cfg) -> list[BootstrapStep]   纯函数, 只读 cfg + 已知事实, 不起进程 / 不建库。
- run_bootstrap(cfg, dry_run)                   dry_run 只打印; 否则逐步执行 (fail-soft + 末尾汇总)。

顺序 (按 config 条件产出):
  ① check  venv      —— .venv 存在且能 import codev_platform (缺 → guide, 绝不自动 pip)。
  ② cmd    serve-mcp —— 拉起 4 端点 (chroma / codegraph / agent-memory / graph)。
  ③ cmd    codegraph link --all —— 仅当 config.projects 非空。
  ④ cmd    memory init-db        —— 仅当 config.memory.pg_dsn 非空; 为空则 guide (可选)。

调子命令一律走 [sys.executable, -m, codev_platform.cli, <subcmd>...] 子进程
(不依赖 console script 在 PATH; 复用已落地子命令, 不重造编排)。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


@dataclass
class BootstrapStep:
    name: str                       # 短标识 (venv / serve-mcp / codegraph-link / memory-init-db)
    kind: str                       # "check" | "cmd" | "guide"
    detail: str                     # 人读说明 (dry-run / 执行时打印)
    cmd: list[str] | None = None    # kind=="cmd" 时要执行的命令; check/guide 为 None


def plan_bootstrap(cfg) -> list[BootstrapStep]:
    """Pure: 从 config 推导有序 bootstrap 步骤 (无 IO 副作用, 不探测文件)。

    venv 是否真存在 / 能否 import 留给 check 步在执行期判定 —— plan 纯函数只读 cfg +
    已知事实 (py 解释器路径), 不把"文件探测结果"当 plan 的硬条件。
    """
    from codev_platform.core.config import get

    py = sys.executable
    steps: list[BootstrapStep] = []

    # ① venv check —— 恒在且在最前
    steps.append(BootstrapStep(
        name="venv",
        kind="check",
        detail="检查 .venv 存在且能 import codev_platform (缺 → 见 CLAUDE.md 用 "
               "requirements-runtime.txt 重建; 绝不自动 pip)",
    ))

    # ② serve-mcp start —— 恒在 (拉起 4 端点)
    steps.append(BootstrapStep(
        name="serve-mcp",
        kind="cmd",
        detail="serve-mcp start: 拉起平台 4 个 MCP 端点 (chroma 预热 ~30-60s)",
        cmd=[py, "-m", "codev_platform.cli", "serve-mcp", "start"],
    ))

    # ③ codegraph link --all —— 仅当有已登记项目 (否则无可联接)
    projects = get(cfg, "projects") or {}
    if projects:
        steps.append(BootstrapStep(
            name="codegraph-link",
            kind="cmd",
            detail=f"codegraph link --all: 为 {len(projects)} 个项目重建 junction 联接 (幂等)",
            cmd=[py, "-m", "codev_platform.cli", "codegraph", "link", "--all"],
        ))

    # ④ memory init-db —— 仅当 memory.pg_dsn 配了 (空则 guide, 可选)
    dsn = get(cfg, "memory.pg_dsn")
    if dsn and str(dsn).strip():
        steps.append(BootstrapStep(
            name="memory-init-db",
            kind="cmd",
            detail="memory init-db: 幂等建 memory PG schema 表 (不建 database)",
            cmd=[py, "-m", "codev_platform.cli", "memory", "init-db"],
        ))
    else:
        steps.append(BootstrapStep(
            name="memory-init-db",
            kind="guide",
            detail="memory.pg_dsn 未配 —— memory PG 可选, 不启用则跳过 (建库见 P4 runbook)",
        ))

    return steps


def _check_venv() -> tuple[bool, str]:
    """check 步: 进程内判定当前解释器能否 import codev_platform。

    bootstrap 本身就是用平台 venv 的 cli 跑的, 所以 import 成功 = venv 可用。
    """
    venv_dir = Path(__file__).resolve().parent.parent.parent / ".venv"
    try:
        import codev_platform  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, f"无法 import codev_platform: {type(exc).__name__}: {exc}"
    if not venv_dir.exists():
        return True, f"已能 import codev_platform (但仓根 .venv 不存在: {venv_dir})"
    return True, f"codev_platform 可用; .venv 存在 ({venv_dir})"


def run_bootstrap(cfg, dry_run: bool) -> int:
    plan = plan_bootstrap(cfg)

    if dry_run:
        _out("[dry-run] bootstrap 步骤 (按序):")
        for i, st in enumerate(plan, 1):
            _out(f"  {i}. [{st.kind}] {st.name} — {st.detail}")
            if st.kind == "cmd" and st.cmd:
                _out(f"       将执行: {' '.join(st.cmd)}")
        return 0

    failures: list[str] = []
    for i, st in enumerate(plan, 1):
        _out(f"[{i}/{len(plan)}] {st.name} ({st.kind}) — {st.detail}")
        if st.kind == "check":
            ok, msg = _check_venv()
            _out(("  OK: " if ok else "  缺失: ") + msg)
            if not ok:
                failures.append(f"{st.name} (check 未过, 但不阻断后续)")
        elif st.kind == "cmd" and st.cmd:
            proc = subprocess.run(st.cmd, check=False)  # fail-soft: 失败不中断后续
            if proc.returncode != 0:
                _err(f"  FAIL: {st.name} rc={proc.returncode} (继续后续步骤)")
                failures.append(f"{st.name} (rc={proc.returncode})")
            else:
                _out(f"  OK: {st.name}")
        else:  # guide
            _out(f"  跳过 (可选): {st.detail}")

    _out("")
    if failures:
        _err(f"bootstrap 完成, 但有 {len(failures)} 步未通过:")
        for f in failures:
            _err(f"  - {f}")
        return 1
    _out("OK: bootstrap 全部步骤通过。")
    return 0


def load_config():
    """Thin indirection over core.config.load_config (lazy import, test-patchable)。"""
    from codev_platform.core.config import load_config as _lc

    return _lc()


def cmd_bootstrap(args: argparse.Namespace) -> int:
    cfg = load_config()
    return run_bootstrap(cfg, args.dry_run)


def register(subparsers) -> None:
    bp = subparsers.add_parser(
        "bootstrap",
        help="换机器 / 重 clone 后按 config 顺序拉起平台 (venv 体检 + serve-mcp + codegraph link + memory)",
    )
    bp.add_argument("--dry-run", action="store_true", help="只打印有序步骤不执行")
    bp.set_defaults(func=cmd_bootstrap)
