"""code_intelligence suite 的纯单测 (不碰 live graph store)。

只覆盖打分纯逻辑 (score_label_cases / _file_matches) + golden 数据集自洽。
真机准确率(查 store 软标签)由 run_eval.py --suite code_intelligence 在有 A1/A2
数据的机器(WSL)跑, 这里不依赖 store —— 对齐 memory conflict 子集纯逻辑测法。
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.run_eval import (
    _build_arch_role_index,
    _file_matches,
    _norm_path,
    score_label_cases,
)

_DATASET = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "code_intelligence.jsonl"


def _load_rows() -> list[dict]:
    rows = []
    with _DATASET.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# --------------------------------------------------------------------- 路径匹配

def test_norm_path_backslash_and_case():
    assert _norm_path("Codev_Platform\\Web\\Db\\Tables.py") == "codev_platform/web/db/tables.py"


def test_file_matches_exact_and_suffix():
    # golden 是相对路径; actual 是 store 里的 node.file
    assert _file_matches("codev_platform/web/db/tables.py", "codev_platform/web/db/tables.py")
    # actual 带前缀(绝对路径)时按 '/golden' 后缀命中
    assert _file_matches("D:/x/codev_platform/web/db/tables.py", "codev_platform/web/db/tables.py")
    # 后缀须落在路径分隔边界 -> other_tables.py 不被 tables.py 误中
    assert not _file_matches("codev_platform/web/db/other_tables.py", "tables.py")
    assert _file_matches("codev_platform/web/db/other_tables.py", "other_tables.py")
    # 完全不同文件 -> 不命中
    assert not _file_matches("codev_platform/web/routes/reports.py", "codev_platform/web/db/tables.py")


# --------------------------------------------------------------------- 打分逻辑

def _actual():
    # 模拟从 store 聚合的 file -> 标签集: 一对、一错、一漏标
    return {
        "codev_platform/web/routes/reports.py": {"controller"},
        "codev_platform/platform_status.py": {"domain_model"},  # 标错(应 service)
        # tables.py 缺失 = 漏标
    }


def test_score_correct_wrong_and_unlabeled():
    cases = [
        {"file": "codev_platform/web/routes/reports.py", "expect_role": "controller"},
        {"file": "codev_platform/platform_status.py", "expect_role": "service"},
        {"file": "codev_platform/web/db/tables.py", "expect_role": "domain_model"},
    ]
    r = score_label_cases(_actual(), cases, "expect_role")
    assert r["total"] == 3
    assert r["correct"] == 1          # 只有 reports.py 对
    assert r["unlabeled"] == 1        # tables.py 没被标
    assert r["accuracy"] == round(1 / 3, 3)
    by_file = {d["file"]: d for d in r["details"]}
    assert by_file["codev_platform/web/routes/reports.py"]["ok"] is True
    assert by_file["codev_platform/platform_status.py"]["ok"] is False
    assert by_file["codev_platform/platform_status.py"]["labeled"] is True
    assert by_file["codev_platform/web/db/tables.py"]["labeled"] is False


def test_score_all_correct_is_full():
    actual = {"a/b/x.py": {"service"}, "a/b/y.py": {"repository"}}
    cases = [
        {"file": "a/b/x.py", "expect_role": "service"},
        {"file": "y.py", "expect_role": "repository"},  # 后缀匹配
    ]
    r = score_label_cases(actual, cases, "expect_role")
    assert r["accuracy"] == 1.0
    assert r["unlabeled"] == 0


def test_empty_cases_accuracy_zero():
    r = score_label_cases({}, [], "expect_role")
    assert r["accuracy"] == 0.0
    assert r["total"] == 0


# --------------------------------------------------------- store 索引构建(合成图)

class _FakeGraph:
    def __init__(self, nodes, edges):
        self.nodes = nodes
        self.edges = edges


def test_build_arch_role_index_from_plays_role_edges():
    # 用真 schema 类型造合成图, 验证 PLAYS_ROLE 软边 -> file->role 聚合
    # (graph 无 FILE kind 节点, 角色落节点级, 同 file 节点共享角色 —— 见 impact.py)
    from codev_platform.graph.schema import EdgeKind, GraphEdge, GraphNode, NodeKind

    pid = "codev-platform"
    nodes = [
        GraphNode(id="fn1", kind=NodeKind.BACKEND_FUNCTION.value, name="get_report",
                  project_id=pid, file="codev_platform/web/routes/reports.py"),
        GraphNode(id="ep1", kind=NodeKind.BACKEND_ENDPOINT.value, name="GET /reports",
                  project_id=pid, file="codev_platform/web/routes/reports.py"),
        GraphNode(id="role_controller", kind=NodeKind.ARCH_LAYER.value, name="controller",
                  project_id=pid),
    ]
    edges = [
        GraphEdge(source="fn1", target="role_controller", kind=EdgeKind.PLAYS_ROLE.value),
        GraphEdge(source="ep1", target="role_controller", kind=EdgeKind.PLAYS_ROLE.value),
        # 非软边不该进索引
        GraphEdge(source="ep1", target="fn1", kind=EdgeKind.CALLS.value),
    ]
    idx = _build_arch_role_index(_FakeGraph(nodes, edges))
    assert idx == {"codev_platform/web/routes/reports.py": {"controller"}}

    # 端到端套上打分: golden 命中
    r = score_label_cases(idx, [
        {"file": "codev_platform/web/routes/reports.py", "expect_role": "controller"},
    ], "expect_role")
    assert r["accuracy"] == 1.0


# --------------------------------------------------------------------- 数据集自洽

def test_dataset_arch_cases_use_closed_vocab():
    # A2 角色软枚举闭集(见 a2 设计 §二)。golden expect_role 不得越界。
    vocab = {"controller", "service", "repository", "domain_model",
             "util", "config", "adapter", "gateway"}
    arch = [r for r in _load_rows() if r.get("kind") == "arch_role"]
    assert arch, "code_intelligence.jsonl 应含 arch_role golden cases"
    for c in arch:
        assert c["expect_role"] in vocab, f"越界角色: {c['expect_role']} ({c['file']})"
        assert c["file"].endswith(".py") or c["file"].endswith(".ts"), c["file"]


def test_dataset_covers_each_backend_role():
    # v4 验收覆盖 controller/service/repository/adapter/domain_model 五角色, golden 不缺角色
    arch = [r for r in _load_rows() if r.get("kind") == "arch_role"]
    roles = {c["expect_role"] for c in arch}
    for need in ("controller", "service", "repository", "adapter", "domain_model"):
        assert need in roles, f"golden 缺 {need} 角色样本"
