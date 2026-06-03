#!/usr/bin/env python
"""Track A2 parity 对账: 统一图谱 store vs cross_layer.sqlite 覆盖核对。

目的: 证明**连通后的统一图谱 store** 对 endpoint / table / 跨层连通的覆盖
>= 旧 **cross_layer.sqlite**, 为退役 cross-link 提供量化依据。

对账 4 项 (两库各自统计, 同义口径对齐):

    | 维度              | store (graph)                       | cross_layer (cross-link)              |
    |-------------------|-------------------------------------|---------------------------------------|
    | endpoint 数       | nodes kind=backend_endpoint         | nodes kind=java_endpoint              |
    | table 数          | nodes kind=db_table                 | nodes kind=table AND path IS NULL     |
    | 前端->端点链接数  | edges kind=calls_api                | edges rel=calls_api                   |
    | 端点->表可达数    | endpoint BFS(经函数 calls/reads/   | endpoint -> java_method -> table       |
    |                   |   writes/updates_table) 触表        |   (queries/writes/updates_table)      |

判定: store 每项 >= cross_layer 则 "可退役"; 否则列出缺口项。

纯只读 (两库都只 SELECT / BFS, 不写不改)。两库任一缺失 -> graceful 报告
(缺失库该侧计为 0 / "缺失", 不抛栈)。

退出码: 0 = store 覆盖全部 >= cross_layer (可退役); 1 = 有缺口 (列差距)。

用法:
    python tools/audit_graph_parity.py <project_id>
    python tools/audit_graph_parity.py <project_id> --json

注: store 需先 reindex --ingest 过 (桥接边 + linker 边才在), 否则端点->表
可达数 / 前端链接数偏低属预期 (会在结论里提示)。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# 允许直接 `python tools/audit_graph_parity.py` 运行 (把仓根加进 sys.path)。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from codev_platform.core.paths import (  # noqa: E402
    cross_link_db_path,
    cross_link_legacy_db_path,
)
from codev_platform.graph.impact import build_impact_graph, layer_of  # noqa: E402
from codev_platform.graph.schema import EdgeKind, NodeKind  # noqa: E402
from codev_platform.graph.store import graph_store_path  # noqa: E402

# cross_layer 里"端点->表"那一跳的边类型 (java_method 碰表)。
_CROSS_TABLE_RELS = ("queries_table", "writes_table", "updates_table")
# store 里"函数->表"那一跳的边类型。
_STORE_TABLE_EDGE_KINDS = {
    EdgeKind.READS_TABLE.value,
    EdgeKind.WRITES_TABLE.value,
    EdgeKind.UPDATES_TABLE.value,
}


# --------------------------------------------------------------------- store 侧

def _store_metrics(project_id: str, store_path: Path) -> dict:
    """从统一图谱 store 统计 4 项。store 缺失 -> available=False, 全 0。"""
    if not store_path.exists():
        return {
            "available": False,
            "path": str(store_path),
            "endpoints": 0,
            "tables": 0,
            "frontend_links": 0,
            "endpoint_table_reach": 0,
        }

    conn = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
    try:
        g = build_impact_graph(conn, project_id)
    finally:
        conn.close()

    endpoint_ids = [
        nid for nid, n in g.nodes.items()
        if n.kind == NodeKind.BACKEND_ENDPOINT.value
    ]
    table_ids = {
        nid for nid, n in g.nodes.items()
        if n.kind == NodeKind.DB_TABLE.value
    }
    frontend_links = sum(
        1
        for src, adj in g.fwd.items()
        for (_tgt, kind) in adj
        if kind == EdgeKind.CALLS_API.value
    )

    reach = _store_endpoint_table_reach(g, endpoint_ids, table_ids)

    return {
        "available": True,
        "path": str(store_path),
        "endpoints": len(endpoint_ids),
        "tables": len(table_ids),
        "frontend_links": frontend_links,
        "endpoint_table_reach": reach,
    }


def _store_endpoint_table_reach(g, endpoint_ids: list[str], table_ids: set[str]) -> int:
    """正向 BFS: 有多少 (endpoint -> ... -> db_table) 可达对 (去重计数)。

    口径与 cross_layer 的 "endpoint -> java_method -> table" 对齐, 但 store 端点到
    表中间可能多跳 (端点--calls-->函数--reads/writes-->表), 故走通用 BFS 到任意
    db_table 即算一对可达。
    """
    pairs = 0
    for ep in endpoint_ids:
        reached_tables: set[str] = set()
        visited = {ep}
        queue = [ep]
        while queue:
            nid = queue.pop(0)
            for (tgt, _kind) in g.fwd.get(nid, ()):
                if tgt in visited:
                    continue
                visited.add(tgt)
                if tgt in table_ids:
                    reached_tables.add(tgt)
                else:
                    queue.append(tgt)
        pairs += len(reached_tables)
    return pairs


# ---------------------------------------------------------------- cross_layer 侧

def _resolve_cross_path(project_id: str) -> Path | None:
    """优先 per-project 路径, 回退 legacy。两者都不存在 -> None。"""
    new_path = cross_link_db_path(project_id)
    if new_path.exists():
        return new_path
    legacy = cross_link_legacy_db_path()
    if legacy.exists():
        return legacy
    return None


def _cross_metrics(project_id: str) -> dict:
    """从 cross_layer.sqlite 统计 4 项。缺失 -> available=False, 全 0。"""
    path = _resolve_cross_path(project_id)
    if path is None:
        return {
            "available": False,
            "path": str(cross_link_db_path(project_id)),
            "endpoints": 0,
            "tables": 0,
            "frontend_links": 0,
            "endpoint_table_reach": 0,
        }

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        endpoints = _scalar(
            conn, "SELECT COUNT(*) FROM nodes WHERE kind = 'java_endpoint'"
        )
        # table 节点 path IS NULL 排重 (与 query.py 的逻辑表节点口径一致, 列/带 path
        # 的引用节点不计)。
        tables = _scalar(
            conn,
            "SELECT COUNT(*) FROM nodes WHERE kind = 'table' AND path IS NULL",
        )
        frontend_links = _scalar(
            conn, "SELECT COUNT(*) FROM edges WHERE rel = 'calls_api'"
        )
        reach = _cross_endpoint_table_reach(conn)
    finally:
        conn.close()

    return {
        "available": True,
        "path": str(path),
        "endpoints": endpoints,
        "tables": tables,
        "frontend_links": frontend_links,
        "endpoint_table_reach": reach,
    }


def _cross_endpoint_table_reach(conn: sqlite3.Connection) -> int:
    """cross_layer 端点->表可达对数。

    cross-link 不直接连 endpoint->method, endpoint 与碰表的 java_method 都是
    java_* 节点。这里取**全部 java_method 经 queries/writes/updates_table 触到的
    去重 (method, table) 对**作为后端->表可达基数 —— 与 store 端点->表的语义对齐
    (store 经 codegraph 桥接把端点连到碰表的函数)。

    用 (src_id, dst_id) 去重, 一个 method 多次 query 同表只算一次。
    """
    placeholders = ", ".join("?" for _ in _CROSS_TABLE_RELS)
    cur = conn.execute(
        f"""SELECT COUNT(*) FROM (
                SELECT DISTINCT e.src_id, e.dst_id
                FROM edges e
                JOIN nodes m ON m.id = e.src_id AND m.kind = 'java_method'
                JOIN nodes t ON t.id = e.dst_id AND t.kind = 'table'
                WHERE e.rel IN ({placeholders})
            )""",
        _CROSS_TABLE_RELS,
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _scalar(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


# ------------------------------------------------------------------- 报告 / 主流程

_METRICS = [
    ("endpoints", "endpoint 数"),
    ("tables", "table 数"),
    ("frontend_links", "前端->端点链接数 (calls_api)"),
    ("endpoint_table_reach", "端点->表可达对数"),
]


def build_report(project_id: str, store: dict, cross: dict) -> dict:
    """组装对账结果: 逐项 store vs cross + 是否达标 + 缺口清单。"""
    rows = []
    gaps = []
    for key, label in _METRICS:
        s_val = store[key]
        c_val = cross[key]
        ok = s_val >= c_val
        rows.append(
            {"metric": key, "label": label, "store": s_val,
             "cross": c_val, "ok": ok}
        )
        if not ok:
            gaps.append(
                {"metric": key, "label": label, "store": s_val,
                 "cross": c_val, "deficit": c_val - s_val}
            )

    return {
        "project_id": project_id,
        "store_available": store["available"],
        "cross_available": cross["available"],
        "store_path": store["path"],
        "cross_path": cross["path"],
        "rows": rows,
        "gaps": gaps,
        "retire_ok": len(gaps) == 0,
    }


def print_report(report: dict) -> None:
    pid = report["project_id"]
    print(f"=== graph store vs cross_layer parity — project: {pid} ===")
    print(f"store path : {report['store_path']}"
          f"  ({'OK' if report['store_available'] else 'MISSING'})")
    print(f"cross path : {report['cross_path']}"
          f"  ({'OK' if report['cross_available'] else 'MISSING'})")
    print()

    label_w = max(len(r["label"]) for r in report["rows"])
    header = f"  {'metric'.ljust(label_w)}  {'store':>8}  {'cross':>8}  result"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in report["rows"]:
        mark = ">= OK" if r["ok"] else "<  GAP"
        print(
            f"  {r['label'].ljust(label_w)}  "
            f"{r['store']:>8}  {r['cross']:>8}  {mark}"
        )
    print()

    if not report["store_available"]:
        print("[!] store 缺失 -> 先跑 reindex --ingest 生成统一图谱再对账。")
    if not report["cross_available"]:
        print("[i] cross_layer 缺失 -> 该项目无旧 cross-link 数据, store 视为唯一来源。")

    if report["retire_ok"]:
        print("结论: store 覆盖每项 >= cross_layer => 可退役 cross-link。")
        if not report["cross_available"]:
            print("      (注: cross_layer 不存在, 退役判定 vacuously true。)")
    else:
        print("结论: store 存在覆盖缺口, 暂不可退役。缺口项:")
        for gp in report["gaps"]:
            print(
                f"  - {gp['label']}: store={gp['store']} < "
                f"cross={gp['cross']} (缺 {gp['deficit']})"
            )
        print("      可能原因: store 未跑 reindex --ingest (桥接/linker 边缺) / "
              "对应 analyzer 插件未覆盖该栈。")


def run(project_id: str) -> dict:
    """对给定 project 跑双库对账, 返回报告 dict (供测试 / --json 复用)。"""
    store = _store_metrics(project_id, graph_store_path(project_id))
    cross = _cross_metrics(project_id)
    return build_report(project_id, store, cross)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="统一图谱 store vs cross_layer.sqlite 覆盖对账 (退役依据)。"
    )
    parser.add_argument("project_id", help="项目隔离键, 如 openclaw-stock")
    parser.add_argument(
        "--json", action="store_true", help="输出机器可读 JSON 而非对账表"
    )
    args = parser.parse_args(argv)

    report = run(args.project_id)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    return 0 if report["retire_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
