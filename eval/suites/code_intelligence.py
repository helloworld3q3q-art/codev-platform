"""code_intelligence suite —— A1/A2 软标签准确率回归 (graph store 直查, 不调 LLM)。

把 A1 业务域 / A2 架构分层的准确率验收从"人肉核对"固化成可复跑 golden set。labeler 是 LLM,
但本 suite 只读已落库的软节点/软边算准确率, 纯确定性、可回归。软标签缺失(analyzer 未开 /
该机未 reindex)→ status="skipped"。
"""
from __future__ import annotations

from eval.metrics import accuracy
from eval.suites._common import load_jsonl


def _norm_path(p: str) -> str:
    """归一文件路径用于匹配: 反斜杠 -> 正斜杠 + 小写。"""
    return (p or "").replace("\\", "/").lower()


def _file_matches(actual: str, golden: str) -> bool:
    """golden 是相对路径 (如 codev_platform/web/db/tables.py); actual 是 store 里的
    node.file。精确相等 / actual 以 '/golden' 结尾 (golden 是后缀) 即命中。"""
    a, gkey = _norm_path(actual), _norm_path(golden)
    return a == gkey or a.endswith("/" + gkey)


def score_label_cases(actual_by_file: dict[str, set[str]], cases: list[dict],
                      expect_key: str) -> dict:
    """纯函数: 给 {file -> 已标标签集} + golden cases, 算分类准确率。

    actual_by_file: 从 store 聚合的 file_path -> {role/domain 名}。
    cases:          [{file, <expect_key>, note?}, ...]。
    expect_key:     "expect_role" (A2) 或 "expect_domain" (A1)。

    命中判定: golden 文件的实际标签集**包含** expected 标签即 correct。
    区分 labeled=False(该文件根本没被标, 可能 batch 漏)与 labeled-but-wrong(标错)。
    无 IO、无 store 依赖 -> 可脱离 live store 单测 (对齐 memory conflict 子集)。
    """
    correct = 0
    details: list[dict] = []
    for c in cases:
        expected = c[expect_key]
        got: set[str] = set()
        for f, labels in actual_by_file.items():
            if _file_matches(f, c["file"]):
                got |= labels
        ok = expected in got
        correct += 1 if ok else 0
        details.append({
            "file": c["file"],
            "expect": expected,
            "got": sorted(got),
            "ok": ok,
            "labeled": bool(got),
            "note": c.get("note", ""),
        })
    total = len(cases)
    return {
        "accuracy": round(accuracy(correct, total), 3),
        "correct": correct,
        "total": total,
        "unlabeled": sum(1 for d in details if not d["labeled"]),
        "details": details,
    }


def _build_arch_role_index(g) -> dict[str, set[str]]:
    """从 graph 的 PLAYS_ROLE 软边聚合 file_path -> {arch_layer 角色名}。"""
    from codev_platform.graph.schema import EdgeKind

    node_by_id = {n.id: n for n in g.nodes}
    out: dict[str, set[str]] = {}
    for e in g.edges:
        if e.kind != EdgeKind.PLAYS_ROLE.value:
            continue
        src = node_by_id.get(e.source)
        tgt = node_by_id.get(e.target)
        if src is not None and tgt is not None and src.file:
            out.setdefault(src.file, set()).add(tgt.name)
    return out


def _build_domain_index(g) -> dict[str, set[str]]:
    """从 BELONGS_TO_DOMAIN 软边聚合 file_path -> {业务域名} (A1, 数据齐时启用)。"""
    from codev_platform.graph.schema import EdgeKind

    node_by_id = {n.id: n for n in g.nodes}
    out: dict[str, set[str]] = {}
    for e in g.edges:
        if e.kind != EdgeKind.BELONGS_TO_DOMAIN.value:
            continue
        src = node_by_id.get(e.source)
        tgt = node_by_id.get(e.target)
        if src is not None and tgt is not None and src.file:
            out.setdefault(src.file, set()).add(tgt.name)
    return out


def run_code_intelligence(project_id: str) -> dict:
    rows = load_jsonl("code_intelligence.jsonl")
    arch_cases = [r for r in rows if r.get("kind") == "arch_role"]
    domain_cases = [r for r in rows if r.get("kind") == "business_domain"]

    try:
        from codev_platform.graph.schema import NodeKind
        from codev_platform.graph.store import graph_store_path, open_store
    except Exception as e:  # noqa: BLE001
        return {"suite": "code_intelligence", "status": "skipped",
                "reason": f"graph 模块不可导入 ({type(e).__name__}: {e})。",
                "n": len(rows)}

    db_path = graph_store_path(project_id)
    if not db_path.exists():
        return {"suite": "code_intelligence", "status": "skipped",
                "reason": f"graph store 不存在: {db_path}。先在有数据的机器跑 "
                          f"`codev-platform reindex` (或 graph ingest) 建图谱。",
                "n": len(rows)}

    with open_store(project_id, mode="ro") as store:
        g = store.load_graph(project_id)

    has_arch = any(n.kind == NodeKind.ARCH_LAYER.value for n in g.nodes)
    has_domain = any(n.kind == NodeKind.BUSINESS_DOMAIN.value for n in g.nodes)
    if not has_arch and not has_domain:
        return {"suite": "code_intelligence", "status": "skipped",
                "reason": f"graph store ({db_path}) 无软标签节点 (ARCH_LAYER / "
                          f"BUSINESS_DOMAIN 均 0) —— analyzer 未在该机跑过。"
                          f"开 config `analyzers.arch_layer.enabled` / "
                          f"`business_domain.enabled` 后 reindex (软标签 analyzer "
                          f"上线在 WSL, 见 roadmap-2026-06-08)。",
                "n": len(rows)}

    sub: dict = {}
    metrics: dict = {}
    if arch_cases and has_arch:
        arch = score_label_cases(_build_arch_role_index(g), arch_cases, "expect_role")
        sub["arch_role"] = arch
        metrics["arch_role_accuracy"] = arch["accuracy"]
    if domain_cases and has_domain:
        dom = score_label_cases(_build_domain_index(g), domain_cases, "expect_domain")
        sub["business_domain"] = dom
        metrics["business_domain_accuracy"] = dom["accuracy"]

    return {
        "suite": "code_intelligence",
        "status": "ok",
        "n": sum(s["total"] for s in sub.values()),
        "project_id": project_id,
        "metrics": metrics,
        "sub": sub,
    }
