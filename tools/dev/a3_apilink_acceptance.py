"""A3 前端→后端 API 链接验收 —— 用真实 LLM(brain provider)验证推断质量。

两模式:
  --smoke           内置 thorn6 风格样例冒烟(业务封装 request + 候选端点 → 验 LLM 通路 + 解析 + grounding)
  --project <pid>   对该项目 graph store 跑 A3, 导出"前端文件 → 推断调的端点"表(人工核对 Y/N)
                    --repo <path> 指向该 project 的前端仓根(A3 读源码)。

key 红线: **不写进脚本、不进 git**。经 env(如 DEEPSEEK_API_KEY)或 config.agent.providers
.<name>.api_key 解析(env > config)。provider 由 --provider 选(默认 deepseek)。

用法(WSL):
  DEEPSEEK_API_KEY='sk-...' .venv/bin/python tools/dev/a3_apilink_acceptance.py --smoke
  DEEPSEEK_API_KEY='sk-...' .venv/bin/python tools/dev/a3_apilink_acceptance.py \
      --project sample-project-beta --repo /path/to/thorn6
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cfg(provider: str, model: str) -> dict:
    prov: dict = {}
    if model:
        prov["model"] = model
    return {"agent": {"provider": provider, "providers": {provider: prov}}}


# 内置 thorn6 风格样例: 业务封装内联 request, url 在源码但业务命名 + 候选端点清单。
_SMOKE_SNIPPET = (
    "import request from '@/utils/request'\n"
    "export function fetchCurrencyList(params) {\n"
    "  return request({ url: '/pda/currency/list', method: 'post', data: params })\n"
    "}\n"
    "export function saveOrder(o) {\n"
    "  return request({ url: '/pda/order/save', method: 'post', data: o })\n"
    "}\n"
)
# 候选端点(ref, url, method, name); 期望 LLM 连到 currency/list + order/save, 不连 noise。
_SMOKE_ENDPOINTS = [
    ("ep1", "/pda/currency/list", "POST", "POST /pda/currency/list"),
    ("ep2", "/pda/order/save", "POST", "POST /pda/order/save"),
    ("ep3", "/pda/user/profile", "GET", "GET /pda/user/profile"),
]
_SMOKE_EXPECT = {"ep1", "ep2"}


def run_smoke(provider: str, model: str) -> int:
    from codev_platform.graph.analyzers.api_link_labeler import (
        ApiLinkRequest,
        EndpointRef,
        FrontendFile,
    )
    from codev_platform.graph.analyzers.brain_api_link_labeler import BrainApiLinkLabeler

    labeler = BrainApiLinkLabeler(_cfg(provider, model))
    if not labeler.available():
        print(f"FATAL: provider '{provider}' 不可用(缺 key 或 agent extra?)。"
              f"设 env <PROVIDER>_API_KEY; sig={labeler.signature}")
        return 1
    req = ApiLinkRequest(
        file=FrontendFile(path="src/api/currency.js", snippet=_SMOKE_SNIPPET),
        endpoints=tuple(EndpointRef(*e) for e in _SMOKE_ENDPOINTS))
    labels = labeler.label([req])
    got = set(labels[0].endpoint_refs) if labels else set()
    print(f"provider={provider}  signature={labeler.signature}\n")
    print(f"  期望 refs ≈ {sorted(_SMOKE_EXPECT)}")
    print(f"  模型 refs  = {sorted(got)}")
    # grounding: 所有 refs 必在候选内(labeler 已剔越界, 这里复核)。
    valid = {e[0] for e in _SMOKE_ENDPOINTS}
    assert got <= valid, f"越界 ref 未被剔: {got - valid}"
    ok = got == _SMOKE_EXPECT
    print(f"\n{'OK' if ok else '部分命中'}: 命中={got & _SMOKE_EXPECT} 漏={_SMOKE_EXPECT - got} "
          f"误={got - _SMOKE_EXPECT}")
    print("(人工判: 应连 currency/list + order/save, 不连 user/profile)")
    return 0 if ok else 1


def run_project(pid: str, repo: str, provider: str, model: str) -> int:
    from codev_platform.graph.analyzers.brain_api_link_labeler import BrainApiLinkLabeler
    from codev_platform.graph.analyzers.frontend_api_link import FrontendApiLinkAnalyzer
    from codev_platform.graph.schema import EdgeKind, NodeKind
    from codev_platform.graph.store import load_graph, open_store

    repo_path = Path(repo)
    if not repo_path.is_dir():
        print(f"FATAL: --repo '{repo}' 不是目录")
        return 1
    labeler = BrainApiLinkLabeler(_cfg(provider, model))
    if not labeler.available():
        print(f"FATAL: provider '{provider}' 不可用(缺 key/agent extra)。")
        return 1
    conn = open_store(pid)
    try:
        g = load_graph(conn, pid)
    finally:
        conn.close()

    eps = {n.id: ((n.meta or {}).get("url"), n.name)
           for n in g.nodes if n.kind == NodeKind.BACKEND_ENDPOINT.value}
    if not eps:
        print(f"FATAL: project '{pid}' graph store 无 backend_endpoint —— 先跑 ingest。")
        return 1

    a = FrontendApiLinkAnalyzer(labeler, repo_path=repo_path)
    if not a.applies(g.nodes):
        print("FATAL: applies=False(无前端节点 / 无后端端点 / repo 不可读 / labeler 不可用)。")
        return 1
    r = a.analyze(pid, g.nodes, g.edges)

    call_target = {n.id: (n.file, (n.meta or {}).get("target_endpoint"))
                   for n in r.nodes if n.kind == NodeKind.INFERRED_API_CALL.value}
    links: list[tuple[str, str]] = []   # (前端文件, 后端端点名)
    for e in r.edges:
        if e.kind == EdgeKind.CALLS_API_INFERRED.value:
            f = call_target.get(e.source, (None, None))[0]
            ep = eps.get(e.target)
            if f and ep:
                links.append((f, ep[1]))

    print(f"# A3 验收  project={pid}  provider={provider}  repo={repo}  "
          f"endpoints={len(eps)}  推断链接={len(links)}\n")
    print("| 前端文件 | LLM 推断调的后端端点 | 判定(Y/N) |")
    print("|---|---|---|")
    for f, ep in sorted(links):
        print(f"| {f} | {ep} | |")
    print("\n人工核对上表 Y/N(打开前端文件看它真调的 url 是否=该端点);"
          " Y 占比 >= 70% 即 A3 验收通过(可放开生产注册)。")
    print("注意: A3 只处理静态层没连上的文件; 若链接=0 多半是静态 url_registry 已覆盖全部前端。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="A3 前端→后端 API 链接验收")
    ap.add_argument("--smoke", action="store_true", help="内置 thorn6 风格样例冒烟")
    ap.add_argument("--project", help="对该 project_id 的 graph store 跑 + 导出验收表")
    ap.add_argument("--repo", default="", help="--project 时前端仓根(A3 读源码)")
    ap.add_argument("--provider", default="deepseek", help="brain provider(默认 deepseek)")
    ap.add_argument("--model", default="", help="覆盖 model(默认走 provider spec)")
    args = ap.parse_args()
    if args.smoke:
        return run_smoke(args.provider, args.model)
    if args.project:
        if not args.repo:
            print("FATAL: --project 需同时配 --repo <前端仓根>")
            return 1
        return run_project(args.project, args.repo, args.provider, args.model)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
