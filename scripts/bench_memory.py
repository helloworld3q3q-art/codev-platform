# -*- coding: utf-8 -*-
r"""memory 压测摸底(C2)—— seed N 条 + 测 write / recall 的 P50/P95(local 与 vector 各一遍)。

隔离 bench org(`bench-mem-org`),跑完 **cleanup**(DELETE bench org 行 + 删 chroma bench collection),
不污染真数据。纯 `percentiles` 可单测;真延迟需 PG(+vector 需 chromadb/Qwen)实跑。

跑法(WSL,需 memory.pg_dsn):
  .venv/bin/python scripts/bench_memory.py [--n 500] [--recalls 100] [--backend both|local|vector] [--keep]
  # vector 在 CPU embed 慢,--n 建议 ≤500;--keep 保留数据给 B1 当 benchmark(默认清理)
退出码 0 / 2(memory 未配)。
"""
from __future__ import annotations

import argparse
import io
import sys
import time

BENCH_ORG = "bench-mem-org"
BENCH_USER = "bench-user"


def percentiles(samples_sec: list[float], ps: tuple[int, ...] = (50, 95, 99)) -> dict[int, float]:
    """秒列表 → {p: 毫秒}(nearest-rank,纯函数可单测)。空列表 → 全 0。"""
    if not samples_sec:
        return {p: 0.0 for p in ps}
    s = sorted(samples_sec)
    out: dict[int, float] = {}
    for p in ps:
        idx = min(len(s) - 1, max(0, int(round(p / 100 * len(s))) - 1))
        out[p] = round(s[idx] * 1000, 2)
    return out


def _content(i: int) -> str:
    return f"benchmark 记忆 {i} 关于主题{i % 50} 关键词kw{i % 20} 偏好深色暗色配色 数据库连接池"


def _query(i: int) -> str:
    return f"kw{i % 20}"


def _bench_writes(store, n: int):
    from codev_platform.agent.memory_store import MemoryEntry
    lat: list[float] = []
    for i in range(n):
        e = MemoryEntry(id="", scope="personal", scope_ref=BENCH_USER, owner_user_id=BENCH_USER,
                        content=_content(i), org_id=BENCH_ORG, kind="fact", topic_key=f"bench-{i}")
        t = time.perf_counter()
        store.write(e)
        lat.append(time.perf_counter() - t)
    return lat


def _bench_recalls(svc, m: int):
    lat: list[float] = []
    for i in range(m):
        t = time.perf_counter()
        svc.recall(org_id=BENCH_ORG, user_id=BENCH_USER, project_id=None, query=_query(i), limit=8)
        lat.append(time.perf_counter() - t)
    return lat


def _report(label: str, write_lat: list[float], recall_lat: list[float]) -> None:
    w, r = percentiles(write_lat), percentiles(recall_lat)
    wtot = sum(write_lat)
    qps = round(len(write_lat) / wtot, 1) if wtot else 0
    print(f"[{label}] writes={len(write_lat)} recalls={len(recall_lat)}")
    print(f"  write  P50={w[50]}ms P95={w[95]}ms P99={w[99]}ms  (~{qps}/s)")
    print(f"  recall P50={r[50]}ms P95={r[95]}ms P99={r[99]}ms")


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")  # CLI 中文输出(导入时不动)
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500, help="seed 条数")
    ap.add_argument("--recalls", type=int, default=100, help="recall 次数")
    ap.add_argument("--backend", choices=["both", "local", "vector"], default="both")
    ap.add_argument("--keep", action="store_true", help="保留 bench 数据(默认跑完清理)")
    args = ap.parse_args()

    from codev_platform.core.config import env_or_config, load_config
    cfg = load_config()
    dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
    if not dsn:
        print("[ABORT] memory.pg_dsn 未配。", file=sys.stderr)
        return 2
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("[ABORT] 缺 psycopg。", file=sys.stderr)
        return 2

    from codev_platform.agent.memory_store_pg import SqlMemoryStore
    raw = SqlMemoryStore(dsn)
    index = None

    try:
        if args.backend in ("local", "both"):
            from codev_platform.agent.recall_service import LocalRecallService
            print(f"=== LOCAL backend (n={args.n}, recalls={args.recalls}) ===")
            wl = _bench_writes(raw, args.n)
            rl = _bench_recalls(LocalRecallService(raw), args.recalls)
            _report("local", wl, rl)
            _cleanup(raw, index)  # 清 local 这批,vector 重新 seed(隔离两次测量)

        if args.backend in ("vector", "both"):
            from codev_platform.agent.memory_store_vector import VectorSyncMemoryStore
            from codev_platform.agent.memory_vector_chroma import build_memory_vector_index
            from codev_platform.agent.recall_service import VectorRecallService
            index = build_memory_vector_index(cfg)
            if index is None:
                print("[vector] 跳过:chromadb/sentence-transformers/模型 不可用")
            else:
                nv = min(args.n, 300)  # vector CPU embed 慢,封顶
                print(f"=== VECTOR backend (n={nv}, recalls={min(args.recalls,50)}, embed CPU 慢) ===")
                vstore = VectorSyncMemoryStore(raw, index)
                wl = _bench_writes(vstore, nv)
                rl = _bench_recalls(VectorRecallService(raw, index), min(args.recalls, 50))
                _report("vector", wl, rl)
    finally:
        if not args.keep:
            _cleanup(raw, index)
            print("[cleanup] 已删 bench org 数据 + bench collection")
        else:
            print(f"[keep] 保留 bench 数据(org={BENCH_ORG})")
    return 0


def _cleanup(store, index) -> None:
    try:
        with store._write_pool.connection() as conn:
            conn.execute("DELETE FROM memory_entries WHERE org_id=%s", (BENCH_ORG,))
    except Exception as e:  # noqa: BLE001
        print(f"[cleanup] PG 清理失败:{e}", file=sys.stderr)
    if index is not None:
        try:
            from codev_platform.core.paths import chroma_collection_name
            index._client.delete_collection(name=chroma_collection_name(BENCH_ORG, "agent_memory"))
        except Exception:  # noqa: BLE001 — collection 可能未建
            pass


if __name__ == "__main__":
    sys.exit(main())
