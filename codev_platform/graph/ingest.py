"""ingest 路径 (Phase 3 起步) —— 跑插件 -> 落统一图谱 store。

把 plugins.registry.run_applicable 的产出灌进 graph.store:对一个仓库执行所有适用
且成功的 analyzer 插件,逐个把 AnalyzerResult upsert 进该 project 的统一图谱 sqlite。

执行隔离继承自 registry/executor:单插件 detect/analyze 崩溃只会被滤掉 (不进
run_applicable 的成功列表),不影响其余插件入库。store 写入按 plugin 归属幂等替换,
重跑 ingest 不产生重复。

不改 cross_link / codegraph / 现有 graph 路由 —— 只新增这条聚合落盘路径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, NodeKind
from codev_platform.graph.store import load_graph, open_store, upsert_result
from codev_platform.plugins.builtin import _stack_scan
from codev_platform.plugins.registry import run_applicable

LINKER_PLUGIN = "builtin.linker"


@dataclass
class IngestReport:
    """一次 ingest 的可观测结果。

    project_id:  入库的项目。
    ingested:    成功写入 store 的 plugin 名列表。
    summaries:   每个 plugin 的产出计数 (plugin -> {nodes, edges, ...})。
    """

    project_id: str
    ingested: list[str] = field(default_factory=list)
    summaries: dict[str, dict] = field(default_factory=dict)


def ingest_project(
    repo_path: Path | str,
    project_id: str,
    *,
    store_path: Path | None = None,
) -> IngestReport:
    """对 repo 跑所有适用插件, 把成功产出灌进 project 的统一图谱 store。

    Args:
        repo_path:   被分析的仓库根路径。
        project_id:  图谱隔离键 (决定 store 文件)。
        store_path:  显式覆盖 store sqlite 路径 (测试用);None 走默认布局。

    Returns:
        IngestReport — 哪些插件成功入库 + 各自计数。
    """
    results = run_applicable(repo_path, project_id)
    report = IngestReport(project_id=project_id)
    conn = open_store(project_id, path=store_path)
    try:
        for exec_result in results:
            analyzer_result = exec_result.result
            if analyzer_result is None:  # run_applicable 只回 ok=True, 兜底保险
                continue
            upsert_result(conn, project_id, analyzer_result)
            report.ingested.append(exec_result.plugin)
            report.summaries[exec_result.plugin] = exec_result.summary

        # 核心 cross-plugin linker pass: 各插件落库后, 读回全量节点, 跨**所有**后端插件
        # (fastapi/spring/node) 把 frontend_api_call --calls_api--> backend_endpoint 连起来。
        # 这是 calls_api 的**唯一** owner (前端插件不再各自只链同仓 FastAPI), 解决前端
        # 链不到 Java/Spring 端点的缺口。挂 builtin.linker, upsert 幂等可重跑。
        _link_pass(conn, project_id, report)
    finally:
        conn.close()
    return report


def _link_pass(conn, project_id: str, report: IngestReport) -> None:
    """跨插件链接: 读 store 全量节点 -> calls_api 边 -> upsert builtin.linker。"""
    merged = load_graph(conn, project_id)
    frontend = [
        n for n in merged.nodes if n.kind == NodeKind.FRONTEND_API_CALL.value
    ]
    backend = [
        n for n in merged.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value
    ]
    edges = _stack_scan.link_api_calls(frontend, backend)
    upsert_result(
        conn, project_id, AnalyzerResult(edges=edges, plugin=LINKER_PLUGIN)
    )
    report.ingested.append(LINKER_PLUGIN)
    report.summaries[LINKER_PLUGIN] = {"calls_api_edges": len(edges)}
