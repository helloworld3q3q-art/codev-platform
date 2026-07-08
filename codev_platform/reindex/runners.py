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


_REGISTRY: dict[str, ReindexRunner] = {}


def register(runner: ReindexRunner) -> None:
    _REGISTRY[runner.kind] = runner


def get_runner(kind: str) -> ReindexRunner | None:
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
_FAILURE_TAIL_BYTES = 8000
_FAILURE_NOTE_CHARS = 1200


def _runner_timeout(cfg: dict) -> float | None:
    """从 config 解析 runner 墙钟上限 (秒)。非法值回默认; <=0 返回 None = 禁用。"""
    from codev_platform.core.config import get as _cfg_get
    raw = _cfg_get(cfg or {}, "reindex.runner_timeout_sec", _DEFAULT_RUNNER_TIMEOUT_SEC)
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return float(_DEFAULT_RUNNER_TIMEOUT_SEC)
    return t if t > 0 else None


def _tail_output(log_file, limit: int = _FAILURE_TAIL_BYTES) -> str:
    log_file.flush()
    log_file.seek(0, 2)
    size = log_file.tell()
    log_file.seek(max(0, size - limit))
    return log_file.read().strip()


def _failure_note(kind: str, rc: int, timeout: float | None, output: str) -> str:
    head = f"{kind} rc={rc}"
    if timeout is not None:
        head += f" timeout={timeout}s"
    if not output:
        return head
    text = "\n".join(line.rstrip() for line in output.splitlines() if line.strip())
    if len(text) > _FAILURE_NOTE_CHARS:
        text = "..." + text[-_FAILURE_NOTE_CHARS:]
    return f"{head}\n{text}"


def _runner_log_path(project_id: str, kind: str) -> Path:
    from codev_platform.core.paths import logs_dir
    safe_project = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in project_id)
    safe_kind = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in kind)
    root = logs_dir() / "reindex-runner"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{safe_project}__{safe_kind}.log"


class CliReindexRunner:
    """通用 runner: 委托 `python -m codev_platform.cli reindex <flag> --repo <repo>`。

    复用 ops/reindex.py 已测的 per-stage 逻辑 (codegraph sync / chroma indexer / graph
    build) —— "怎么 reindex" 只此一处, 本 runner 不重复。
    """

    def __init__(self, kind: str, flag) -> None:
        self.kind = kind
        self.last_note = ""
        # flag 可为单个 str 或多个(list): code_vec 用 ['--codegraph','--code-vec'] 让同一子进程先
        # sync codegraph 再建向量, 保证读新鲜 codegraph.db(纵深, 不靠跨 job 排序)+ 使 R4 锁忙逻辑生效。
        self._flags = [flag] if isinstance(flag, str) else list(flag)

    def run(self, project_id: str, repo: Path, cfg: dict) -> int:
        self.last_note = ""
        py = _venv_python(cfg)
        cmd = [py, "-m", "codev_platform.cli", "reindex", *self._flags, "--repo", str(repo)]
        timeout = _runner_timeout(cfg)
        log_path = _runner_log_path(project_id, self.kind)
        with log_path.open("w+", encoding="utf-8", errors="replace") as log_file:
            try:
                rc = subprocess.run(
                    cmd,
                    timeout=timeout,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                ).returncode
            except subprocess.TimeoutExpired:
                # 超时 → 子进程已被 kill; 返回 rc=124 让 worker 丢弃该 job, 避免挂死任务
                # 永久阻塞串行队列队头 (挂过一次大概率再挂, 不重试)。
                self.last_note = _failure_note(self.kind, _TIMEOUT_RC, timeout, _tail_output(log_file))
                print(f"[reindex:{self.kind}] {self.last_note}\nlog={log_path}", file=sys.stderr)
                return _TIMEOUT_RC
            if rc != 0:
                self.last_note = _failure_note(self.kind, rc, None, _tail_output(log_file))
                print(f"[reindex:{self.kind}] {self.last_note}\nlog={log_path}", file=sys.stderr)
            return rc


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


# 内置四类 (与 ops/reindex.py 的 --chroma / --codegraph / --ingest / --code-vec 对齐)
register(CliReindexRunner("chroma", "--chroma"))
register(CodegraphReindexRunner())
# 统一图谱 ingest: 跑 analyzer 插件 -> graph store。委托 reindex --ingest (失败隔离在
# ops/reindex.py 内: 插件层异常只 warn 不改退出码, 不拖垮基线索引)。
register(CliReindexRunner("ingest", "--ingest"))
# 代码向量索引 (vector lane): 增量重嵌变更节点。**依赖 codegraph.db 新鲜** —— runner 用
# ['--codegraph','--code-vec'] 让同一子进程**先 sync codegraph 再建向量**, 故无论入队排序如何、
# 即使 codegraph 是独立 job, code_vec 都读到刚同步的新鲜 db; 且 codegraph sync 锁忙(rc=2)时
# commands 的 R4 逻辑(同进程 do_codegraph+do_codevec)跳过 code_vec + rc=2 让 worker 重试。
# codegraph sync 增量近 noop, 双跑成本低。
register(CliReindexRunner("code_vec", ["--codegraph", "--code-vec"]))
