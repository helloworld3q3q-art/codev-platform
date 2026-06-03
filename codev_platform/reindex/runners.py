"""reindex 执行层 —— 每索引类型一个 runner (协议 + registry)。

加一种索引类型 = 写一个 ReindexRunner + register() 一行, worker / queue 零改
(同 .claude/rules/agent-provider-architecture.md 的策略接口 + registry 铁律, 零 if-else)。

runner 只管"某 project 这一类索引怎么跑", 不碰队列 / 编排。三个内置 runner 都**委托给既有
`codev-platform reindex --<flag>` CLI** (ops/reindex.py 是 reindex 命令的单一真值源), 本层
只做 kind → flag 映射 + 可扩展挂点, 不复制 reindex 逻辑。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ReindexRunner(Protocol):
    kind: str

    def run(self, project_id: str, repo: Path, cfg: dict) -> int:
        """对该 project 跑这一类索引。返回进程退出码 (0=ok)。"""
        ...


_REGISTRY: dict[str, "ReindexRunner"] = {}


def register(runner: "ReindexRunner") -> None:
    _REGISTRY[runner.kind] = runner


def get_runner(kind: str) -> "ReindexRunner | None":
    return _REGISTRY.get(kind)


def kinds() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def _venv_python(cfg: dict) -> str:
    from codev_platform.mcp_serve import _venv_python as _vp
    return str(_vp(cfg))


# 单 reindex 子进程的墙钟上限 (deep-audit-2026-06-03 P2#4): worker 串行消费,
# 任一 runner 无界挂死 (外部 CLI / 模型加载 / SQLite 锁 / 网络 / git 卡住) 会堵住
# 整个队列。config 驱动: reindex.runner_timeout_sec; <=0 = 显式禁用 (无界, 老行为)。
_DEFAULT_RUNNER_TIMEOUT_SEC = 1800  # 30min 上限 (大仓 chroma embedding 留足余量)
_TIMEOUT_RC = 124  # 与 GNU timeout 约定一致; worker 视 rc!=0 且 !=2 → 丢弃防死循环


def _runner_timeout(cfg: dict) -> float | None:
    """从 config 解析 runner 墙钟上限 (秒)。非法值回默认; <=0 返回 None = 禁用。"""
    from codev_platform.core.config import get as _cfg_get
    raw = _cfg_get(cfg or {}, "reindex.runner_timeout_sec", _DEFAULT_RUNNER_TIMEOUT_SEC)
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return float(_DEFAULT_RUNNER_TIMEOUT_SEC)
    return t if t > 0 else None


class CliReindexRunner:
    """通用 runner: 委托 `python -m codev_platform.cli reindex <flag> --repo <repo>`。

    复用 ops/reindex.py 已测的 per-stage 逻辑 (codegraph sync / chroma indexer / cross_link
    build) —— "怎么 reindex" 只此一处, 本 runner 不重复。
    """

    def __init__(self, kind: str, flag: str) -> None:
        self.kind = kind
        self._flag = flag

    def run(self, project_id: str, repo: Path, cfg: dict) -> int:
        py = _venv_python(cfg)
        cmd = [py, "-m", "codev_platform.cli", "reindex", self._flag, "--repo", str(repo)]
        timeout = _runner_timeout(cfg)
        try:
            return subprocess.run(cmd, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            # 超时 → 子进程已被 kill; 返回 rc=124 让 worker 丢弃该 job, 避免挂死任务
            # 永久阻塞串行队列队头 (挂过一次大概率再挂, 不重试)。
            print(
                f"[reindex:{self.kind}] timeout {timeout}s killed "
                f"(project={project_id}, repo={repo}) -> rc={_TIMEOUT_RC}",
                file=sys.stderr,
            )
            return _TIMEOUT_RC


class CodegraphReindexRunner(CliReindexRunner):
    """codegraph 专用 runner: sync 前先幂等 ensure .codegraph junction 指向平台。

    免手动 `codegraph link --all` —— 首次对某 project reindex 时自动建联接, 让 sync
    写穿 junction 落平台。ensure-link 是 fail-soft 的(见 ops.codegraph.ensure_codegraph_linked),
    失败只记录不中断 sync。
    """

    def __init__(self) -> None:
        super().__init__("codegraph", "--codegraph")

    def run(self, project_id: str, repo: Path, cfg: dict) -> int:
        from codev_platform.ops.codegraph import ensure_codegraph_linked
        r = ensure_codegraph_linked(project_id, repo, cfg)
        if r.get("action") == "error":
            print(f"[reindex:codegraph] ensure-link 失败 (fail-soft, 继续 sync): {r.get('note')}",
                  file=sys.stderr)
        return super().run(project_id, repo, cfg)


# 内置四类 (与 ops/reindex.py 的 --chroma / --codegraph / --cross-link / --ingest 对齐)
register(CliReindexRunner("chroma", "--chroma"))
register(CodegraphReindexRunner())
register(CliReindexRunner("cross_link", "--cross-link"))
# 统一图谱 ingest: 跑 analyzer 插件 -> graph store。委托 reindex --ingest (失败隔离在
# ops/reindex.py 内: 插件层异常只 warn 不改退出码, 不拖垮基线索引)。
register(CliReindexRunner("ingest", "--ingest"))
