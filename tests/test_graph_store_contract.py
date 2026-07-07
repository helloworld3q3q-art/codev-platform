"""GraphStore 契约测试 —— sqlite / pg 两后端参数化双跑, 证行为一致(防漂移)。

Stage B 的硬闸: GraphStore Protocol 抽象的全部语义(往返 / 幂等 / 隔离 / stats /
audit_scan / list_project_ids)对两实现必须等价。断言体后端无关, 只吃 `store` fixture。

后端门控:
- sqlite: 永远跑(open_store + tmp_path 临时文件)。
- pg: 双重 gate —— PgGraphStore 未落地(ImportError)或 dsn 缺/连不上 → skip,
  绝不硬依赖。PgGraphStore 落地后同一批断言自动双跑。

audit_scan 的对抗注入(串台行 / 软产物 plugin 漂移)需要绕过 GraphStore 的 pid 强制
往原表直插行。GraphStore 不暴露底层 conn(防 recouple), 故注入只能走后端原生连接:
sqlite 用独立 sqlite3.connect 同文件直插; pg 后端没有可移植的"裸表直插"路径(且 org_id
行隔离的注入语义与 sqlite 文件隔离不同), 故 audit_scan 的对抗注入断言**仅 sqlite 跑**,
pg 后端 skip 该用例(见 test 内注释)。canonical 比对在 audit.py, 此处只验 store 探查事实。
"""
from __future__ import annotations

import pytest

from codev_platform.graph.schema import (
    AnalyzerResult,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store

PID = "t-contract"
# 共享 PG 库按 project_id 隔离(无 org_id, 同 reindex_jobs)。本 contract 用的固定 pid 集,
# teardown 按这些 pid 清行避免与真数据 / 并发串(非 Date/random)。
_TEST_PIDS = [PID, "p-iso", "OTHER-PID"]


def _sample_result(plugin: str = "builtin.fake") -> AnalyzerResult:
    return AnalyzerResult(
        nodes=[
            GraphNode(
                id=f"{PID}:db_table:t1", kind="db_table", name="t1",
                project_id=PID, file="a.sql", line=3, language="sql",
                meta={"rows": 100, "raw": "x"},
            ),
            GraphNode(
                id=f"{PID}:backend_function:m1", kind="backend_function",
                name="m1", project_id=PID, file="M.java", line=42,
            ),
        ],
        edges=[
            GraphEdge(
                source=f"{PID}:backend_function:m1", target=f"{PID}:db_table:t1",
                kind="reads_table", confidence=0.7, meta={"evidence": "SELECT *"},
            ),
        ],
        evidences=[
            Evidence(source=plugin, detail="hit", file="M.java", line=42,
                     confidence=0.9, meta={"snippet": "select"}),
        ],
        findings=[
            Finding(kind="impact", severity="high", title="t1 写入面广",
                    detail="多处写", node_ids=[f"{PID}:db_table:t1"],
                    evidence_ids=["0"], meta={"score": 5}),
        ],
        plugin=plugin,
        plugin_version="1.0.0",
    )


def _pg_dsn() -> str | None:
    from codev_platform.core.config import get, load_config
    import os
    return os.environ.get("CODEV_PLATFORM_MEMORY_DSN") or get(load_config(), "memory.pg_dsn")


@pytest.fixture(params=["sqlite", "pg"])
def store(request, tmp_path):
    """两后端参数化。sqlite 用临时文件; pg 双重 gate(import + dsn)+ teardown 清 org 行。"""
    backend = request.param
    if backend == "sqlite":
        s = open_store(PID, path=tmp_path / "g.sqlite")
        yield s
        s.close()
        return

    # pg 分支: PgGraphStore 未落地 → skip(不硬依赖)。
    try:
        from codev_platform.graph.pg_store import PgGraphStore
    except ImportError:
        pytest.skip("PgGraphStore 未落地(Stage B)")
    dsn = _pg_dsn()
    if not dsn:
        pytest.skip("无 memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN")
    try:
        s = PgGraphStore(dsn)
        s.probe()   # 建表 + 探活; 缺 psycopg / 连不上 → skip
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PgGraphStore 不可用: {exc}")
    _pg_clean(dsn)   # 进场清本 contract pid 残留(防上次 teardown 中断)
    yield s
    _pg_clean(dsn)
    s.close()


def _pg_clean(dsn) -> None:
    """删本 contract 用到的 project_id 全部行(共享 PG 库 project_id 隔离), 收敛干净态。"""
    import psycopg
    with psycopg.connect(dsn) as conn:
        for t in ("graph_nodes", "graph_edges", "graph_evidences", "graph_findings",
                  "graph_ingest_meta"):
            conn.execute(f"DELETE FROM {t} WHERE project_id = ANY(%s)", (_TEST_PIDS,))
        conn.commit()


# ---- 1. upsert 幂等替换: 重灌同 plugin 不翻倍 ----

def test_upsert_idempotent_no_double(store):
    store.upsert_result(PID, _sample_result())
    store.upsert_result(PID, _sample_result())  # 重灌同 plugin
    got = store.load_graph(PID, plugin="builtin.fake")
    s = store.stats(PID)
    assert len(got.nodes) == 2
    assert len(got.edges) == 1
    assert s["totals"]["nodes"] == 2
    one = next(p for p in s["plugins"] if p["plugin"] == "builtin.fake")
    assert one["node_count"] == 2


# ---- 2. 多 plugin 共存 + 重灌一个不动另一个 ----

def test_multi_plugin_isolated_reingest(store):
    store.upsert_result(PID, _sample_result(plugin="builtin.a"))
    store.upsert_result(PID, _sample_result(plugin="builtin.b"))
    thin = _sample_result(plugin="builtin.a")
    thin.nodes = thin.nodes[:1]
    store.upsert_result(PID, thin)  # 只重灌 a → 1 节点
    a = store.load_graph(PID, plugin="builtin.a").nodes
    b = store.load_graph(PID, plugin="builtin.b").nodes
    assert len(a) == 1   # a 被替换
    assert len(b) == 2   # b 不受影响


# ---- 3. load_graph 往返一致(字段保真 + meta 不透明往返)+ plugin 过滤 ----

def test_roundtrip_fidelity_and_plugin_filter(store):
    store.upsert_result(PID, _sample_result())
    got = store.load_graph(PID, plugin="builtin.fake")
    src = _sample_result()
    # 节点按 id 比对(load_graph 按 id 排序, 与源 list 顺序无关)。
    assert {n.id: n.to_dict() for n in got.nodes} == {n.id: n.to_dict() for n in src.nodes}
    assert [e.to_dict() for e in got.edges] == [e.to_dict() for e in src.edges]
    assert [ev.to_dict() for ev in got.evidences] == [ev.to_dict() for ev in src.evidences]
    assert [f.to_dict() for f in got.findings] == [f.to_dict() for f in src.findings]
    assert got.plugin == "builtin.fake"
    assert got.plugin_version == "1.0.0"
    # meta 不透明往返(dict 原样)。
    t1 = next(n for n in got.nodes if n.name == "t1")
    assert t1.meta == {"rows": 100, "raw": "x"}
    # plugin 过滤: 别 plugin 返空。
    assert store.load_graph(PID, plugin="builtin.nonexistent").nodes == []


# ---- 4. project 隔离: 别 pid load 返空 ----

def test_project_isolation(store):
    store.upsert_result(PID, _sample_result())
    assert store.load_graph("p-iso").nodes == []   # 从未写过的 pid
    assert store.load_graph(PID).nodes               # 本 pid 有数据


# ---- 5. stats: 四表计数 + plugins 含 ingest_meta ----

def test_stats_totals_and_plugins(store):
    store.upsert_result(PID, _sample_result(plugin="builtin.a"))
    store.upsert_result(PID, _sample_result(plugin="builtin.b"))
    s = store.stats(PID)
    assert s["totals"]["nodes"] == 4   # 2 + 2
    assert s["totals"]["edges"] == 2
    assert s["totals"]["evidences"] == 2
    assert s["totals"]["findings"] == 2
    assert {p["plugin"] for p in s["plugins"]} == {"builtin.a", "builtin.b"}
    a = next(p for p in s["plugins"] if p["plugin"] == "builtin.a")
    assert a["node_count"] == 2 and a["plugin_version"] == "1.0.0"


# ---- 6. audit_scan: 串台行 + 软产物 plugin 漂移 ----

def test_audit_scan_foreign_and_soft(store, tmp_path, request):
    # 对抗注入需绕过 GraphStore 的 pid 强制往原表直插。GraphStore 不暴露 conn(防 recouple),
    # 注入走后端原生连接: sqlite=独立 sqlite3.connect 同文件; pg=独立 psycopg 同库。两后端均验。
    backend = request.node.callspec.params["store"]
    store.upsert_result(PID, _sample_result())
    role = GraphNode(id=f"{PID}:arch_layer:service", kind=NodeKind.ARCH_LAYER.value,
                     name="service", project_id=PID)
    store.upsert_result(PID, AnalyzerResult(plugin="builtin.analyzers", nodes=[role]))
    # 串台行(别 project_id) + 软产物 plugin 漂移行(arch_layer 软 kind 被自名 plugin 写)。
    foreign = ("x1", "t", "backend_function", "leak", "OTHER-PID", "a.py", None, None, None)
    drift = (f"{PID}:arch_layer:drift", "arch_layer", NodeKind.ARCH_LAYER.value, "drift",
             PID, None, None, None, None)
    if backend == "sqlite":
        import sqlite3
        raw = sqlite3.connect(tmp_path / "g.sqlite")
        raw.executemany(
            "INSERT INTO nodes (id, plugin, kind, name, project_id, file, line, language, "
            "meta_json) VALUES (?,?,?,?,?,?,?,?,?)", [foreign, drift])
        raw.commit()
        raw.close()
    else:
        import psycopg
        with psycopg.connect(_pg_dsn()) as conn:
            conn.cursor().executemany(
                "INSERT INTO graph_nodes (id, plugin, kind, name, project_id, file, line, "
                "language, meta_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", [foreign, drift])
            conn.commit()

    scan = store.audit_scan(PID)
    # soft 产物 plugin 漂移: 两后端都查得到。
    assert "arch_layer" in scan["soft_plugins"]["nodes"]
    if backend == "sqlite":
        # cross-project 串台 = sqlite per-file 隔离概念(文件本应只一个 project)→ 检出 OTHER-PID。
        assert "OTHER-PID" in scan["foreign_project_ids"]["nodes"]
    else:
        # 共享 PG 无 per-file 隔离, 所有 project 合法共表 → 无"串台"概念, foreign 恒空(注入的
        # OTHER-PID 行只是另一 project 的合法邻居, 不误报)。
        assert scan["foreign_project_ids"]["nodes"] == []


# ---- 7. list_project_ids: 建库后能枚举到本 pid ----

def test_list_project_ids_enumerates(store):
    # list_project_ids 是 Protocol 实例方法(两后端一致): sqlite=本 store 文件内 DISTINCT,
    # pg=共享库 DISTINCT。建库后必能枚举到本 pid。
    store.upsert_result(PID, _sample_result())
    assert PID in store.list_project_ids()


# ---- 8. 同批重复 (id,plugin) 节点: 两后端都 last-wins(审计 #3 parity)----

def test_same_batch_dup_node_id_last_wins(store):
    # sqlite INSERT OR REPLACE / PG ON CONFLICT DO UPDATE → 后者赢; 不一个静默吞一个 IntegrityError。
    store.upsert_result(PID, AnalyzerResult(plugin="builtin.dup", nodes=[
        GraphNode(id=f"{PID}:db_table:x", kind="db_table", name="first", project_id=PID),
        GraphNode(id=f"{PID}:db_table:x", kind="db_table", name="second", project_id=PID),
    ]))
    got = store.load_graph(PID, plugin="builtin.dup").nodes
    assert len(got) == 1 and got[0].name == "second"


# ---- 9. PG 读路径中性异常(审计 P1 #6): 坏 dsn 下四读方法均抛 GraphStoreUnreadable ----

def test_pg_read_path_neutral_on_bad_dsn():
    # 消费方(web/recall/audit)只 catch GraphStoreUnreadable 优雅降级 → 读失败绝不能泄漏裸 psycopg。
    try:
        from codev_platform.graph.pg_store import PgGraphStore
    except ImportError:
        pytest.skip("PgGraphStore 未落地")
    from codev_platform.graph.store import GraphStoreUnreadable
    pytest.importorskip("psycopg_pool")
    bad = PgGraphStore("postgresql://nouser:nopass@127.0.0.1:5999/nodb")
    for call in (lambda: bad.load_graph(PID), lambda: bad.stats(PID),
                 lambda: bad.audit_scan(PID), lambda: bad.list_project_ids()):
        with pytest.raises(GraphStoreUnreadable):
            call()
    bad.close()


# ---- 10. 并发 upsert 同 (project,plugin) 经 advisory lock 串行(审计 P1 #2)----

def test_pg_concurrent_upsert_same_key_serialized():
    # 多机并发 upsert 同 key: evidences/findings 纯 INSERT 无 ON CONFLICT, 原会撞 PK UniqueViolation;
    # advisory xact lock 串行化后双线程强制重叠也不抛(失败=advisory lock 未生效, 回归)。
    try:
        from codev_platform.graph.pg_store import PgGraphStore
    except ImportError:
        pytest.skip("PgGraphStore 未落地")
    dsn = _pg_dsn()
    if not dsn:
        pytest.skip("无 memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN")
    import threading
    _pg_clean(dsn)
    errors: list = []
    barrier = threading.Barrier(2)

    def _w():
        try:
            s = PgGraphStore(dsn)
            barrier.wait()   # 强制两线程同时进入 upsert, 放大 race
            for _ in range(5):
                s.upsert_result(PID, _sample_result(plugin="builtin.shared"))
            s.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_w) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _pg_clean(dsn)
    assert not errors, f"并发 upsert 同 key 抛异常(advisory lock 未生效?): {errors}"
