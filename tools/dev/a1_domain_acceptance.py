"""A1-3 业务域映射验收 —— 用真实 LLM(brain provider)验证 ≥70% 准确率。

两模式:
  --smoke           内置真实业务 cluster 样例冒烟(验证 LLM 通路 + prompt 渲染 + 解析)
  --project <pid>   对该项目 graph store 跑业务域标注, 导出 endpoint|域|待判 表

key 红线: **不写进脚本、不进 git**。经 env(如 DEEPSEEK_API_KEY)或 config.agent.providers
.<name>.api_key 解析(registry.resolve_key: env > config)。provider 由 --provider 选(默认 deepseek)。

用法:
  $env:DEEPSEEK_API_KEY='sk-...'; python tools/dev/a1_domain_acceptance.py --smoke
  $env:DEEPSEEK_API_KEY='sk-...'; python tools/dev/a1_domain_acceptance.py --project codev-platform
"""
from __future__ import annotations

import argparse
import sys


def _cfg(provider: str, model: str) -> dict:
    # key 不在此 —— 靠 env(resolve_key 优先 env)。model 空则用 spec 默认。
    prov: dict = {}
    if model:
        prov["model"] = model
    return {"agent": {"provider": provider, "providers": {provider: prov}}}


# 内置真实业务 cluster 样例(人对照: 期望域)。
_SMOKE = [
    ("c_order", "订单", [("e1", "endpoint", "POST /api/orders"),
                         ("e2", "endpoint", "GET /api/orders/{id}"),
                         ("t1", "table", "orders"), ("t2", "table", "order_items")]),
    ("c_quote", "行情", [("e1", "endpoint", "GET /api/quotes/daily"),
                         ("e2", "endpoint", "GET /api/quotes/realtime"),
                         ("t1", "table", "stock_quote_daily")]),
    ("c_auth", "用户鉴权", [("e1", "endpoint", "POST /api/login"),
                            ("e2", "endpoint", "POST /api/logout"),
                            ("t1", "table", "sys_user")]),
]


def run_smoke(provider: str, model: str) -> int:
    from codev_platform.graph.analyzers.brain_labeler import BrainDomainLabeler
    from codev_platform.graph.analyzers.domain_labeler import ClusterMember, ClusterRequest

    labeler = BrainDomainLabeler(_cfg(provider, model))
    if not labeler.available():
        print(f"FATAL: provider '{provider}' 不可用(缺 key 或 agent extra?)。"
              f"设 env <PROVIDER>_API_KEY; sig={labeler.signature}")
        return 1
    reqs = [ClusterRequest(cid, tuple(ClusterMember(*m) for m in members))
            for cid, _exp, members in _SMOKE]
    labels = labeler.label(reqs)
    by_cid = {lab.cluster_id: lab for lab in labels}
    print(f"provider={provider}  signature={labeler.signature}\n")
    hit = 0
    for cid, exp, _m in _SMOKE:
        lab = by_cid.get(cid)
        got = lab.domain if lab else None
        mark = "OK" if got else "--"
        print(f"  [{mark}] {cid}: 期望≈{exp!r}  模型={got!r}  refs={lab.member_refs if lab else ()}")
        if got:
            hit += 1
    print(f"\n标注成功 {hit}/{len(_SMOKE)} (人工判域名是否准确; 这里只验通路 + 非空)")
    return 0 if hit == len(_SMOKE) else 1


def run_project(pid: str, provider: str, model: str) -> int:
    from codev_platform.graph.analyzers.brain_labeler import BrainDomainLabeler
    from codev_platform.graph.analyzers.business_domain import BusinessDomainAnalyzer
    from codev_platform.graph.schema import EdgeKind, NodeKind
    from codev_platform.graph.store import load_graph, open_store

    labeler = BrainDomainLabeler(_cfg(provider, model))
    if not labeler.available():
        print(f"FATAL: provider '{provider}' 不可用(缺 key/agent extra)。")
        return 1
    conn = open_store(pid)
    try:
        g = load_graph(conn, pid)
    finally:
        conn.close()
    eps = {n.id: n.name for n in g.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value}
    if not eps:
        print(f"FATAL: project '{pid}' graph store 无 backend_endpoint —— 先跑 ingest。")
        return 1

    r = BusinessDomainAnalyzer(labeler).analyze(pid, g.nodes, g.edges)
    dom_name = {n.id: n.name for n in r.nodes if n.kind == NodeKind.BUSINESS_DOMAIN.value}
    ep_dom: dict[str, str] = {}
    for e in r.edges:
        if e.kind == EdgeKind.BELONGS_TO_DOMAIN.value and e.source in eps:
            ep_dom[e.source] = dom_name.get(e.target, "?")

    print(f"# A1-3 验收  project={pid}  provider={provider}  "
          f"endpoints={len(eps)}  已标={len(ep_dom)}  域数={len(dom_name)}\n")
    print("| endpoint | 模型标的业务域 | 判定(Y/N) |")
    print("|---|---|---|")
    for eid in sorted(ep_dom, key=lambda x: eps[x]):
        print(f"| {eps[eid]} | {ep_dom[eid]} | |")
    print(f"\n人工核对上表 Y/N; Y 占比 >= 70% 即 A1-3 验收通过(可放开生产注册)。")
    return 0


def run_fix(pid: str, endpoint_name: str, domain: str) -> int:
    """人工纠正(ownership): 找含该 endpoint 名的 cluster, 把整簇业务域纠正为 domain。
    纯聚类 + set_override, 不调 LLM。下次 analyze 这簇用纠正值(跳 LLM, 跨模型保留)。"""
    from codev_platform.graph.analyzers.brain_labeler import BrainDomainLabeler
    from codev_platform.graph.analyzers.business_domain import BusinessDomainAnalyzer
    from codev_platform.graph.store import load_graph, open_store

    conn = open_store(pid)
    try:
        g = load_graph(conn, pid)
    finally:
        conn.close()
    a = BusinessDomainAnalyzer(BrainDomainLabeler())   # 仅用 _cluster/set_override, 不调 LLM
    clusters, by_id = a._cluster(g.nodes, g.edges)
    target = None
    for _cid, eps, _tables in clusters:
        if any(by_id[ep].name == endpoint_name for ep in eps):
            target = eps
            break
    if target is None:
        print(f"FATAL: 没找到含 endpoint '{endpoint_name}' 的 cluster(先 ingest?)")
        return 1
    a.set_override(pid, target, domain)
    print(f"OK: cluster(含 {endpoint_name}, 共 {len(target)} 个 endpoint)业务域纠正为 '{domain}'。")
    print("下次 ingest/analyze 这簇用纠正值(跳 LLM, 跨模型保留)。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="A1-3 业务域映射验收 + 人工纠正")
    ap.add_argument("--smoke", action="store_true", help="内置样例冒烟验 LLM 通路")
    ap.add_argument("--project", help="对该 project_id 的 graph store 跑 + 导出验收表")
    ap.add_argument("--fix-endpoint", help="人工纠正: 含该 endpoint 名的 cluster 归到 --domain")
    ap.add_argument("--domain", default="", help="--fix-endpoint 时纠正成的业务域名")
    ap.add_argument("--provider", default="deepseek", help="brain provider(默认 deepseek)")
    ap.add_argument("--model", default="", help="覆盖 model(默认走 provider spec)")
    args = ap.parse_args()
    if args.smoke:
        return run_smoke(args.provider, args.model)
    if args.fix_endpoint:
        if not args.project or not args.domain:
            print("FATAL: --fix-endpoint 需同时配 --project 和 --domain")
            return 1
        return run_fix(args.project, args.fix_endpoint, args.domain)
    if args.project:
        return run_project(args.project, args.provider, args.model)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
