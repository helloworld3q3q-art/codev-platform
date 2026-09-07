"""结构社区检测算法纯核(Phase 4)—— 纯 stdlib 确定性 Louvain。

定位(functional core): 给定节点 + 边, 算出每个节点的结构社区。**无 IO、无 networkx、无
random** → 可脱离 store 单测, 且重跑同图必产同结果(Phase 5 路径评分稳定排序依赖此)。

与 A1 business_domain 的 union-find 同纪律(sorted 遍历 + min-id tie-break 消除随机源),
但解决不同问题: union-find 是"按规则合并"(file/共享表), Louvain 是"模块度优化"找结构社区,
覆盖**全节点**(前端组件/函数/模块/符号), 不止 endpoint/表。

算法 = 标准 Louvain 两阶段迭代:
  1. 本地移动: 每个节点尝试移入邻居社区, 取模块度增益最大者(min-id tie-break), 直到无移动。
  2. 社区聚合: 每个社区缩成超级节点(自环承载社区内边权), 在聚合图上递归阶段 1。
  直到某轮无改进。无向图、首版等权 1.0。

确定性要点(逐项消除随机源):
  - 节点 / 邻居社区遍历一律 `sorted`(节点 id 是 `<pid>:<kind>:<key>` 字符串, 字典序稳定)。
  - 增益并列(差 < _EPS)时取**社区代表 id 最小**者(对齐 A1 _UnionFind min-id 作根)。
  - 社区 id = 成员节点最小 id(重跑同图同 id)。无 `random` import。max_passes 封顶防病态。
"""
from __future__ import annotations

import logging

from codev_platform.graph.schema import GraphEdge, GraphNode, is_soft_node_kind

logger = logging.getLogger(__name__)

_EPS = 1e-12            # 模块度增益浮点容差(差小于此视作并列, 走 min-id tie-break)
_MAX_NODES = 20000     # 规模护栏: 超量 fail-soft 跳过(同 A1 max_clusters 纪律)
_MAX_PASSES = 20       # Louvain 外层迭代上限(防病态不收敛)


def detect_communities(
    nodes: list[GraphNode], edges: list[GraphEdge], *,
    max_nodes: int = _MAX_NODES,
) -> dict[str, str]:
    """全节点结构社区划分。返回 {node_id: community_id}(community_id = 该社区成员最小 node id)。

    只对**硬节点 + 硬边**跑(软产物由调用方过滤后传入或本函数兜底剔除): 社区是确定性血缘的
    结构聚类, 混入 LLM 软边会把已退化的连通分量再次粘连。孤立节点 → 自成社区(id=自身)。
    超 max_nodes → 返回 {} (fail-soft, 调用方告警跳过)。无边 → 全节点各自单独社区。
    """
    ids = sorted(n.id for n in nodes if not is_soft_node_kind(n.kind))
    if not ids:
        return {}
    if len(ids) > max_nodes:
        logger.warning("[community] %d nodes > cap %d, skip", len(ids), max_nodes)
        return {}
    id_set = set(ids)

    # 无向加权邻接(同一对多边累加)+ 自环(初始 0; 聚合阶段承载社区内边权)。
    adj: dict[str, dict[str, float]] = {i: {} for i in ids}
    loops: dict[str, float] = {i: 0.0 for i in ids}
    for e in edges:
        a, b = e.source, e.target
        if a not in id_set or b not in id_set or a == b:
            continue
        adj[a][b] = adj[a].get(b, 0.0) + 1.0
        adj[b][a] = adj[b].get(a, 0.0) + 1.0

    # 第 0 层: 每节点自成社区。逐层 Louvain, 把每层划分**折叠**回原始节点。
    partition = {i: i for i in ids}        # 原始节点 → 当前层超级节点(代表 = 成员最小 id)
    while True:
        comm = _one_level(adj, loops)      # 当前层节点 → 社区 label(label 是某成员 id, 未必最小)
        if len(set(comm.values())) == len(adj):  # 社区数 == 节点数 → 本层无合并 → 收敛
            break
        # label 规范化成 rep(成员最小 id), 保证聚合图键与折叠键一致(否则折叠 KeyError)。
        groups: dict[str, list[str]] = {}
        for cur, lab in comm.items():
            groups.setdefault(lab, []).append(cur)
        rep = {lab: min(mem) for lab, mem in groups.items()}
        comm_rep = {cur: rep[lab] for cur, lab in comm.items()}   # 当前层节点 → rep
        partition = {orig: comm_rep[cur] for orig, cur in partition.items()}
        adj, loops = _aggregate(adj, loops, comm_rep)
        if len(adj) <= 1:                  # 全图缩成一个社区, 不必再迭代
            break

    # 归一社区 id = 成员最小原始 node id(确定性、可读)。
    members: dict[str, list[str]] = {}
    for orig, c in partition.items():
        members.setdefault(c, []).append(orig)
    out: dict[str, str] = {}
    for _community, mem in members.items():
        cid = min(mem)
        for orig in mem:
            out[orig] = cid
    return out


def _one_level(adj: dict[str, dict[str, float]],
               loops: dict[str, float]) -> dict[str, str]:
    """Louvain 本地移动: 返回 {node: community}(社区代表 = 节点 id, 初始各自为政)。

    每节点 = `sum(邻边权) + 2×自环`(模块度 k_i 自环计两次)。增益公式(标准 Louvain):
    把孤立 i 移入社区 C 的增益 ∝ k_i_in − Σtot(C)×k_i / 2m。argmax 取社区, 并列取 min-id。"""
    deg = {n: sum(adj[n].values()) + 2.0 * loops[n] for n in adj}
    m2 = sum(deg.values())                 # = 2m(总边权两倍)
    comm = {n: n for n in adj}
    tot = {n: deg[n] for n in adj}         # 社区 → 成员度之和
    if m2 <= 0:                            # 无边: 全孤立, 各自社区
        return comm

    moved = True
    passes = 0
    while moved and passes < _MAX_PASSES:
        moved = False
        passes += 1
        for n in sorted(adj):              # 确定性遍历序
            cn = comm[n]
            # n 到各邻居社区的边权和
            nbr_comm: dict[str, float] = {}
            for nbr, w in adj[n].items():
                c = comm[nbr]
                nbr_comm[c] = nbr_comm.get(c, 0.0) + w
            tot[cn] -= deg[n]              # 先把 n 从原社区摘出
            best_c = cn
            best_gain = nbr_comm.get(cn, 0.0) - tot[cn] * deg[n] / m2
            for c in sorted(nbr_comm):     # 确定性候选序
                gain = nbr_comm[c] - tot[c] * deg[n] / m2
                if gain > best_gain + _EPS or (abs(gain - best_gain) <= _EPS and c < best_c):
                    best_gain, best_c = gain, c
            tot[best_c] += deg[n]
            if best_c != cn:
                comm[n] = best_c
                moved = True
    return comm


def modularity(nodes: list[GraphNode], edges: list[GraphEdge],
               mapping: dict[str, str]) -> float:
    """给定划分的模块度 Q ∈ [-0.5, 1.0](越高 = 社区内密、社区间疏 = 划分越好)。

    诊断指标(不卡硬阈值: Q 随图密度浮动, 稀疏血缘图 Q 偏低属正常)。无向等权口径与
    detect_communities 一致。mapping 未覆盖的节点 → 各自单点社区(真实划分)。无边 → 0.0。
    Q = Σ_c [ Σ_in(c)/2m − (Σ_tot(c)/2m)² ];Σ_in 按有序对(=2×社区内边权)。
    """
    ids = sorted(n.id for n in nodes if not is_soft_node_kind(n.kind))
    id_set = set(ids)
    comm = {i: mapping.get(i, i) for i in ids}      # 未分配 → 自成单点社区
    deg = {i: 0.0 for i in ids}
    in_w: dict[str, float] = {}
    m2 = 0.0
    for e in edges:
        a, b = e.source, e.target
        if a not in id_set or b not in id_set or a == b:
            continue
        deg[a] += 1.0
        deg[b] += 1.0
        m2 += 2.0
        if comm[a] == comm[b]:
            in_w[comm[a]] = in_w.get(comm[a], 0.0) + 2.0
    if m2 <= 0:
        return 0.0
    tot: dict[str, float] = {}
    for i in ids:
        tot[comm[i]] = tot.get(comm[i], 0.0) + deg[i]
    return sum(in_w.get(c, 0.0) / m2 - (tot[c] / m2) ** 2 for c in tot)


def _aggregate(adj: dict[str, dict[str, float]], loops: dict[str, float],
               comm_rep: dict[str, str]) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """把社区缩成超级节点(键 = comm_rep 给的代表 id)。社区内边 → 新自环; 社区间边 → 新邻接。"""
    labels = sorted(set(comm_rep.values()))
    new_adj: dict[str, dict[str, float]] = {label: {} for label in labels}
    new_loops: dict[str, float] = {label: 0.0 for label in labels}
    for n in adj:
        rn = comm_rep[n]
        new_loops[rn] += loops[n]
        for nbr, w in adj[n].items():
            rm = comm_rep[nbr]
            if rn == rm:
                new_loops[rn] += w / 2.0   # 社区内边被 n、nbr 两端各记一次 → /2 合成 w
            else:
                new_adj[rn][rm] = new_adj[rn].get(rm, 0.0) + w
    return new_adj, new_loops
