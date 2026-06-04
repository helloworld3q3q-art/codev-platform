"""chroma daemon —— MCP call_tool handler (从 server.py 抽出, file-discipline §1)。

search_docs / list_collections / get_by_file 三个 tool 的执行逻辑。逐字搬迁, 零行为变更。

import 约定 (避免循环 + rebinding 陷阱):
- server.py 末尾 `from . import _tools` 触发本模块 @server.call_tool() 注册; 故本模块 import
  的 server 符号 (函数 / 常量 / dict / contextvar) 在那一刻全已定义。
- **rebind 标量** (_global_init_error / _reranker_load_err 由 _ensure_model/_ensure_reranker
  运行时重绑) 必须经 `srv.<name>` 模块限定访问读当前值; top-level `from import` 会捕获 import
  期旧值 (None) 而 stale。其余 (常量 import 期定死 / dict 原地 mutate / 函数) top-level import 安全。
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import Any

from mcp.types import TextContent

import codev_platform.chroma._models as mdl  # model rebind 标量经 mdl.<name> (_global_init_error)
# 注: 不能用别名 `m` —— call_tool 内有局部 `for m in metas` / `m = metas[i]`, 会 UnboundLocalError。
import codev_platform.chroma._reranker as rr  # reranker rebind 标量经 rr.<name> (_reranker_load_err)
from codev_platform.chroma.server import (
    server,
    _current_project_id,
    _current_client,
    PROJECT_ID,
    _projects,
    _ensure_project,
    _encode_query,
    _get_gpu_sem,
    _rerank_scores,
    DATA_DIR,
    EMBED_MODEL,
    DEFAULT_RETURN_K,
    RERANKER_ENABLED,
    RERANKER_MODEL,
    RERANKER_TOP_K,
    BM25_TOP_K,
    RRF_K_CONST,
    _LOG_MODE,
)
from codev_platform.chroma._obslog import _flog, _log_recall
from codev_platform.chroma._schema import _err, _ok, _build_where
from codev_platform.core.errors import ErrorCode
from codev_platform.core.obslog import redact_text

# rrf_fuse 仅在 BM25 激活时用 (那要求 bm25 依赖在位); 缺依赖时降级 None, 与 server 同款守护。
try:
    from codev_platform.chroma.bm25 import rrf_fuse
except ImportError:
    rrf_fuse = None


@server.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    # Multi-tenant routing: contextvar 由 SSE handler 在 session 起来时 set
    pid = _current_project_id.get()
    if pid is None:
        # stdio mode 或 SSE 没传 ?project_id= 时, fallback 到 daemon 启动默认
        pid = PROJECT_ID
    state = _ensure_project(pid)
    if state is None or state.collection is None:
        err = (
            _projects.get(pid).init_error if pid in _projects else None
        ) or mdl._global_init_error or f"project {pid} 未初始化"
        return _err(err, ErrorCode.PROJECT_UNKNOWN)
    col = state.collection
    _bm25 = state.bm25_index

    _t0 = time.perf_counter()
    # 记录 project last_request_at (widget 三态点用).
    # init_error 路径已 _err 早返回, 不进此处 — 失败请求视为静默 (widget 显 init_error + ts=null)
    state.last_request_at = time.time()
    try:
        if name == "search_docs":
            query = args.get("query", "").strip()
            if not query:
                return _err("query 不能为空", ErrorCode.INVALID_PARAMS)
            _flog(f"[search_docs] q={query!r} k={args.get('k', 5)} cat={args.get('category', 'all')} mod={args.get('module', 'all')}")
            k = int(args.get("k", DEFAULT_RETURN_K))
            k = max(1, min(20, k))
            category = args.get("category", "all")
            module = args.get("module", "all")
            where = _build_where(category, module)

            # Stage 1: embedding 召回. Reranker 启用时取 top-K 候选, 否则直接 top-k.
            use_rerank = RERANKER_ENABLED and Path(RERANKER_MODEL or "").exists() and rr._reranker_load_err is None
            n_candidates = max(k, RERANKER_TOP_K) if use_rerank else k

            # GPU 串行: 多 session 并发 search_docs 时 encode 排队 (防 8GB VRAM 抖动)
            async with _get_gpu_sem():
                qvec = _encode_query(query)
            kwargs: dict[str, Any] = {"query_embeddings": [qvec], "n_results": n_candidates}
            if where is not None:
                kwargs["where"] = where
            try:
                res = col.query(**kwargs)
            except Exception as q_exc:  # noqa: BLE001
                # F9: reindex 期间底层 collection 被重建, daemon 持 stale handle → NotFoundError。
                # evict 该 project 重新 ensure 一次, 拿到新 handle 后重试 (一次, 仍失败则上抛)。
                _flog(f"[search_docs] query failed ({type(q_exc).__name__}: {q_exc}), evict+retry once")
                _projects.pop(pid, None)
                state = _ensure_project(pid)
                if state is None or state.collection is None:
                    raise
                col = state.collection
                _bm25 = state.bm25_index
                res = col.query(**kwargs)

            ids = (res.get("ids") or [[]])[0]
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]

            # Stage 1.5: BM25 并行召回 + RRF 融合(可选,失败降级纯向量).
            # 精确符号 / 版本号 / 罕见术语用 BM25 比向量准,中文段语义用向量.
            bm25_used = False
            bm25_hits_n = 0
            if _bm25 is not None and _bm25.ready():
                try:
                    _tb = time.perf_counter()
                    bm25_hits = _bm25.search(query, n=BM25_TOP_K, where=where)
                    bm25_hits_n = len(bm25_hits)
                    if bm25_hits:
                        # RRF 融合两路 rank
                        vector_ids = list(ids)
                        bm25_ids = [h[0] for h in bm25_hits]
                        fused = rrf_fuse(vector_ids, bm25_ids, k_const=RRF_K_CONST)

                        # 重组 ids/docs/metas/dists:按融合 rank,缺失字段从 BM25 索引补
                        # BM25-only 命中没有 chroma distance,标 None
                        vec_pos = {cid: i for i, cid in enumerate(vector_ids)}
                        bm25_lookup = {h[0]: (h[2], h[3]) for h in bm25_hits}

                        new_ids: list[str] = []
                        new_docs: list[str] = []
                        new_metas: list[dict] = []
                        new_dists: list[float | None] = []
                        for cid, _ in fused[:n_candidates]:
                            new_ids.append(cid)
                            if cid in vec_pos:
                                i = vec_pos[cid]
                                new_docs.append(docs[i] if i < len(docs) else "")
                                new_metas.append(metas[i] if i < len(metas) else {})
                                new_dists.append(dists[i] if i < len(dists) else None)
                            elif cid in bm25_lookup:
                                bdoc, bmeta = bm25_lookup[cid]
                                new_docs.append(bdoc)
                                new_metas.append(bmeta)
                                new_dists.append(None)  # BM25-only,无向量距离
                        ids, docs, metas, dists = new_ids, new_docs, new_metas, new_dists
                        bm25_used = True
                        _flog(
                            f"[bm25] vec={len(vector_ids)} bm25={bm25_hits_n} "
                            f"fused={len(fused)} -> top {n_candidates}, took={(time.perf_counter()-_tb)*1000:.1f}ms"
                        )
                except Exception as exc:  # noqa: BLE001
                    _flog(f"[bm25] search FAILED, fallback to vector-only: {exc!s}")

            # Stage 2: Reranker 重排(可选). 失败降级用 embedding 顺序.
            rerank_scores_arr: list[float] | None = None
            rerank_used = False
            if use_rerank and docs:
                _tr = time.perf_counter()
                # GPU 串行 (reranker 同享 GPU 与 embed model)
                async with _get_gpu_sem():
                    scores = _rerank_scores(query, docs)
                if scores is not None:
                    rerank_scores_arr = scores
                    rerank_used = True
                    # 按 rerank score 降序重排所有数组
                    order = sorted(range(len(docs)), key=lambda i: scores[i], reverse=True)
                    ids = [ids[i] for i in order]
                    docs = [docs[i] for i in order]
                    metas = [metas[i] for i in order]
                    dists = [dists[i] for i in order]
                    rerank_scores_arr = [scores[i] for i in order]
                    _flog(f"[rerank] {len(scores)} pairs, top_score={rerank_scores_arr[0]:.4f}, took={(time.perf_counter()-_tr)*1000:.1f}ms")

            # 取 top-k 返回
            ids = ids[:k]
            docs = docs[:k]
            metas = metas[:k]
            dists = dists[:k]
            if rerank_scores_arr is not None:
                rerank_scores_arr = rerank_scores_arr[:k]

            out = []
            for idx, _id in enumerate(ids):
                meta = metas[idx] if idx < len(metas) else {}
                item = {
                    "file": (meta or {}).get("file"),
                    "category": (meta or {}).get("category"),
                    "module": (meta or {}).get("module"),
                    "chunk_index": (meta or {}).get("chunk_index"),
                    "distance": dists[idx] if idx < len(dists) else None,
                    "chunk": docs[idx] if idx < len(docs) else "",
                }
                if rerank_scores_arr is not None:
                    item["rerank_score"] = rerank_scores_arr[idx]
                out.append(item)

            _ms = (time.perf_counter() - _t0) * 1000
            top1 = (metas[0] or {}).get("file") if metas else None
            top1 = redact_text(top1, _LOG_MODE)  # prod 脱敏召回路径 (dev 原样)
            top1_d = dists[0] if dists else None
            top1_rs = rerank_scores_arr[0] if rerank_scores_arr else None
            _flog(f"[search_docs] hit={len(out)} top1={top1} dist={top1_d} rerank={rerank_used} top1_score={top1_rs} took={_ms:.1f}ms")
            # JSONL 召回日志:记录 top-5 完整 metadata + distance + rerank_score
            import datetime as _dt
            # prod 下: query 原文 + 召回 file 路径脱敏 (路径暴露仓库结构, 算敏感);
            # category/module/chunk_index/distance/rerank_score 是结构标量, 保留。
            top5 = []
            for i in range(min(5, len(out))):
                m = metas[i] if i < len(metas) else {}
                entry = {
                    "file": redact_text((m or {}).get("file"), _LOG_MODE),
                    "category": (m or {}).get("category"),
                    "module": (m or {}).get("module"),
                    "chunk_index": (m or {}).get("chunk_index"),
                    "distance": dists[i] if i < len(dists) else None,
                }
                if rerank_scores_arr is not None and i < len(rerank_scores_arr):
                    entry["rerank_score"] = rerank_scores_arr[i]
                top5.append(entry)
            _log_recall({
                "ts": _dt.datetime.now().isoformat(timespec="seconds"),
                "project_id": pid,
                "client": _current_client.get(),  # agent(web 端) / dev(开发端直调), 采纳率分桶
                "query": redact_text(query, _LOG_MODE),
                "k": k,
                "category": category,
                "module": module,
                "hit": len(out),
                "rerank_used": rerank_used,
                "bm25_used": bm25_used,
                "bm25_hits": bm25_hits_n,
                "n_candidates": n_candidates if use_rerank else None,
                "elapsed_ms": round(_ms, 1),
                "top5": top5,
            })
            return _ok(out)

        if name == "list_collections":
            total = col.count()
            # 拉全部 metadata（不要 documents 节省内存）
            all_data = col.get(include=["metadatas"])
            metas = all_data.get("metadatas") or []
            by_category: dict[str, int] = {}
            by_module: dict[str, int] = {}
            for m in metas:
                if not m:
                    continue
                c = m.get("category") or "unknown"
                mod = m.get("module") or "unknown"
                by_category[c] = by_category.get(c, 0) + 1
                by_module[mod] = by_module.get(mod, 0) + 1
            return _ok(
                {
                    "project_id": pid,
                    "collection": state.active_collection_name,
                    "data_dir": str(DATA_DIR),
                    "embed_model": EMBED_MODEL,
                    "collection_metadata": col.metadata or {},
                    "total_chunks": total,
                    "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1])),
                    "by_module": dict(sorted(by_module.items(), key=lambda x: -x[1])),
                    "tenant_mode": "multi",
                    "loaded_projects": list(_projects.keys()),
                }
            )

        if name == "get_by_file":
            file_path = (args.get("file") or "").strip()
            if not file_path:
                return _err("file 不能为空", ErrorCode.INVALID_PARAMS)
            res = col.get(where={"file": file_path}, include=["documents", "metadatas"])
            ids = res.get("ids") or []
            docs = res.get("documents") or []
            metas = res.get("metadatas") or []
            if not ids:
                return _err(f"未找到文件 '{file_path}' 的任何 chunk（检查路径是否相对仓库根）", ErrorCode.INDEX_MISSING)
            pairs = []
            for idx, _id in enumerate(ids):
                meta = metas[idx] if idx < len(metas) else {}
                pairs.append(
                    {
                        "chunk_index": (meta or {}).get("chunk_index", idx),
                        "category": (meta or {}).get("category"),
                        "module": (meta or {}).get("module"),
                        "chunk": docs[idx] if idx < len(docs) else "",
                    }
                )
            pairs.sort(key=lambda x: x.get("chunk_index") or 0)
            return _ok({"file": file_path, "chunk_count": len(pairs), "chunks": pairs})

        return _err(f"未知 tool: {name}", ErrorCode.INVALID_PARAMS)
    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        print(f"[chroma-mcp] tool '{name}' error: {tb}", file=sys.stderr)
        return _err(f"tool '{name}' 执行失败: {exc!s}")
