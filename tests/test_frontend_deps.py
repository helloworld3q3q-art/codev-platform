"""前端组件依赖扫描测试 —— mock dependency-cruiser 输出, 验解析 / 建边 / fail-soft。

scan_frontend_deps 接外部 node 工具(dependency-cruiser)。单测不跑真 node, monkeypatch
_node_available / _frontend_roots / _run_depcruise, 只验"JSON modules -> 节点+边"的解析逻辑
+ fail-soft(无 node / 工具挂)。真实端到端在 platform 仓人工验证(已确认 PermissionButton→12 页面)。
"""
from __future__ import annotations

from codev_platform.plugins.builtin._stack_scan import frontend_deps as fd
from codev_platform.graph.schema import EdgeKind, NodeKind

_FAKE = {
    "modules": [
        {"source": "src/pages/foo/index.tsx",
         "dependencies": [{"resolved": "src/components/Btn.tsx"}]},
        {"source": "src/components/Btn.tsx",
         "dependencies": [{"resolved": "src/components/Base.tsx"},
                          {"resolved": "antd"}]},  # 外部包 -> 不建边
        {"source": "src/components/Base.tsx", "dependencies": []},
    ]
}


def test_no_node_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(fd, "_node_available", lambda: False)
    assert fd.scan_frontend_deps(tmp_path, "proj") == ([], [])


def test_depcruise_failure_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(fd, "_node_available", lambda: True)
    monkeypatch.setattr(fd, "_frontend_roots", lambda repo: [tmp_path / "web"])
    monkeypatch.setattr(fd, "_run_depcruise", lambda front, pid: None)  # 工具挂
    assert fd.scan_frontend_deps(tmp_path, "proj") == ([], [])


def test_parse_nodes_and_edges(monkeypatch, tmp_path):
    monkeypatch.setattr(fd, "_node_available", lambda: True)
    monkeypatch.setattr(fd, "_frontend_roots", lambda repo: [tmp_path / "web"])
    monkeypatch.setattr(fd, "_run_depcruise", lambda front, pid: _FAKE)
    nodes, edges = fd.scan_frontend_deps(tmp_path, "proj")

    assert len(nodes) == 3
    assert all(n.kind == NodeKind.FRONTEND_MODULE.value for n in nodes)
    # front_rel="web" 拼进 file 路径(monorepo 下相对 repo 稳定锚)。
    assert all(n.file.startswith("web/src/") for n in nodes)
    # is_page: 只有 pages/ 下的页面。
    pages = [n for n in nodes if n.meta["is_page"]]
    assert len(pages) == 1 and "pages/foo/index" in pages[0].file
    # 边: foo->Btn, Btn->Base; Btn->antd(外部 src_set 外)不建。
    assert len(edges) == 2
    assert all(e.kind == EdgeKind.IMPORTS.value for e in edges)
    assert all(e.meta.get("via") == "dependency-cruiser" for e in edges)
    targets = {e.target for e in edges}
    assert not any("antd" in t for t in targets)


_FAKE_VUE = {
    "modules": [
        {"source": "src/views/Home.vue",
         "dependencies": [{"resolved": "src/components/Menu.vue"}]},
        {"source": "src/components/Menu.vue", "dependencies": []},
    ]
}


def test_vue_views_treated_as_page(monkeypatch, tmp_path):
    # vue 工程: .vue 组件 + views/ 当页面(react 用 pages/, vue 多用 views/)。
    monkeypatch.setattr(fd, "_node_available", lambda: True)
    monkeypatch.setattr(fd, "_frontend_roots", lambda repo: [tmp_path / "app"])
    monkeypatch.setattr(fd, "_run_depcruise", lambda front, pid: _FAKE_VUE)
    nodes, edges = fd.scan_frontend_deps(tmp_path, "proj")
    home = next(n for n in nodes if "Home.vue" in n.id)
    menu = next(n for n in nodes if "Menu.vue" in n.id)
    assert home.meta["is_page"] is True       # views/ => 页面
    assert menu.meta["is_page"] is False       # components/ => 非页面
    assert len(edges) == 1                      # Home renders Menu


def test_reverse_component_to_page(monkeypatch, tmp_path):
    # 验证反向可达: Base 组件 -> 被 Btn 用 -> 被 foo 页面用 => 影响 foo 页面。
    monkeypatch.setattr(fd, "_node_available", lambda: True)
    monkeypatch.setattr(fd, "_frontend_roots", lambda repo: [tmp_path / "web"])
    monkeypatch.setattr(fd, "_run_depcruise", lambda front, pid: _FAKE)
    nodes, edges = fd.scan_frontend_deps(tmp_path, "proj")
    nm = {n.id: n for n in nodes}
    rev: dict[str, list[str]] = {}
    for e in edges:
        rev.setdefault(e.target, []).append(e.source)
    base = next(n for n in nodes if "Base.tsx" in n.id)
    seen, stack, affected = set(), [base.id], set()
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for s in rev.get(cur, []):
            if nm[s].meta["is_page"]:
                affected.add(s)
            stack.append(s)
    assert len(affected) == 1
    assert "pages/foo" in nm[next(iter(affected))].file


# ---- 通用前端结构检测(不写死 src+tsconfig): React/Vue src 与 uni-app pages 都认 ----

def _touch(p, content="x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_component_dirs_detects_src_and_uniapp_pages(tmp_path):
    # 标准 src 结构
    std = tmp_path / "web"
    _touch(std / "src" / "App.vue")
    assert "src" in fd._component_dirs(std)
    # uni-app: pages/ + components/(无 src)
    uni = tmp_path / "pda"
    _touch(uni / "pages" / "index" / "index.vue")
    _touch(uni / "components" / "Foo.vue")
    dirs = fd._component_dirs(uni)
    assert "pages" in dirs and "components" in dirs and "src" not in dirs


def test_frontend_roots_finds_uniapp_without_tsconfig(tmp_path):
    # 一项目挂两个结构各异的前端: 标准 web(src+tsconfig) + uni-app(pages, 无 tsconfig)
    _touch(tmp_path / "web" / "package.json", "{}")
    _touch(tmp_path / "web" / "tsconfig.json", "{}")
    _touch(tmp_path / "web" / "src" / "App.vue")
    _touch(tmp_path / "pda" / "package.json", "{}")
    _touch(tmp_path / "pda" / "pages" / "i.vue")
    roots = {r.name for r in fd._frontend_roots(tmp_path)}
    assert roots == {"web", "pda"}        # 两个都检测到(旧版只认 src+tsconfig 会漏 pda)


def test_resolve_setup_uses_project_tsconfig(tmp_path):
    _touch(tmp_path / "tsconfig.json", "{}")
    block, tmps = fd._resolve_setup(tmp_path)
    assert "tsconfig.json" in block and tmps == []   # 用项目自带, 无临时文件


def test_resolve_setup_synthesizes_jsconfig_for_uniapp(tmp_path):
    # 无 ts/jsconfig → 合成临时 jsconfig 解 @/ alias, 返回待清理文件
    block, tmps = fd._resolve_setup(tmp_path)
    assert len(tmps) == 1 and tmps[0].exists()
    import json as _j
    cfg = _j.loads(tmps[0].read_text(encoding="utf-8"))
    assert cfg["compilerOptions"]["paths"]["@/*"] == ["./*"]   # 无 src → @ 指向根
    for t in tmps:
        t.unlink()
