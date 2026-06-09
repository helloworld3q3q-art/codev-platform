"""软标签质量诊断 —— A1 业务域 / A2 架构层软标签的健康度体检(确定性, 不调 LLM)。

三轴正交分工:
- `audit.py`  管**结构对不对**(断链 / 串台 / 重复 / 低置信硬边)。
- `eval`      管**标签对不对**(对人工 golden 算准确率, 需真值)。
- 本模块      管**标签好不好**(分布 / 覆盖 / 退化)—— **无需 golden, 任意项目可跑**,
              抓 LLM 软标签的**结构性退化信号**:

  - giant_cluster:  单个域 / 层吃掉过高比例成员 —— 共享节点(如热点表)致"巨型 cluster"
                    退化(见教训: A1 用 file 主信号前 58%→95%)。软标签可信度的首要红旗。
  - low_coverage:   可标注硬节点里真拿到标签的比例过低(analyzer 漏标多, 软层稀疏)。
  - singletons:     只有 ≤1 成员的域 / 层(噪声 / 弱标签)。

业务域与架构层**结构同构**(软节点 + 反向软边 → 成员), 故用一个 `_assess_axis` 复用两轴。
纯读已落库软节点 / 软边, 计数 + 阈值判定, 不调 LLM、不写 store。
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from codev_platform.graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind
from codev_platform.graph.store import load_graph

# 默认阈值(可由调用方覆盖)。
_GIANT_SHARE = 0.5      # 单 cluster 成员占比 > 此 = 巨型(疑似共享节点退化)
_MIN_COVERAGE = 0.3     # 可标注硬节点的标注覆盖率 < 此 = 偏低
_TOP_DIST = 10          # markdown 分布预览取前 N

# 各轴"可被标注的硬节点 kind"(coverage 分母)。A1 标 endpoint/表; A2 标代码节点。
_DOMAIN_ELIGIBLE = frozenset({NodeKind.BACKEND_ENDPOINT.value, NodeKind.DB_TABLE.value})
_LAYER_ELIGIBLE = frozenset({
    NodeKind.BACKEND_FUNCTION.value, NodeKind.BACKEND_ENDPOINT.value,
    NodeKind.FRONTEND_MODULE.value, NodeKind.FRONTEND_COMPONENT.value,
})


def _assess_axis(nodes: list[GraphNode], edges: list[GraphEdge], *,
                 soft_kind: str, soft_edge_kind: str, eligible_kinds: frozenset[str],
                 giant_share: float, min_coverage: float) -> dict:
    """评一条软标签轴(业务域 / 架构层 同构): 分布 + 覆盖 + 退化信号。

    成员 = 反向软边(hard --soft_edge--> soft_node)的源端硬节点。
    **giant / share 按"文件单位"算(同文件多节点去重)**, 不按节点边数 —— 否则 A2 节点级 +
    method/列密集文件(Mapper 多方法 / 宽表多列)会把一个角色占比线性放大成假阳性(2026-06-09
    四轮取证: openclaw repository 成员口径 64% 但文件口径仅 19%, 79% 成员是 db_column/db_table)。
    无 file 的节点按自身 id 计(不丢)。**singleton 仍按节点数**(1 节点 = 噪声, 与密度无关)。
    """
    node_by_id = {n.id: n for n in nodes}
    soft_id_to_name = {n.id: n.name for n in nodes if n.kind == soft_kind}
    units_by_soft: dict[str, set[str]] = defaultdict(set)    # 文件单位(去重)
    members_by_soft: dict[str, set[str]] = defaultdict(set)  # 原始节点(展示)
    labeled_hard: set[str] = set()
    for e in edges:
        if e.kind == soft_edge_kind and e.target in soft_id_to_name:
            src = node_by_id.get(e.source)
            units_by_soft[e.target].add(src.file if src and src.file else e.source)
            members_by_soft[e.target].add(e.source)
            labeled_hard.add(e.source)

    total = sum(len(u) for u in units_by_soft.values())   # 文件单位总数(share 分母)
    dist = sorted(
        ({"name": soft_id_to_name[sid],
          "members": len(members_by_soft[sid]),            # 节点数(展示, 含密度)
          "files": len(u),                                  # 文件单位数
          "share": (len(u) / total) if total else 0.0}      # 占比按文件单位
         for sid, u in units_by_soft.items()),
        key=lambda d: (-d["files"], -d["members"], d["name"]),
    )
    # giant: 一个 cluster 占多数**文件单位**(>阈值)且跨 >1 文件 → 真分层/聚类退化
    # (按文件不被方法/列密度误报)。需 ≥2 cluster 才相对有意义(单 cluster 占 100% 正常)。
    giant = ([d for d in dist if d["files"] > 1 and d["share"] > giant_share]
             if len(dist) >= 2 else [])
    singletons = [d["name"] for d in dist if d["members"] <= 1]  # 1 节点 = 噪声(节点口径)

    eligible_ids = {n.id for n in nodes if n.kind in eligible_kinds}
    coverage = (len(labeled_hard & eligible_ids) / len(eligible_ids)) if eligible_ids else None

    soft_count = len(soft_id_to_name)
    # low_coverage 只在**有软层**时才成立 —— 软节点为 0 = analyzer 没跑(不是退化),
    # 不能因"没标"就报覆盖低(否则任何没跑 analyzer 的项目都假阳性)。
    return {
        "soft_nodes": soft_count,
        "labeled_members": sum(len(m) for m in members_by_soft.values()),  # 节点口径(展示)
        "labeled_files": total,                                            # 文件单位口径
        "eligible": len(eligible_ids),
        "coverage": coverage,
        "distribution": dist,
        "giant": giant,
        "singletons": singletons,
        "low_coverage": soft_count > 0 and coverage is not None and coverage < min_coverage,
    }


def _axis_flags(label: str, ax: dict, *, min_coverage: float) -> list[str]:
    """把一条轴的退化信号翻成人类可读 flag(空 = 健康)。"""
    flags: list[str] = []
    for g in ax["giant"]:
        flags.append(f"{label}: 巨型 cluster「{g['name']}」占 {g['share']:.0%} 文件"
                     f"({g['files']} 文件 / {g['members']} 节点; 一层或域吞掉多数文件, 疑分层/聚类退化)")
    if ax["low_coverage"]:
        flags.append(f"{label}: 覆盖率 {ax['coverage']:.0%} 偏低(< {min_coverage:.0%}, "
                     "analyzer 漏标多 / 软层稀疏)")
    # 单成员域占多数 → 多为噪声/弱标签。
    if ax["soft_nodes"] >= 4 and len(ax["singletons"]) * 2 >= ax["soft_nodes"]:
        flags.append(f"{label}: {len(ax['singletons'])}/{ax['soft_nodes']} 个为单成员"
                     "(噪声/弱标签偏多)")
    return flags


def assess_soft_labels(conn: sqlite3.Connection, project_id: str, *,
                       giant_share: float = _GIANT_SHARE,
                       min_coverage: float = _MIN_COVERAGE) -> dict:
    """软标签健康度诊断。返回两轴指标 + flags + healthy 布尔(无 flag = healthy)。

    无软层(未跑 analyzer)→ 两轴空 + healthy True(不报假阳性, 没标就没退化)。
    """
    g = load_graph(conn, project_id)
    domains = _assess_axis(
        g.nodes, g.edges, soft_kind=NodeKind.BUSINESS_DOMAIN.value,
        soft_edge_kind=EdgeKind.BELONGS_TO_DOMAIN.value, eligible_kinds=_DOMAIN_ELIGIBLE,
        giant_share=giant_share, min_coverage=min_coverage)
    layers = _assess_axis(
        g.nodes, g.edges, soft_kind=NodeKind.ARCH_LAYER.value,
        soft_edge_kind=EdgeKind.PLAYS_ROLE.value, eligible_kinds=_LAYER_ELIGIBLE,
        giant_share=giant_share, min_coverage=min_coverage)

    flags = (_axis_flags("业务域", domains, min_coverage=min_coverage)
             + _axis_flags("架构层", layers, min_coverage=min_coverage))
    return {
        "project_id": project_id,
        "thresholds": {"giant_share": giant_share, "min_coverage": min_coverage},
        "domains": domains,
        "layers": layers,
        "flags": flags,
        "healthy": not flags,
    }


def _render_axis(label: str, ax: dict) -> list[str]:
    cov = "n/a" if ax["coverage"] is None else f"{ax['coverage']:.0%}"
    lines = [
        f"## {label}",
        f"- 软节点 {ax['soft_nodes']} / 文件 {ax['labeled_files']}(节点成员 {ax['labeled_members']}) / "
        f"覆盖 {cov}(可标注 {ax['eligible']})",
    ]
    for d in ax["distribution"][:_TOP_DIST]:
        lines.append(f"    - {d['name']}: {d['files']} 文件 / {d['members']} 节点 ({d['share']:.0%})")
    if len(ax["distribution"]) > _TOP_DIST:
        lines.append(f"    - … 另 {len(ax['distribution']) - _TOP_DIST} 个")
    return lines


def render_markdown(report: dict) -> str:
    """渲染软标签诊断报告为可读 markdown。"""
    verdict = "✅ healthy" if report["healthy"] else f"⚠️ {len(report['flags'])} 个信号"
    lines = [f"# graph soft-quality — {report['project_id']}", "", f"- 结论: {verdict}", ""]
    if report["flags"]:
        lines.append("## 退化信号")
        lines += [f"- {f}" for f in report["flags"]]
        lines.append("")
    lines += _render_axis("业务域 (A1)", report["domains"])
    lines += [""]
    lines += _render_axis("架构层 (A2)", report["layers"])
    return "\n".join(lines)
