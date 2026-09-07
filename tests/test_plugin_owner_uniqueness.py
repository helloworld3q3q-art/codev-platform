"""生产者归属防复发闸 (unified-graph-lineage P4 防复发)。

ingest 一个多栈混合仓 (react + fastapi + spring + sql), 断言:
- 每个架构级 kind 的节点都只来自其声明 owner (KIND_OWNERS), 杜绝跨插件重复产同类节点
- builtin.cross_link 已退场 (不再作为生产者灌库)

这道闸防 "又写一个扫描器重复产 endpoint/table" 的回归 (cross_link 退场后)。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from codev_platform.graph.ingest import ingest_project
from codev_platform.plugins.ownership import kind_owners

PID = "demo-owner"

_FE = """\
import { post } from '@/utils/fetch';
export async function postFoo(d) { return post({ url: '/api/v1/foo', data: d }); }
"""
_FASTAPI = '''\
from fastapi import APIRouter
router = APIRouter()
@router.post("/api/v1/foo")
def foo():
    conn.execute("SELECT id FROM orders")
    return 1
'''
_SPRING = """\
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/v1")
public class BarController {
    @PostMapping("/bar")
    public Object bar() { return null; }
}
"""
_SCHEMA = "CREATE TABLE orders (id BIGINT, amount DECIMAL);\n"


def _build(repo: Path) -> None:
    (repo / "package.json").write_text('{"dependencies":{"react":"18"}}', encoding="utf-8")
    api = repo / "src" / "services" / "apis"
    api.mkdir(parents=True)
    (api / "fooapi.ts").write_text(_FE, encoding="utf-8")
    (repo / "routes.py").write_text(_FASTAPI, encoding="utf-8")
    (repo / "BarController.java").write_text(_SPRING, encoding="utf-8")
    (repo / "schema.sql").write_text(_SCHEMA, encoding="utf-8")


def _store_kind_plugins(store: Path) -> dict[str, set[str]]:
    conn = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT DISTINCT kind, plugin FROM nodes").fetchall()
    finally:
        conn.close()
    out: dict[str, set[str]] = {}
    for kind, plugin in rows:
        out.setdefault(kind, set()).add(plugin)
    return out


def test_each_kind_only_from_its_owner(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build(repo)
    store = tmp_path / "store.sqlite"
    ingest_project(repo, PID, store_path=store)

    kind_plugins = _store_kind_plugins(store)
    # 至少把核心几类产出来了 (fixture 覆盖 db_table / backend_endpoint / frontend_api_call)。
    assert "db_table" in kind_plugins
    assert "backend_endpoint" in kind_plugins
    assert "frontend_api_call" in kind_plugins

    owners_map = kind_owners()
    for kind, plugins in kind_plugins.items():
        owners = owners_map.get(kind)
        if owners is None:
            continue  # 未登记的 kind 不约束。
        rogue = plugins - owners
        assert not rogue, f"kind {kind} 被非 owner 插件产出: {rogue} (owner={owners})"


def test_cross_link_plugin_gone(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _build(repo)
    store = tmp_path / "store.sqlite"
    ingest_project(repo, PID, store_path=store)

    all_plugins = set()
    for plugins in _store_kind_plugins(store).values():
        all_plugins |= plugins
    assert "builtin.cross_link" not in all_plugins

    from codev_platform.plugins import registered_names
    assert "builtin.cross_link" not in registered_names()


def test_frontend_module_owner_registered(tmp_path: Path, monkeypatch) -> None:
    # frontend_deps pass 产 frontend_module 节点, 验其 owner 已在 KIND_OWNERS(防"假绿":
    # 真 fixture 无 tsconfig 时 frontend_deps no-op, 故 mock 强制产节点, 让 owner 契约真被覆盖)。
    from codev_platform.graph.schema import GraphNode, NodeKind

    def _fake_scan(repo, project_id):
        node = GraphNode(
            id=f"{project_id}:frontend_module:src/components/A.tsx",
            kind=NodeKind.FRONTEND_MODULE.value, name="A.tsx", project_id=project_id,
        )
        return [node], []

    monkeypatch.setattr(
        "codev_platform.plugins.builtin._stack_scan.scan_frontend_deps", _fake_scan)
    repo = tmp_path / "repo"
    repo.mkdir()
    _build(repo)
    store = tmp_path / "store.sqlite"
    ingest_project(repo, PID, store_path=store)

    kind_plugins = _store_kind_plugins(store)
    assert "frontend_module" in kind_plugins
    rogue = kind_plugins["frontend_module"] - kind_owners()["frontend_module"]
    assert not rogue, f"frontend_module 被非 owner 产出: {rogue}"
