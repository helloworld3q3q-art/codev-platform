"""reindex 执行层 —— 每索引类型一个 runner (协议 + registry)。

加一种索引类型 = 写一个 ReindexRunner + register() 一行, worker / queue 零改
(同 .claude/rules/agent-provider-architecture.md 的策略接口 + registry 铁律, 零 if-else)。

runner 只管"某 project 这一类索引怎么跑", 不碰队列 / 编排。三个内置 runner 都**委托给既有
`codev-platform reindex --<flag>` CLI** (ops/reindex.py 是 reindex 命令的单一真值源), 本层
只做 kind → flag 映射 + 可扩展挂点, 不复制 reindex 逻辑。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

from codev_platform.index_kind_contract import REQUIRED_INDEX_KINDS
from codev_platform.reindex import runner_logs
from codev_platform.reindex.runner_proof import RunnerProofScanner


@runtime_checkable
class ReindexRunner(Protocol):
    kind: str

    def run(self, project_id: str, repo: Path, cfg: dict) -> int:
        """对该 project 跑这一类索引。返回进程退出码 (0=ok)。"""
        ...


_REGISTRY: dict[str, ReindexRunner] = {}


def register(runner: ReindexRunner) -> None:
    kind = getattr(runner, "kind", None)
    if type(kind) is not str or not kind or not callable(getattr(runner, "run", None)):
        raise ValueError("reindex runner 注册对象无效")
    if kind in _REGISTRY:
        raise ValueError(f"reindex runner 重复注册：{kind}")
    _REGISTRY[kind] = runner


def get_runner(kind: str) -> ReindexRunner | None:
    return _REGISTRY.get(kind)


def kinds() -> tuple[str, ...]:
    return tuple(_REGISTRY)


_RUNTIME_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _platform_runtime_python() -> str:
    """legacy runner 使用当前平台解释器，不能误继承 Chroma 专用 venv。"""
    from codev_platform.mcp_serve import _platform_runtime_python as current_python

    return str(current_python())


# 单 reindex 子进程的墙钟上限 (deep-audit-2026-06-03 P2#4): worker 串行消费,
# 任一 runner 无界挂死 (外部 CLI / 模型加载 / SQLite 锁 / 网络 / git 卡住) 会堵住
# 整个队列。config 驱动: reindex.runner_timeout_sec；无效或非正值回退安全默认值。
_DEFAULT_RUNNER_TIMEOUT_SEC = 1800  # 30min 上限 (大仓 chroma embedding 留足余量)
_CHROMA_CPU_RUNNER_TIMEOUT_SEC = 7200  # CPU 文档全量嵌入专用受控时限: 2h
_CODE_VEC_CPU_RUNNER_TIMEOUT_SEC = 10800  # CPU Qwen 全量 code_vec 的专用受控时限: 3h
_TIMEOUT_RC = 124  # 与 GNU timeout 约定一致; worker 视 rc!=0 且 !=2 → 丢弃防死循环
_FAILURE_TAIL_BYTES = 8000
_FAILURE_NOTE_CHARS = 1200
_ATTEMPT_LOG_KEY = object()
_ATTEMPT_RUNTIME_KEY = object()
_ATTEMPT_INTERPRETER_KEY = object()
_ATTEMPT_TARGET_KEY = object()


def manifest_covered_kinds() -> tuple[str, ...]:
    """兼容旧调用方的生产必需索引视图。"""
    return REQUIRED_INDEX_KINDS


def _runner_timeout(
    cfg: dict,
    *,
    fallback: float = _DEFAULT_RUNNER_TIMEOUT_SEC,
) -> float:
    """解析 runner 墙钟上限；任何无效或无界配置都回退安全默认值。"""
    from codev_platform.core.config import get as _cfg_get

    raw = _cfg_get(cfg or {}, "reindex.runner_timeout_sec", fallback)
    if type(raw) not in (int, float, str):
        return float(fallback)
    try:
        t = float(raw)
    except (TypeError, ValueError):
        return float(fallback)
    valid = math.isfinite(t) and 0 < t <= runner_logs.MAX_RUNNER_TIMEOUT_SEC
    return t if valid else float(fallback)


def _tail_output(log_path: Path, limit: int = _FAILURE_TAIL_BYTES) -> str:
    if not log_path.exists():
        return ""
    with log_path.open("rb") as log_file:
        log_file.seek(0, 2)
        size = log_file.tell()
        log_file.seek(max(0, size - limit))
        return log_file.read().decode("utf-8", errors="replace").strip()


def _failure_note(kind: str, rc: int, timeout: float | None, output: str) -> str:
    head = f"{kind} rc={rc}"
    if timeout is not None:
        head += f" timeout={timeout}s"
    if not output:
        return head
    safe_output = runner_logs.redact_runner_output(output)
    text = "\n".join(line.rstrip() for line in safe_output.splitlines() if line.strip())
    if len(text) > _FAILURE_NOTE_CHARS:
        text = "..." + text[-_FAILURE_NOTE_CHARS:]
    return f"{head}\n{text}"


def bind_attempt_context(
    cfg: dict,
    attempt_id: str,
    *,
    runtime_revision: str,
    interpreter: str | Path,
    target_commit: str | None = None,
) -> None:
    """把 executor 已证明的日志、版本和稳定 venv 解释器绑定到同一 cfg。"""
    if (
        type(cfg) is not dict
        or type(attempt_id) is not str
        or not attempt_id.strip()
        or type(runtime_revision) is not str
        or _RUNTIME_REVISION_RE.fullmatch(runtime_revision) is None
        or set(runtime_revision) == {"0"}
    ):
        raise ValueError("attempt 运行上下文无效")
    declared = Path(interpreter)
    try:
        logical = declared.parent.resolve(strict=True) / declared.name
    except OSError:
        raise ValueError("attempt 解释器不可用") from None
    if not declared.is_absolute() or declared != logical or not logical.is_file():
        raise ValueError("attempt 解释器必须是规范绝对文件")
    cfg[_ATTEMPT_LOG_KEY] = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()[:16]
    cfg[_ATTEMPT_RUNTIME_KEY] = runtime_revision
    cfg[_ATTEMPT_INTERPRETER_KEY] = str(logical)
    if target_commit is not None:
        if _RUNTIME_REVISION_RE.fullmatch(target_commit) is None or set(target_commit) == {"0"}:
            raise ValueError("attempt 目标提交无效")
        cfg[_ATTEMPT_TARGET_KEY] = target_commit


def _proven_context(cfg: dict) -> tuple[str, str]:
    revision = cfg.get(_ATTEMPT_RUNTIME_KEY)
    interpreter = cfg.get(_ATTEMPT_INTERPRETER_KEY)
    if (
        type(revision) is not str
        or _RUNTIME_REVISION_RE.fullmatch(revision) is None
        or set(revision) == {"0"}
        or type(interpreter) is not str
    ):
        raise ValueError("attempt 已证明运行上下文缺失")
    path = Path(interpreter)
    try:
        logical = path.parent.resolve(strict=True) / path.name
    except OSError:
        raise ValueError("attempt 已证明解释器不可用") from None
    if path != logical or not logical.is_file():
        raise ValueError("attempt 已证明解释器发生漂移")
    return revision, str(logical)


def _runner_log_path(project_id: str, kind: str, cfg: dict) -> Path:
    from codev_platform.core.paths import logs_dir
    from codev_platform.core.runtime_artifact_io import prepare_runtime_artifact_directory

    safe_project = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in project_id)
    safe_kind = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in kind)
    attempt_suffix = cfg.get(_ATTEMPT_LOG_KEY)
    suffix = f"__{attempt_suffix}" if type(attempt_suffix) is str else ""
    root = prepare_runtime_artifact_directory(logs_dir() / "reindex-runner")
    return root / f"{safe_project}__{safe_kind}{suffix}.log"


class CliReindexRunner:
    """通用 runner: 委托 `python -I -m codev_platform.cli reindex <flag> --repo <repo>`。

    复用 ops/reindex.py 已测的 per-stage 逻辑 (codegraph sync / chroma indexer / graph
    build) —— "怎么 reindex" 只此一处, 本 runner 不重复。
    """

    def __init__(self, kind: str, flag) -> None:
        self.kind = kind
        self.last_note = ""
        self.last_log_ref: str | None = None
        self._flags = [flag] if isinstance(flag, str) else list(flag)

    def runner_timeout(self, cfg: dict) -> float:
        """子类可按索引特性覆写时限，不得改动 attempt 的配置快照。"""
        return _runner_timeout(cfg)

    def run(self, project_id: str, repo: Path, cfg: dict) -> int:
        self.last_note = ""
        self.last_log_ref = None
        try:
            from codev_platform.core.repos import runtime_repo_override_environment

            override_env = runtime_repo_override_environment(cfg, project_id)
        except ValueError as exc:
            self.last_note = _failure_note(self.kind, 1, None, str(exc))
            print(f"[reindex:{self.kind}] {self.last_note}", file=sys.stderr)
            return 1
        isolated = bool(override_env)
        try:
            runtime_revision, py = (
                _proven_context(cfg) if isolated else ("", _platform_runtime_python())
            )
        except ValueError as exc:
            self.last_note = _failure_note(self.kind, 1, None, str(exc))
            print(f"[reindex:{self.kind}] {self.last_note}", file=sys.stderr)
            return 1
        cmd = [py, "-I", "-m", "codev_platform.cli", "reindex", *self._flags]
        if isolated:
            cmd.extend(
                (
                    "--proven-runtime",
                    "--expected-project-id",
                    project_id,
                    "--expected-runtime-revision",
                    runtime_revision,
                )
            )
        cmd.extend(("--repo", str(repo)))
        timeout = self.runner_timeout(cfg)
        log_path = _runner_log_path(project_id, self.kind, cfg)
        self.last_log_ref = str(log_path)
        child_env = os.environ.copy()
        child_env.update(override_env)
        if isolated:
            from codev_platform.core.config import reindex_config_snapshot_environment
            from codev_platform.core.repo_input_guard import PROVEN_REINDEX_INPUT_ENV
            from codev_platform.core.repo_input_guard import REINDEX_TARGET_COMMIT_ENV
            from codev_platform.core.runtime_interpreter import REINDEX_RUNTIME_REVISION_ENV

            child_env.update(reindex_config_snapshot_environment(cfg))
            child_env["PLATFORM_PROJECT_ID"] = project_id
            child_env[PROVEN_REINDEX_INPUT_ENV] = "1"
            child_env[REINDEX_RUNTIME_REVISION_ENV] = runtime_revision
            target_commit = cfg.get(_ATTEMPT_TARGET_KEY)
            if type(target_commit) is str:
                child_env[REINDEX_TARGET_COMMIT_ENV] = target_commit
        proof_scanner = RunnerProofScanner(self.kind)
        try:
            rc = runner_logs.run_logged_process(
                cmd,
                timeout=timeout,
                log_path=log_path,
                env=child_env,
                output_observer=proof_scanner.feed_bytes,
            )
        except subprocess.TimeoutExpired:
            # 超时 → 子进程已被 kill; 返回 rc=124 让 worker 丢弃该 job, 避免挂死任务
            # 永久阻塞串行队列队头 (挂过一次大概率再挂, 不重试)。
            self.last_note = _failure_note(self.kind, _TIMEOUT_RC, timeout, _tail_output(log_path))
            print(f"[reindex:{self.kind}] {self.last_note}\nlog={log_path}", file=sys.stderr)
            return _TIMEOUT_RC
        if rc != 0:
            self.last_note = _failure_note(self.kind, rc, None, _tail_output(log_path))
            print(f"[reindex:{self.kind}] {self.last_note}\nlog={log_path}", file=sys.stderr)
            return rc
        proof_failure = proof_scanner.finish(log_path)
        if proof_failure:
            self.last_note = _failure_note(self.kind, 1, None, proof_failure)
            print(f"[reindex:{self.kind}] {self.last_note}\nlog={log_path}", file=sys.stderr)
            return 1
        return rc


class CodegraphReindexRunner(CliReindexRunner):
    """codegraph 专用 runner: sync 前先幂等 ensure .codegraph junction 指向平台。

    免手动 `codegraph link --all` —— 首次对某 project reindex 时自动建联接, 让 sync
    写穿 junction 落平台。ensure-link 结果仍由 ops.codegraph.ensure_codegraph_linked
    提供; action=error 会被提升为 runner 失败,避免后续 sync 假绿。
    """

    def __init__(self) -> None:
        super().__init__("codegraph", "--codegraph")

    def run(self, project_id: str, repo: Path, cfg: dict) -> int:
        self.last_note = ""
        self.last_log_ref = None
        from codev_platform.ops.codegraph import ensure_codegraph_linked

        r = ensure_codegraph_linked(project_id, repo, cfg)
        if r.get("action") == "error":
            note = f"codegraph proof failed: ensure-link failed: {r.get('note') or ''}".strip()
            self.last_note = _failure_note(self.kind, 1, None, note)
            print(f"[reindex:codegraph] {self.last_note}", file=sys.stderr)
            return 1
        return super().run(project_id, repo, cfg)


def _chroma_embedding_runs_on_cpu(cfg: dict) -> bool:
    """统一解析嵌入设备；环境变量优先，普通 CLI 则回退项目配置。"""
    from codev_platform.core.config import get as _cfg_get

    device = os.environ.get("PLATFORM_EMBED_DEVICE")
    if device is None:
        device = _cfg_get(cfg, "models.embed_device", "")
    return str(device).strip().lower() == "cpu"


def _scoped_runner_timeout(cfg: dict, path: str, fallback: float) -> float:
    """解析 kind 专用时限；非法值回退该 kind 的上限，而不是通用 30 分钟。"""
    from codev_platform.core.config import get as _cfg_get

    raw = _cfg_get(cfg, path, fallback)
    return _runner_timeout(
        {"reindex": {"runner_timeout_sec": raw}},
        fallback=fallback,
    )


class ChromaReindexRunner(CliReindexRunner):
    """文档向量 runner：CPU 全量编码使用独立的有界时限。"""

    def __init__(self) -> None:
        super().__init__("chroma", "--chroma")

    def runner_timeout(self, cfg: dict) -> float:
        if _chroma_embedding_runs_on_cpu(cfg):
            return _scoped_runner_timeout(
                cfg,
                "reindex.chroma_runner_timeout_sec",
                _CHROMA_CPU_RUNNER_TIMEOUT_SEC,
            )
        return super().runner_timeout(cfg)


class CodeVecReindexRunner(CliReindexRunner):
    """代码向量 runner：仅在严格 checkpoint 确实推进时交回队列续跑。"""

    def __init__(self) -> None:
        super().__init__("code_vec", "--code-vec")

    def runner_timeout(self, cfg: dict) -> float:
        from codev_platform.agent.embed.registry import code_vec_embedding_device

        if code_vec_embedding_device(cfg) == "cpu":
            return _scoped_runner_timeout(
                cfg,
                "recall.code_vec.runner_timeout_sec",
                _CODE_VEC_CPU_RUNNER_TIMEOUT_SEC,
            )
        return super().runner_timeout(cfg)

    def run(self, project_id: str, repo: Path, cfg: dict) -> int:
        from codev_platform.recall.code_vector_store import (
            code_vec_checkpoint_progress,
            code_vec_current_manifest_exists,
        )

        current_manifest_before = code_vec_current_manifest_exists(project_id)
        before = code_vec_checkpoint_progress(project_id)
        rc = super().run(project_id, repo, cfg)
        if rc == 0:
            return rc
        after = code_vec_checkpoint_progress(project_id)
        advanced = any(
            entries > before.get(fingerprint, 0) for fingerprint, entries in after.items()
        )
        current_manifest_revoked = (
            current_manifest_before and not code_vec_current_manifest_exists(project_id)
        )
        if not advanced and not current_manifest_revoked:
            return rc
        reason = (
            "code_vec current manifest 已撤销，交回队列转隔离 side-build"
            if current_manifest_revoked
            else "code_vec 本次 checkpoint 已推进，非零退出交回队列续跑"
        )
        self.last_note = f"{self.last_note}\n{reason}"
        return 2


# 内置四类 (与 ops/reindex.py 的 --chroma / --codegraph / --ingest / --code-vec 对齐)
register(ChromaReindexRunner())
register(CodegraphReindexRunner())
# 统一图谱 ingest: 跑 analyzer 插件 -> graph store。委托 reindex --ingest (失败隔离在
# ops/reindex.py 内: 插件层异常只 warn 不改退出码, 不拖垮基线索引)。
register(CliReindexRunner("ingest", "--ingest"))
# 代码向量索引 (vector lane): 只跑向量。依赖新鲜 codegraph.db 的安全性由 worker 依赖调度保证。
register(CodeVecReindexRunner())
