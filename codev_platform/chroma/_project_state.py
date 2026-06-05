"""Per-project 多租户运行态机: collection + bm25 加载 / 重载戳 (从 server.py 抽出, file-discipline §1)。

daemon 单进程服务多 project_id, 每 project 独立 _ProjectState。_ensure_project 是入口 (模型 + 该
project collection 就绪才返 state, 否则 None); 构建戳 mtime 变新 → evict 让下次 ensure 重建
(per-project stamp 隔离, 单项目重建不误伤其它)。

无环分层 (§A.2b): 只依赖叶子 (_config 常量 / _models 模型&client / _obslog 日志 / bm25 / core.paths),
**不 import server**。server.py re-export 本模块符号, 保 `from chroma.server import _ensure_project`
等兼容 (tests / _tools)。_projects 是原地 mutate 的 dict, 跨模块 from import 共享同一份安全。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from codev_platform.chroma._config import (
    BM25_ENABLED,
    COLLECTION_BASE,
    LEGACY_COLLECTION_NAME,
    PROJECT_ID,
    _BM25_IMPORT_OK,
    _STAMP_PATH,
)
from codev_platform.chroma._models import _ensure_model, _get_client
from codev_platform.chroma._obslog import _flog
from codev_platform.core.paths import chroma_collection_name

# BM25Index: jieba+rank_bm25 缺失时不可用, 实际用前由 _BM25_IMPORT_OK 守护 (同 server.py 原处理)。
try:
    from codev_platform.chroma.bm25 import BM25Index
except ImportError:
    pass


@dataclass
class _ProjectState:
    """Per-project runtime state for multi-tenant daemon."""
    project_id: str
    collection: Any = None  # chroma Collection
    bm25_index: BM25Index | None = None
    last_stamp_mtime: float = 0.0
    init_error: str | None = None
    active_collection_name: str | None = None  # 实际使用的 collection 名 (prefixed 或 legacy)
    last_request_at: float | None = None  # tool 调用时间戳 (epoch sec), widget 三态点用


# project_id -> state. 用 dict, 不上锁 — asyncio 单线程, dict 操作原子。
_projects: dict[str, _ProjectState] = {}


def _project_last_indexed_iso(project_id: str) -> str | None:
    """读 chroma per-project .last_build.<pid>.json mtime, 转 ISO8601 字符串。

    优先 per-project stamp (indexer 2026-05-28 起写入),
    回退全局 .last_build.json (老索引未升级时,仅当 project_id == daemon 启动默认时有效)。
    """
    from datetime import datetime, timezone
    # per-project stamp (新, 推荐)
    pp = _STAMP_PATH.parent / f".last_build.{project_id}.json"
    if pp.exists():
        try:
            return datetime.fromtimestamp(pp.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
        except Exception as exc:  # noqa: BLE001 — stamp mtime 读失败: 不致命, 但记一笔便于排查
            _flog(f"[last_indexed] project={project_id} per-project stamp mtime 读失败: {exc!s}")
    # fallback: 全局 stamp 仅对启动默认 project 准确, 其它返 None (避免误导)
    if project_id != PROJECT_ID:
        return None
    if not _STAMP_PATH.exists():
        return None
    try:
        return datetime.fromtimestamp(_STAMP_PATH.stat().st_mtime, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001 — 全局 stamp mtime 读失败: 同上
        _flog(f"[last_indexed] project={project_id} global stamp mtime 读失败: {exc!s}")
        return None


def _load_project_state(project_id: str, reason: str) -> _ProjectState:
    """加载 project_id 对应的 collection + bm25, 失败抛 (调用方包 try)。

    backward compat: 若 project_id == 启动默认 PROJECT_ID 且 prefixed collection 不存在,
    fallback 到 legacy 'platform_docs'。其它 project_id 缺 prefixed collection 直接 raise。
    """
    client = _get_client()
    prefixed_name = chroma_collection_name(project_id, COLLECTION_BASE)
    col = None
    active_name = None
    try:
        col = client.get_collection(prefixed_name)
        active_name = prefixed_name
    except Exception as exc:  # noqa: BLE001
        if project_id == PROJECT_ID:
            # legacy fallback 仅对 daemon 启动默认 project 生效
            try:
                col = client.get_collection(LEGACY_COLLECTION_NAME)
                active_name = LEGACY_COLLECTION_NAME
                _flog(
                    f"[{reason}] WARN: prefixed '{prefixed_name}' 不存在, "
                    f"fallback legacy '{LEGACY_COLLECTION_NAME}' (project_id={project_id})。"
                    f"重跑 index_docs.py 后会写入新命名 collection。"
                )
            except Exception:
                raise exc from None
        else:
            raise

    cmeta = col.metadata or {}
    _flog(
        f"[{reason}] collection '{active_name}' loaded (project_id={project_id}), "
        f"chunks={col.count()} "
        f"meta_model={cmeta.get('embed_model_name')} meta_dim={cmeta.get('embedding_dim')}"
    )

    bm25 = None
    if BM25_ENABLED and _BM25_IMPORT_OK:
        try:
            _t0 = time.perf_counter()
            bm25 = BM25Index()
            n = bm25.build(col)
            _flog(
                f"[{reason}] bm25 built for {project_id}: {n} chunks, "
                f"took={(time.perf_counter()-_t0)*1000:.0f}ms"
            )
        except Exception as exc:  # noqa: BLE001
            _flog(f"[{reason}] bm25 build FAILED for {project_id}: {exc!s} (fallback vector-only)")
            bm25 = None

    return _ProjectState(
        project_id=project_id,
        collection=col,
        bm25_index=bm25,
        active_collection_name=active_name,
    )


def _maybe_reload_project(state: _ProjectState) -> bool:
    """探测构建戳, mtime 变新则把该 project 从 _projects 移除让下次 _ensure_project 重建。
    返回 True = 已置失效 (caller 应重新 ensure)。

    优先读 per-project stamp `.last_build.<project_id>.json` (indexer 2026-05-28 起写),
    全局 `.last_build.json` 仅 legacy fallback。这样一个项目重建只 evict 自己,
    不再使其它项目误 reload。"""
    pp = _STAMP_PATH.parent / f".last_build.{state.project_id}.json"
    stamp = pp if pp.exists() else _STAMP_PATH
    if not stamp.exists():
        return False
    try:
        cur = stamp.stat().st_mtime
    except OSError:
        return False
    if cur <= state.last_stamp_mtime:
        return False
    # 首次见戳 → 只记录, 不重建 (state 已经 fresh)
    if state.last_stamp_mtime > 0:
        _flog(
            f"[reload] stamp mtime {state.last_stamp_mtime:.0f} -> {cur:.0f}, "
            f"dropping project {state.project_id} (model kept)"
        )
        _projects.pop(state.project_id, None)
        return True
    state.last_stamp_mtime = cur
    return False


def _ensure_project(project_id: str) -> _ProjectState | None:
    """模型 + 该 project 的 collection 都就绪。失败返回 None。"""
    if _ensure_model() is None:
        return None
    state = _projects.get(project_id)
    if state is not None:
        # 已加载 → 探测重载戳
        if _maybe_reload_project(state):
            state = None  # 已 evict, 走加载分支
    if state is not None:
        return state
    try:
        state = _load_project_state(project_id, "init")
        _projects[project_id] = state
        return state
    except Exception as exc:  # noqa: BLE001
        msg = (
            f"Chroma collection 加载失败 (project_id={project_id}): {exc!s}. "
            f"请先跑 tools/chroma/index_docs.py 索引文档 (PLATFORM_PROJECT_ID={project_id})。"
        )
        _flog(f"[ensure] ERROR: {msg}")
        # 记录到一个临时 state 让后续查询能拿到错误信息
        _projects[project_id] = _ProjectState(project_id=project_id, init_error=msg)
        return None
