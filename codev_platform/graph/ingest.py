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
    is_soft_node_kind,
    stamp_provenance,
    stamp_unprovenanced,
)
from codev_platform.graph.store import open_store
from codev_platform.graph.repo_scope import RepoScope
from codev_platform.plugins.builtin import _stack_scan
from codev_platform.plugins.registry import run_applicable
# "项目→仓根集合"解析在 core.repos 单一真值源(graph ingest 与 agent 文件工具共用)。
# 旧名 re-export 保持向后兼容(既有调用/测试不破)。
from codev_platform.core.repos import (
    meta_extra_repos as _meta_extra_repos,
    project_repo_roots,
    resolve_meta_extra_entries as _resolve_meta_extra_entries,
)

logger = logging.getLogger(__name__)

LINKER_PLUGIN = "builtin.linker"
CALLS_PLUGIN = "builtin.call_resolvers"  # 调用边(CALLS)统一归属: 多 resolver 去重后合并入此 plugin
FRONTEND_DEPS_PLUGIN = "builtin.frontend_deps"  # 前端组件依赖图(接 dependency-cruiser)
FRONTEND_BRIDGE_PLUGIN = "builtin.frontend_bridge"  # 前端内部桥: module(文件) -> 同文件 api_call/route
FRONTEND_API_USAGE_PLUGIN = "builtin.frontend_api_usage"  # 页面 -> url_registry 常量 精确 uses_api 边
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


def _resolve_repos(repo_path: Path | str, project_id: str,
                   extra_repos: list[str] | None) -> list[Path]:
    """多根仓列表: 主仓 + extra_repos。**同一逻辑项目**跨仓(前后端分离 / PDA 前端独立仓)一起
    ingest 进同一 store, _link_pass 据此跨仓连前端→后端。单仓项目零影响(无声明=只主仓)。

    extra_repos=None → 走 core.project_repo_roots(config 绝对路径 + meta.json 可移植声明合并);
    显式给 list → 仅用之(测试/直调), 去重 + 仅保留存在目录。
    """
    main = Path(repo_path).resolve()
    if extra_repos is None:
        return project_repo_roots(project_id, main_repo=main)
    repos = [main]
    for r in extra_repos:
        p = Path(r).expanduser()
        if p.is_dir() and p.resolve() not in {x.resolve() for x in repos}:
            repos.append(p.resolve())
    return repos


def _merge_result(merged: dict[str, AnalyzerResult], node_seen: dict[str, set[str]],
                  exec_result, report: IngestReport) -> None:
    """把一个插件执行结果按 plugin 累加进 merged(节点按 id 去重, 边 concat)+ 累加 summary。
    同 plugin 跨仓多次结果合并到一处 —— upsert 按 plugin 删插, 不合并会互相覆盖。"""
    ar = exec_result.result
    plug = exec_result.plugin
    acc = merged.setdefault(plug, AnalyzerResult(plugin=plug))
    seen = node_seen.setdefault(plug, set())
    for n in ar.nodes:
        if n.id not in seen:
            seen.add(n.id)
            acc.nodes.append(n)
    acc.edges.extend(ar.edges)
    summ = exec_result.summary or {}
    s = report.summaries.setdefault(plug, {"nodes": 0, "edges": 0})
    s["nodes"] += summ.get("nodes", len(ar.nodes))
    s["edges"] += summ.get("edges", len(ar.edges))


def _collect_merged(repos: list[Path], project_id: str, scope: RepoScope,
                    report: IngestReport) -> dict[str, AnalyzerResult]:
    """跑所有仓 × 适用插件 → 前端节点打仓维度(scope.localize 防跨仓碰撞)→ 按 plugin 合并跨仓产出。"""
    merged: dict[str, AnalyzerResult] = {}
    node_seen: dict[str, set[str]] = {}
    for repo in repos:
        for exec_result in run_applicable(repo, project_id):
            if exec_result.result is None:
                continue
            scope.localize(exec_result.result, repo)
            _merge_result(merged, node_seen, exec_result, report)
    return merged


def ingest_project(
    repo_path: Path | str,
    project_id: str,
    *,
    store_path: Path | None = None,
    extra_repos: list[str] | None = None,
) -> IngestReport:
    """对 repo(+ 多根 extra_repos)跑所有适用插件, 把产出灌进 project 的统一图谱 store。

    Args:
        repo_path:   主仓根路径。
        project_id:  图谱隔离键 (决定 store 文件)。
        store_path:  显式覆盖 store sqlite 路径 (测试用);None 走默认布局。
        extra_repos: 额外仓(多根; None=读 config projects.<pid>.extra_repos)。跨仓前后端连用。

    多根关键: upsert 按 plugin 删了再插 → 同插件跑多仓会互相覆盖。故**按 plugin 合并跨仓
    AnalyzerResult**(节点/边按 id 去重 concat)后再 upsert 一次。

    Returns:
        IngestReport — 哪些插件成功入库 + 各自计数。
    """
    repos = _resolve_repos(repo_path, project_id, extra_repos)
    report = IngestReport(project_id=project_id)
    store = open_store(project_id, path=store_path)
    # 多仓: 前端节点 id/file 以仓相对路径为锚、无仓维度 → 跨仓同相对路径文件会碰撞(merge first-wins
    # 静默丢后仓节点)。RepoScope 给前端节点打仓维度做仓内唯一化(主仓 tag='' no-op, 单仓零影响),
    # 写侧 localize 与读侧 resolve(api_usage 读盘)共用同一映射。详见 graph.repo_scope。
    scope = RepoScope(repos)
    try:
        # 跑所有仓 × 插件 + 前端节点打仓维度 + 按 plugin 合并跨仓产出, 然后逐 plugin 落库。
        for plug, ar in _collect_merged(repos, project_id, scope, report).items():
            store.upsert_result(project_id, ar)
            report.ingested.append(plug)

        # 核心 cross-plugin linker pass: 各插件落库后, 读回全量节点, 跨**所有**后端插件
        # (fastapi/spring/node) 把 frontend_api_call --calls_api--> backend_endpoint 连起来。
        # **多根关键**: 读 store 全量节点 → 自动跨仓连(extra_repo 前端 → 主仓后端)。
        _link_pass(store, project_id, report)

        # 调用边 post-pass: 仅主仓跑(call 边主要在后端=主仓; 按 plugin 删插 upsert 每仓调会互相
        # 覆盖)。前端依赖 post-pass: **所有根**跑(多前端各在不同仓, 内部累积一次写, 见函数注释)。
        main_repo = repos[0]
        _calls_pass(store, project_id, report, main_repo)
        _frontend_deps_pass(store, project_id, report, repos, scope)

        # 前端内部桥接 post-pass: 两个前端插件(frontend_deps 建 module / react 建 api_call/route)
        # 为同批文件建节点但 id 不相交、无边相连 → frontend_module 成孤岛(impact 滤软边后到不了
        # 后端)。按**文件**缝: module --contains--> 同文件 api_call/route(硬边, impact 也走), 打通
        # 前端页→api→endpoint→表 跨层链。必须在 _frontend_deps_pass(产 module)之后跑。
        _frontend_bridge_pass(store, project_id, report)

        # 前端 API 使用精确归因 post-pass: 页面 --uses_api--> 它源码真正引用的 url_registry 常量。
        # 必须在 url_registry(api_call)+ frontend_deps(component)+ bridge 之后。取代"页面 import
        # 共享注册模块 → 算调用其每个接口"的过报(impact 据 uses_api 精确, 见 build_impact_graph)。
        _frontend_api_usage_pass(store, project_id, report, repos, scope)

        # 综合分析 second post-pass: 硬骨架全部落库且连通后, analyzer 在其上归纳软节点/软边
        # (业务域等)。软产物 confidence<1.0 + referential-integrity 校验, 与硬骨架物理隔离。
        # 无注册 analyzer 时 no-op(A1-1 框架先行, LLM business_domain analyzer 待 A1-2)。
        # main_repo 传入: 需读源码的 analyzer(A3 前端 API 链接)经 set_context 拿主仓根。
        _analyzers_pass(store, project_id, report, main_repo)
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


def _frontend_deps_pass(store, project_id: str, report: IngestReport,
                        repos: list[Path], scope: RepoScope) -> None:
    """前端组件依赖 post-pass: 接 dependency-cruiser 产 frontend_component 节点 + imports 边。

    **对所有根(主仓 + extra_repos)各扫一遍, 合并后一次写入** —— 一个项目可挂多个独立前端
    (如 thorn6 web 在主仓 + PDA uni-app 在 extra 仓), 每个前端的内部组件依赖都要扫, 否则没扫到
    的那个前端组件全是孤点(只能靠软枢纽连)。按 plugin 删插 upsert, 故必须先跨仓累积再一次写,
    不能每仓各 upsert(会互相覆盖)。框架/结构无关(react/vue/uni-app 自 detect)。fail-soft。
    """
    from codev_platform.plugins.builtin._stack_scan import scan_frontend_deps

    all_edges: list[GraphEdge] = []
    all_nodes: list[GraphNode] = []
    for repo_path in repos:
        nodes, edges = scan_frontend_deps(repo_path, project_id)
        ar = AnalyzerResult(nodes=nodes, edges=edges, plugin=FRONTEND_DEPS_PLUGIN)
        scope.localize(ar, repo_path)
        all_nodes.extend(ar.nodes)
        all_edges.extend(ar.edges)
    # dependency-cruiser 真依赖图(AST 工具): src=ast
    stamp_unprovenanced(all_edges, ProvSource.AST, parser=FRONTEND_DEPS_PLUGIN)
    store.upsert_result(
        project_id,
        AnalyzerResult(nodes=all_nodes, edges=all_edges, plugin=FRONTEND_DEPS_PLUGIN),
    )
    report.ingested.append(FRONTEND_DEPS_PLUGIN)
    report.summaries[FRONTEND_DEPS_PLUGIN] = {
        "components": len(all_nodes), "imports_edges": len(all_edges),
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


def _frontend_api_usage_pass(store, project_id: str, report: IngestReport,
                             repos: list[Path], scope: RepoScope) -> None:
    """页面→api_call 精确 uses_api 边 post-pass —— 薄编排: 注入跨仓源码读取 + 委托解析引擎。

    解析逻辑在 `_stack_scan.api_usage`(策略式, 可扩展不同"使用模式": 当前 url_registry 常量引用;
    量化式服务方法调用是后续策略)。必须在 url_registry(产 api_call)+ frontend_deps(产 component/
    module)之后跑。无适用 api_call(纯内联项目)→ no-op。fail-soft: 解析异常只 warn 不拖垮 ingest。"""
    from codev_platform.plugins.builtin._stack_scan.api_usage import resolve_api_usage_edges

    def _read(file: str) -> str:
        # 多仓: file 可能带仓 tag(merge 期 RepoScope 加)→ resolve 还原到来源仓 + 真实相对路径(读对
        # 那个仓的源码, 不被同相对路径别仓文件串内容)。单仓 / 无 tag → (None, 原样) 回退遍历全仓。
        repo, rel = scope.resolve(file)
        for r in ([repo] if repo is not None else repos):
            p = r / rel
            if p.is_file():
                try:
                    return p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return ""
        return ""

    try:
        merged = store.load_graph(project_id)
        edges = resolve_api_usage_edges(merged.nodes, _read)
    except Exception as exc:  # noqa: BLE001 — 精确归因失败不拖垮基线 ingest
        logger.warning("[frontend_api_usage] pass failed (fail-soft): %r", exc)
        return
    stamp_unprovenanced(edges, ProvSource.REGEX, parser=FRONTEND_API_USAGE_PLUGIN)
    store.upsert_result(
        project_id, AnalyzerResult(edges=edges, plugin=FRONTEND_API_USAGE_PLUGIN))
    report.ingested.append(FRONTEND_API_USAGE_PLUGIN)
    report.summaries[FRONTEND_API_USAGE_PLUGIN] = {"uses_api_edges": len(edges)}


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


def _analyzers_pass(store, project_id: str, report: IngestReport,
                    repo_path: Path | None = None) -> None:
    """综合分析 second post-pass: 在连通硬骨架上跑 analyzer, 产软节点/软边(业务域等)。

    硬骨架(plugins + calls + frontend_deps)全部落库后才跑 —— analyzer 归纳需完整骨架。
    每个 analyzer 产出经 referential-integrity 校验(软边端点必须是真实硬节点, 悬空即丢)+
    软标记钳制(confidence<1.0), 再合并 upsert(plugin=ANALYZERS_PLUGIN, 重跑幂等替换)。
    无注册 analyzer 时写空 result(no-op, 清旧软产物);单 analyzer 失败 fail-soft 不拖垮其余。
    """
    from codev_platform.graph.analyzers import (
        applicable_analyzers,
        registered_analyzers,
        validate_soft_result,
    )

    merged = store.load_graph(project_id)
    hard_nodes = merged.nodes
    # referential-integrity 信任集只含**硬节点**: analyzer 仍读全量节点(hard_nodes)作上下文, 但产出的
    # 软边只有指向硬节点(或本轮自产软节点)才算有效。含软节点会让"引用别人上轮软节点"的悬空软边蒙混
    # 过校验持久化, 下轮 load 后悬空(2026-06-13 取证: A2 plays_role 指向 A3 inferred_api_call 软节点)。
    hard_ids = {n.id for n in hard_nodes if not is_soft_node_kind(n.kind)}
    soft_nodes: list[GraphNode] = []
    soft_edges: list[GraphEdge] = []
    by_analyzer: dict[str, int] = {}
    # 需读源码的 analyzer(A3)经 set_context 拿 repo_path(其他 analyzer 无此方法, 跳过)。
    # 必须在 applicable_analyzers(它 applies 里查 repo)之前注入。
    if repo_path is not None:
        for a in registered_analyzers():
            setter = getattr(a, "set_context", None)
            if callable(setter):
                try:
                    setter(repo_path)
                except Exception:  # noqa: BLE001 — 注入失败不拖垮 pass
                    pass
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
