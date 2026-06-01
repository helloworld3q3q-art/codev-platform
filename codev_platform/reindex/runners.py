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
        return subprocess.run(cmd).returncode


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


# 内置三类 (与 ops/reindex.py 的 --chroma / --codegraph / --cross-link 对齐)
register(CliReindexRunner("chroma", "--chroma"))
register(CodegraphReindexRunner())
register(CliReindexRunner("cross_link", "--cross-link"))
