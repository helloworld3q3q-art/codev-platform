"""Web GraphAPI codegraph read-side fan-out.

Keep repo fan-out at the integration boundary: routes still see one codegraph-like
client, while this module opens one read-only CodegraphClient per RepoSpec and
prefixes extra repo ids/paths with `tag::`.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from types import TracebackType

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.repos import RepoSpec, project_repo_specs, resolve_tagged_value
from codev_platform.web.integrations.codegraph_client import CodegraphClient

_DEFAULT_SEARCH_LIMIT = 50
_MAX_SEARCH_LIMIT = 500
_DEFAULT_GRAPH_LIMIT = 2000
_MAX_GRAPH_LIMIT = 20000


@dataclass
class _ClientSlot:
    spec: RepoSpec | None
    client: CodegraphClient


def _limit(value: int | None, default: int, maximum: int) -> int:
    if value is None or value <= 0:
        return default
    return min(value, maximum)


def _tag_node(spec: RepoSpec | None, node: dict | None) -> dict | None:
    if node is None:
        return None
    if spec is None or not spec.tag:
        return dict(node)
    out = dict(node)
    if out.get("id"):
        out["id"] = spec.local_ref(str(out["id"]))
    if out.get("filePath"):
        out["filePath"] = spec.local_file(str(out["filePath"]))
    return out


def _tag_edge(spec: RepoSpec | None, edge: dict) -> dict:
    if spec is None or not spec.tag:
        return dict(edge)
    out = dict(edge)
    if out.get("source"):
        out["source"] = spec.local_ref(str(out["source"]))
    if out.get("target"):
        out["target"] = spec.local_ref(str(out["target"]))
    return out


def _tag_file(spec: RepoSpec | None, item: dict) -> dict:
    if spec is None or not spec.tag:
        return dict(item)
    out = dict(item)
    if out.get("path"):
        out["path"] = spec.local_file(str(out["path"]))
    return out


class FanoutCodegraphClient:
    """CodegraphClient-compatible facade for a logical project with extra repos."""

    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._repo_specs = project_repo_specs(project_id)
        self._slots: list[_ClientSlot] = []

    def __enter__(self) -> FanoutCodegraphClient:
        if not self._repo_specs:
            cli = CodegraphClient(self._project_id)
            self._slots.append(_ClientSlot(None, cli.__enter__()))
            return self

        for spec in self._repo_specs:
            if not spec.codegraph_db.is_file():
                continue
            cli = CodegraphClient(db_path=spec.codegraph_db)
            self._slots.append(_ClientSlot(spec, cli.__enter__()))
        if not self._slots:
            raise PlatformError(
                ErrorCode.INDEX_MISSING,
                "codegraph index not found for this project.",
                detail="no repo codegraph.db found",
            )
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        for slot in reversed(self._slots):
            slot.client.__exit__(exc_type, exc, tb)
        self._slots.clear()

    def stats(self) -> dict:
        out = {
            "totalFiles": 0, "totalNodes": 0, "totalEdges": 0,
            "byLanguage": {}, "byNodeKind": {}, "byEdgeKind": {},
        }
        for slot in self._slots:
            data = slot.client.stats()
            out["totalFiles"] += data.get("totalFiles", 0)
            out["totalNodes"] += data.get("totalNodes", 0)
            out["totalEdges"] += data.get("totalEdges", 0)
            for src_key, out_key in (
                ("byLanguage", "byLanguage"),
                ("byNodeKind", "byNodeKind"),
                ("byEdgeKind", "byEdgeKind"),
            ):
                for k, v in (data.get(src_key) or {}).items():
                    out[out_key][k] = out[out_key].get(k, 0) + v
        return out

    def search(self, keyword: str | None, languages: list[str] | None,
               kinds: list[str] | None, limit: int | None) -> list[dict]:
        lim = _limit(limit, _DEFAULT_SEARCH_LIMIT, _MAX_SEARCH_LIMIT)
        rows: list[dict] = []
        for slot in self._slots:
            for node in slot.client.search(keyword, languages, kinds, lim):
                tagged = _tag_node(slot.spec, node)
                if tagged is not None:
                    rows.append(tagged)
        return rows[:lim]

    def node(self, node_id: str | None) -> dict | None:
        if not node_id:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "id 不能为空")
        targets = self._target_slots(node_id)
        local_id = targets[0][1] if len(targets) == 1 else node_id
        for slot, ref in targets:
            node = slot.client.node(ref if len(targets) == 1 else local_id)
            if node is not None:
                return _tag_node(slot.spec, node)
        return None

    def neighbors(self, node_id: str | None, direction: str | None,
                  edge_kinds: list[str] | None) -> dict:
        if not node_id:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "id 不能为空")
        center = None
        nodes: list[dict] = []
        edges: list[dict] = []
        for slot, ref in self._target_slots(node_id):
            data = slot.client.neighbors(ref, direction, edge_kinds)
            tagged_center = _tag_node(slot.spec, data.get("center"))
            if center is None and tagged_center is not None:
                center = tagged_center
            nodes.extend(n for n in (_tag_node(slot.spec, n) for n in data.get("nodes", [])) if n is not None)
            edges.extend(_tag_edge(slot.spec, e) for e in data.get("edges", []))
        return {"center": center, "nodes": nodes, "edges": edges}

    def file_tree(self, prefix: str | None) -> list[dict]:
        target = self._target_prefix(prefix)
        rows: list[dict] = []
        for slot, local_prefix in target:
            rows.extend(_tag_file(slot.spec, f) for f in slot.client.file_tree(local_prefix))
        return rows

    def graph(self, limit: int | None, languages: list[str] | None,
              kinds: list[str] | None, edge_kinds: list[str] | None) -> dict:
        node_cap = _limit(limit, _DEFAULT_GRAPH_LIMIT, _MAX_GRAPH_LIMIT)
        per_repo_limit = max(1, math.ceil(node_cap / max(1, len(self._slots))))
        nodes: list[dict] = []
        edges: list[dict] = []
        total_nodes = 0
        total_edges = 0
        for slot in self._slots:
            data = slot.client.graph(per_repo_limit, languages, kinds, edge_kinds)
            nodes.extend(n for n in (_tag_node(slot.spec, n) for n in data.get("nodes", [])) if n is not None)
            edges.extend(_tag_edge(slot.spec, e) for e in data.get("edges", []))
            total_nodes += data.get("totalNodes", 0)
            total_edges += data.get("totalEdges", 0)
        return {
            "nodes": nodes[:node_cap], "edges": edges,
            "totalNodes": total_nodes, "totalEdges": total_edges,
        }

    def _target_slots(self, value: str) -> list[tuple[_ClientSlot, str]]:
        if not self._repo_specs:
            return [(slot, value) for slot in self._slots]
        spec, local = resolve_tagged_value(self._repo_specs, value)
        if "::" in value and spec is None:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"unknown repo tag: {value.split('::', 1)[0]}")
        if spec is not None:
            return [(slot, local) for slot in self._slots if slot.spec == spec]
        return [(slot, value) for slot in self._slots]

    def _target_prefix(self, prefix: str | None) -> list[tuple[_ClientSlot, str | None]]:
        raw = (prefix or "").strip()
        if not raw or not self._repo_specs:
            return [(slot, prefix) for slot in self._slots]
        spec, local = resolve_tagged_value(self._repo_specs, raw)
        if "::" in raw and spec is None:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"unknown repo tag: {raw.split('::', 1)[0]}")
        if spec is not None:
            return [(slot, local) for slot in self._slots if slot.spec == spec]
        return [(slot, prefix) for slot in self._slots]
