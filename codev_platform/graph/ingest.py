"""ingest 路径 (Phase 3 起步) —— 跑插件 -> 落统一图谱 store。

把 plugins.registry.run_applicable 的产出灌进 graph.store:对一个仓库执行所有适用
且成功的 analyzer 插件,逐个把 AnalyzerResult upsert 进该 project 的统一图谱 sqlite。

执行隔离继承自 registry/executor:单插件 detect/analyze 崩溃只会被滤掉 (不进
run_applicable 的成功列表),不影响其余插件入库。store 写入按 plugin 归属幂等替换,
重跑 ingest 不产生重复。

不改 cross_link / codegraph / 现有 graph 路由 —— 只新增这条聚合落盘路径。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from codev_platform.graph.schema import AnalyzerResult, GraphEdge, NodeKind
from codev_platform.graph.store import load_graph, open_store, upsert_result
from codev_platform.plugins.builtin import _stack_scan
from codev_platform.plugins.registry import run_applicable

logger = logging.getLogger(__name__)

LINKER_PLUGIN = "builtin.linker"
CALLS_PLUGIN = "builtin.call_resolvers"  # 调用边(CALLS)统一归属: 多 resolver 去重后合并入此 plugin
FRONTEND_DEPS_PLUGIN = "builtin.frontend_deps"  # 前端组件依赖图(接 dependency-cruiser)


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

        # 调用边 post-pass: 跑所有适用 CallResolver(codegraph 兜底 + 未来各语言栈 resolver)把
        # endpoint→function / 函数→函数 calls 边物化, store 自成连通解锁影响分析。按语言栈
        # 可扩展(graph/call_resolvers/), 全局去重, fail-soft。
        _calls_pass(conn, project_id, report, Path(repo_path))

        # 前端组件依赖 post-pass: 接 dependency-cruiser(读 tsconfig paths 解 @/ alias)产
        # frontend_component 节点 + renders 边, 解锁"改组件→影响哪些页面"(codegraph 盲区)。
        # 框架无关(react .tsx + vue .vue 都吃), 自 detect, fail-soft 无 node/前端则空。
        _frontend_deps_pass(conn, project_id, report, Path(repo_path))
    finally:
        conn.close()
    return report


def _calls_pass(conn, project_id: str, report: IngestReport, repo_path: Path) -> None:
    """调用边 post-pass: 跑所有适用 CallResolver(按语言栈)→ 全局去重 → upsert 统一 plugin。

    取代原 _bridge_pass: codegraph resolver(原桥接逻辑)+ 各语言 resolver(spring/fastapi/...)
    都在此跑。calls 边全局去重((source,target,kind)), 多 resolver 产同边时 **confidence 最高者
    赢**(专门 resolver 精确解析 conf=1.0 正确盖过 codegraph 兜底边 conf<1.0;并列时先注册者赢)。
    单 resolver 失败 fail-soft 不拖垮其余。
    """
    from codev_platform.graph.call_resolvers import applicable_resolvers

    merged = load_graph(conn, project_id)
    nodes = merged.nodes
    # 先收集所有 resolver 产的边(带来源名), 再按 key 选 winner —— 避免"先到先得"误丢高置信边。
    collected: list[tuple[str, GraphEdge]] = []
    by_resolver: dict[str, int] = {}
    for r in applicable_resolvers(repo_path, nodes):
        by_resolver.setdefault(r.name, 0)  # 跑过即登记(哪怕 0 边 / 抛错), 审计可见
        try:
            edges = r.resolve(repo_path, project_id, nodes)
        except Exception as exc:  # noqa: BLE001 — 单 resolver 失败不拖垮其余 + 整个 pass
            logger.warning("[calls] resolver %s failed: %r", r.name, exc)
            continue
        for e in edges:
            collected.append((r.name, e))
    # winner 选取: 同 (source,target,kind) 保留 confidence 最高者;并列不取代(严格 >),
    # 故 collected 的注册先后成为并列 tiebreak(codegraph 兜底先注册, 并列时兜底赢)。
    best: dict[tuple[str, str, str], tuple[str, GraphEdge]] = {}
    for name, e in collected:
        key = (e.source, e.target, e.kind)
        cur = best.get(key)
        if cur is None or e.confidence > cur[1].confidence:
            best[key] = (name, e)
    all_edges: list[GraphEdge] = [e for _, e in best.values()]
    for name, _ in best.values():
        by_resolver[name] += 1  # 计数 = 该 resolver 最终赢下的边数(被盖过的不计)
    upsert_result(conn, project_id, AnalyzerResult(edges=all_edges, plugin=CALLS_PLUGIN))
    report.ingested.append(CALLS_PLUGIN)
    report.summaries[CALLS_PLUGIN] = {"calls_edges": len(all_edges), "by_resolver": by_resolver}


def _frontend_deps_pass(conn, project_id: str, report: IngestReport, repo_path: Path) -> None:
    """前端组件依赖 post-pass: 接 dependency-cruiser 产 frontend_component 节点 + renders 边。

    框架无关(react .tsx / vue .vue 都吃, 自 detect 前端子目录)。fail-soft: 无 node/npx 或无
    前端 => 空, 不拖垮 ingest。详见 plugins/builtin/_stack_scan/frontend_deps.py。
    """
    from codev_platform.plugins.builtin._stack_scan import scan_frontend_deps

    nodes, edges = scan_frontend_deps(repo_path, project_id)
    upsert_result(
        conn, project_id,
        AnalyzerResult(nodes=nodes, edges=edges, plugin=FRONTEND_DEPS_PLUGIN),
    )
    report.ingested.append(FRONTEND_DEPS_PLUGIN)
    report.summaries[FRONTEND_DEPS_PLUGIN] = {
        "components": len(nodes), "renders_edges": len(edges),
    }


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
