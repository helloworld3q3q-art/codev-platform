"""ingest 路径 (Phase 3 起步) —— 跑插件 -> 落统一图谱 store。

把 plugins.registry.run_applicable 的产出灌进 graph.store:对一个仓库执行所有适用
且成功的 analyzer 插件,逐个把 AnalyzerResult upsert 进该 project 的统一图谱 sqlite。

执行隔离继承自 registry/executor:单插件 detect/analyze 崩溃只会被滤掉 (不进
run_applicable 的成功列表),不影响其余插件入库。store 写入按 plugin 归属幂等替换,
重跑 ingest 不产生重复。

不改 codegraph / 现有 graph 路由 —— 只新增这条聚合落盘路径。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    ProvSource,
    stamp_provenance,
    stamp_unprovenanced,
)
from codev_platform.graph.store import open_store
from codev_platform.plugins.builtin import _stack_scan
from codev_platform.plugins.registry import run_applicable

logger = logging.getLogger(__name__)

LINKER_PLUGIN = "builtin.linker"
CALLS_PLUGIN = "builtin.call_resolvers"  # 调用边(CALLS)统一归属: 多 resolver 去重后合并入此 plugin
FRONTEND_DEPS_PLUGIN = "builtin.frontend_deps"  # 前端组件依赖图(接 dependency-cruiser)
FRONTEND_BRIDGE_PLUGIN = "builtin.frontend_bridge"  # 前端内部桥: module(文件) -> 同文件 api_call/route
ANALYZERS_PLUGIN = "builtin.analyzers"  # 综合分析器(软节点/软边: 业务域等)统一归属


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
    store = open_store(project_id, path=store_path)
    try:
        for exec_result in results:
            analyzer_result = exec_result.result
            if analyzer_result is None:  # run_applicable 只回 ok=True, 兜底保险
                continue
            store.upsert_result(project_id, analyzer_result)
            report.ingested.append(exec_result.plugin)
            report.summaries[exec_result.plugin] = exec_result.summary

        # 核心 cross-plugin linker pass: 各插件落库后, 读回全量节点, 跨**所有**后端插件
        # (fastapi/spring/node) 把 frontend_api_call --calls_api--> backend_endpoint 连起来。
        # 这是 calls_api 的**唯一** owner (前端插件不再各自只链同仓 FastAPI), 解决前端
        # 链不到 Java/Spring 端点的缺口。挂 builtin.linker, upsert 幂等可重跑。
        _link_pass(store, project_id, report)

        # 调用边 post-pass: 跑所有适用 CallResolver(codegraph 兜底 + 未来各语言栈 resolver)把
        # endpoint→function / 函数→函数 calls 边物化, store 自成连通解锁影响分析。按语言栈
        # 可扩展(graph/call_resolvers/), 全局去重, fail-soft。
        _calls_pass(store, project_id, report, Path(repo_path))

        # 前端组件依赖 post-pass: 接 dependency-cruiser(读 tsconfig paths 解 @/ alias)产
        # frontend_component 节点 + renders 边, 解锁"改组件→影响哪些页面"(codegraph 盲区)。
        # 框架无关(react .tsx + vue .vue 都吃), 自 detect, fail-soft 无 node/前端则空。
        _frontend_deps_pass(store, project_id, report, Path(repo_path))

        # 前端内部桥接 post-pass: 两个前端插件(frontend_deps 建 module / react 建 api_call/route)
        # 为同批文件建节点但 id 不相交、无边相连 → frontend_module 成孤岛(impact 滤软边后到不了
        # 后端)。按**文件**缝: module --contains--> 同文件 api_call/route(硬边, impact 也走), 打通
        # 前端页→api→endpoint→表 跨层链。必须在 _frontend_deps_pass(产 module)之后跑。
        _frontend_bridge_pass(store, project_id, report)

        # 综合分析 second post-pass: 硬骨架全部落库且连通后, analyzer 在其上归纳软节点/软边
        # (业务域等)。软产物 confidence<1.0 + referential-integrity 校验, 与硬骨架物理隔离。
        # 无注册 analyzer 时 no-op(A1-1 框架先行, LLM business_domain analyzer 待 A1-2)。
        _analyzers_pass(store, project_id, report)
    finally:
        store.close()
    return report


def _calls_pass(store, project_id: str, report: IngestReport, repo_path: Path) -> None:
    """调用边 post-pass: 跑所有适用 CallResolver(按语言栈)→ 全局去重 → upsert 统一 plugin。

    取代原 _bridge_pass: codegraph resolver(原桥接逻辑)+ 各语言 resolver(spring/fastapi/...)
    都在此跑。calls 边全局去重((source,target,kind)), 多 resolver 产同边时 **confidence 最高者
    赢**(专门 resolver 精确解析 conf=1.0 正确盖过 codegraph 兜底边 conf<1.0;并列时先注册者赢)。
    单 resolver 失败 fail-soft 不拖垮其余。
    """
    from codev_platform.graph.call_resolvers import applicable_resolvers

    merged = store.load_graph(project_id)
    nodes = merged.nodes
    # 先收集所有 resolver 产的边(带来源名 + provenance 来源类), 再按 key 选 winner ——
    # 避免"先到先得"误丢高置信边。prov_source 由各 resolver 声明(codegraph=ast 精确解析,
    # fastapi=regex 名称 BFS); 未声明者保守视作 regex(候选), 让影响分析默认不当确定依赖。
    collected: list[tuple[str, str, GraphEdge]] = []
    by_resolver: dict[str, int] = {}
    for r in applicable_resolvers(repo_path, nodes):
        by_resolver.setdefault(r.name, 0)  # 跑过即登记(哪怕 0 边 / 抛错), 审计可见
        prov_src = getattr(r, "prov_source", ProvSource.REGEX.value)
        try:
            edges = r.resolve(repo_path, project_id, nodes)
        except Exception as exc:  # noqa: BLE001 — 单 resolver 失败不拖垮其余 + 整个 pass
            logger.warning("[calls] resolver %s failed: %r", r.name, exc)
            continue
        for e in edges:
            collected.append((r.name, prov_src, e))
    # winner 选取: 同 (source,target,kind) 保留 confidence 最高者;并列不取代(严格 >),
    # 故 collected 的注册先后成为并列 tiebreak(codegraph 兜底先注册, 并列时兜底赢)。
    best: dict[tuple[str, str, str], tuple[str, str, GraphEdge]] = {}
    for name, prov_src, e in collected:
        key = (e.source, e.target, e.kind)
        cur = best.get(key)
        if cur is None or e.confidence > cur[2].confidence:
            best[key] = (name, prov_src, e)
    all_edges: list[GraphEdge] = []
    for name, prov_src, e in best.values():
        stamp_provenance(e, prov_src, parser=name)  # 盖来源戳(src=ast/regex, parser=resolver 名)
        all_edges.append(e)
        by_resolver[name] += 1  # 计数 = 该 resolver 最终赢下的边数(被盖过的不计)
    store.upsert_result(project_id, AnalyzerResult(edges=all_edges, plugin=CALLS_PLUGIN))
    report.ingested.append(CALLS_PLUGIN)
    report.summaries[CALLS_PLUGIN] = {"calls_edges": len(all_edges), "by_resolver": by_resolver}


def _frontend_deps_pass(store, project_id: str, report: IngestReport, repo_path: Path) -> None:
    """前端组件依赖 post-pass: 接 dependency-cruiser 产 frontend_component 节点 + renders 边。

    框架无关(react .tsx / vue .vue 都吃, 自 detect 前端子目录)。fail-soft: 无 node/npx 或无
    前端 => 空, 不拖垮 ingest。详见 plugins/builtin/_stack_scan/frontend_deps.py。
    """
    from codev_platform.plugins.builtin._stack_scan import scan_frontend_deps

    nodes, edges = scan_frontend_deps(repo_path, project_id)
    # dependency-cruiser 真依赖图(AST 工具): src=ast
    stamp_unprovenanced(edges, ProvSource.AST, parser=FRONTEND_DEPS_PLUGIN)
    store.upsert_result(
        project_id,
        AnalyzerResult(nodes=nodes, edges=edges, plugin=FRONTEND_DEPS_PLUGIN),
    )
    report.ingested.append(FRONTEND_DEPS_PLUGIN)
    report.summaries[FRONTEND_DEPS_PLUGIN] = {
        "components": len(nodes), "imports_edges": len(edges),
    }


_FRONTEND_CHILD_KINDS = (NodeKind.FRONTEND_API_CALL.value, NodeKind.FRONTEND_ROUTE.value)


def build_frontend_bridge_edges(nodes: list[GraphNode]) -> list[GraphEdge]:
    """纯函数: frontend_module(文件) --contains--> 同文件的 api_call/route 节点。

    确定性文件匹配(dependency-cruiser 文件级, 一文件一 module 节点), conf=1.0 硬边。
    同 file 的 module 与 react 子节点缝合, 把 import 岛接进 api_call→endpoint 跨层链。
    无 IO → 可脱离 store 单测。
    """
    mod_by_file: dict[str, str] = {}
    for n in nodes:
        if n.kind == NodeKind.FRONTEND_MODULE.value and n.file:
            mod_by_file.setdefault(n.file, n.id)   # 一文件一 module; 取首个稳定
    edges: list[GraphEdge] = []
    seen: set[tuple[str, str]] = set()
    for n in nodes:
        if n.kind not in _FRONTEND_CHILD_KINDS or not n.file:
            continue
        mid = mod_by_file.get(n.file)
        if mid is None or mid == n.id or (mid, n.id) in seen:
            continue
        seen.add((mid, n.id))
        e = GraphEdge(source=mid, target=n.id,
                      kind=EdgeKind.CONTAINS.value, confidence=1.0)
        stamp_provenance(e, ProvSource.BRIDGE, parser=FRONTEND_BRIDGE_PLUGIN)
        edges.append(e)
    return edges


def _frontend_bridge_pass(store, project_id: str, report: IngestReport) -> None:
    """读全量节点 → 建 frontend_module→api_call/route 的 contains 硬边 → upsert builtin.frontend_bridge。"""
    merged = store.load_graph(project_id)
    edges = build_frontend_bridge_edges(merged.nodes)
    store.upsert_result(
        project_id, AnalyzerResult(edges=edges, plugin=FRONTEND_BRIDGE_PLUGIN)
    )
    report.ingested.append(FRONTEND_BRIDGE_PLUGIN)
    report.summaries[FRONTEND_BRIDGE_PLUGIN] = {"contains_edges": len(edges)}


def _link_pass(store, project_id: str, report: IngestReport) -> None:
    """跨插件链接: 读 store 全量节点 -> calls_api 边 -> upsert builtin.linker。"""
    merged = store.load_graph(project_id)
    frontend = [
        n for n in merged.nodes if n.kind == NodeKind.FRONTEND_API_CALL.value
    ]
    backend = [
        n for n in merged.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value
    ]
    edges = _stack_scan.link_api_calls(frontend, backend)
    for e in edges:  # 框架语义适配: frontend_api_call→endpoint, src=framework
        stamp_provenance(e, ProvSource.FRAMEWORK, parser=LINKER_PLUGIN)
    store.upsert_result(
        project_id, AnalyzerResult(edges=edges, plugin=LINKER_PLUGIN)
    )
    report.ingested.append(LINKER_PLUGIN)
    report.summaries[LINKER_PLUGIN] = {"calls_api_edges": len(edges)}


def _analyzers_pass(store, project_id: str, report: IngestReport) -> None:
    """综合分析 second post-pass: 在连通硬骨架上跑 analyzer, 产软节点/软边(业务域等)。

    硬骨架(plugins + calls + frontend_deps)全部落库后才跑 —— analyzer 归纳需完整骨架。
    每个 analyzer 产出经 referential-integrity 校验(软边端点必须是真实硬节点, 悬空即丢)+
    软标记钳制(confidence<1.0), 再合并 upsert(plugin=ANALYZERS_PLUGIN, 重跑幂等替换)。
    无注册 analyzer 时写空 result(no-op, 清旧软产物);单 analyzer 失败 fail-soft 不拖垮其余。
    """
    from codev_platform.graph.analyzers import (
        applicable_analyzers,
        validate_soft_result,
    )

    merged = store.load_graph(project_id)
    hard_nodes = merged.nodes
    hard_ids = {n.id for n in hard_nodes}
    soft_nodes: list[GraphNode] = []
    soft_edges: list[GraphEdge] = []
    by_analyzer: dict[str, int] = {}
    for a in applicable_analyzers(hard_nodes):
        by_analyzer.setdefault(a.name, 0)  # 跑过即登记(哪怕 0 产出 / 抛错), 审计可见
        try:
            raw = a.analyze(project_id, hard_nodes, merged.edges)
        except Exception as exc:  # noqa: BLE001 — 单 analyzer 失败不拖垮其余 + 整个 pass
            logger.warning("[analyzers] %s failed: %r", a.name, exc)
            continue
        clean = validate_soft_result(raw, hard_ids)  # 悬空软边丢弃 + 软标记钳制
        soft_nodes.extend(clean.nodes)
        soft_edges.extend(clean.edges)
        by_analyzer[a.name] += len(clean.nodes)
    store.upsert_result(
        project_id,
        AnalyzerResult(nodes=soft_nodes, edges=soft_edges, plugin=ANALYZERS_PLUGIN),
    )
    report.ingested.append(ANALYZERS_PLUGIN)
    report.summaries[ANALYZERS_PLUGIN] = {
        "soft_nodes": len(soft_nodes), "soft_edges": len(soft_edges),
        "by_analyzer": by_analyzer,
    }
