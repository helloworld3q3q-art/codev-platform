"""Cross-layer KG builder for codev-platform itself (self-hosted, no Java/Flyway).

The openclaw cross_link builder (tools/cross_link/build_index.py in the platform repo)
hardcodes the openclaw layout (apps/stock-admin-web, python/stock-pipeline,
apps/stock-admin-api + a mandatory mvn compile) and dies on the first missing
Java project, so it cannot index codev-platform.

codev-platform's own stack:
- backend  : FastAPI routes in codev_platform/web/routes/*.py (@router.<method>("/api/..."))
- generated: web-ui/src/services/apis/*.ts (openapi-style post/get clients with url: `/api/...`)
- pages    : web-ui/src/pages/**/*.{ts,tsx} import & call those api client functions

This builder emits, into data/codegraph_ext/<project_id>/cross_layer.sqlite:
- frontend_page  node  (a page/component file that calls api clients)
- frontend_api   node  (one per exported api client function -> url)
- java_endpoint  node  (one per FastAPI route -> url)  [kind reused for the
                        generic "backend endpoint" slot the cross-link UI/query
                        layer already understands]
- page_calls_api edge  (frontend_page -> frontend_api)
- calls_api      edge  (frontend_api  -> java_endpoint, by exact url match)

Run (project + scan root pinned via env, mirrors ops.reindex cross-link stage):

    CROSS_LINK_REPO_ROOT=<codev repo> PLATFORM_PROJECT_ID=codev-platform \
    PLATFORM_DATA_DIR=<codev repo>/data \
    python -m cross_link.build_codev
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from codev_platform.cross_link.schema import (
    DB_PATH,
    open_db,
    set_meta,
    upsert_edge,
    upsert_node,
)
from codev_platform.cross_link.linker import link_api

logger = logging.getLogger(__name__)

_env_root = os.environ.get("CROSS_LINK_REPO_ROOT")
REPO_ROOT: Path = (
    Path(_env_root).resolve() if _env_root else Path(__file__).resolve().parents[2]
)

API_DIR = REPO_ROOT / "web-ui" / "src" / "services" / "apis"
PAGES_DIR = REPO_ROOT / "web-ui" / "src" / "pages"
ROUTES_DIR = REPO_ROOT / "codev_platform" / "web" / "routes"

# --- frontend services/apis/*.ts -------------------------------------------

_RE_FN = re.compile(r"export\s+async\s+function\s+(\w+)\s*\(")
# url: `${commonUrl}/api/v1/...`  /  url: '/api/...'  /  url: "/api/..."
_RE_URL = re.compile(r"url:\s*[`'\"]\s*(?:\$\{commonUrl\})?\s*(/api/[\w\-/:]+)")
_HTTP_METHOD_PREFIX = {
    "post": "POST", "get": "GET", "put": "PUT",
    "dele": "DELETE", "del": "DELETE", "patch": "PATCH",
}


def _infer_method(fn_name: str) -> str:
    low = fn_name.lower()
    for prefix, method in _HTTP_METHOD_PREFIX.items():
        if low.startswith(prefix):
            return method
    return "POST"


def scan_frontend_apis(conn: sqlite3.Connection) -> dict:
    stats = {"ts_files": 0, "frontend_apis": 0, "skipped_no_url": 0}
    if not API_DIR.exists():
        logger.warning("frontend api dir missing: %s", API_DIR)
        return stats
    for f in sorted(API_DIR.rglob("*.ts")):
        if f.name in {"index.ts", "typings.d.ts"}:
            continue
        text = f.read_text(encoding="utf-8")
        rel = str(f.relative_to(REPO_ROOT)).replace("\\", "/")
        stats["ts_files"] += 1
        found = 0
        for m in _RE_FN.finditer(text):
            fn = m.group(1)
            line = text.count("\n", 0, m.start()) + 1
            url_m = _RE_URL.search(text[m.end():m.end() + 2000])
            if not url_m:
                continue
            url = url_m.group(1).rstrip("/")
            upsert_node(
                conn, kind="frontend_api", name=fn, path=rel, line=line, language="ts",
                meta_json=json.dumps({"url": url, "http_method": _infer_method(fn)},
                                     ensure_ascii=False),
            )
            stats["frontend_apis"] += 1
            found += 1
        if not found:
            stats["skipped_no_url"] += 1
    conn.commit()
    return stats


# --- FastAPI routes codev_platform/web/routes/*.py -------------------------

_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}


def _route_url_from_decorator(dec: ast.expr) -> tuple[str, str] | None:
    """Return (HTTP_METHOD, url) for @router.<method>("...") else None."""
    if not isinstance(dec, ast.Call):
        return None
    func = dec.func
    if not (isinstance(func, ast.Attribute) and func.attr in _HTTP_METHODS):
        return None
    # first positional arg = path literal
    for arg in dec.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return func.attr.upper(), arg.value.rstrip("/")
    return None


def scan_fastapi_routes(conn: sqlite3.Connection) -> dict:
    """Scan FastAPI route modules -> java_endpoint nodes (generic backend endpoint).

    name = operation_id when present (matches openapi style), else <function>.
    """
    stats = {"py_files": 0, "endpoints": 0}
    if not ROUTES_DIR.exists():
        logger.warning("routes dir missing: %s", ROUTES_DIR)
        return stats
    for f in sorted(ROUTES_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        src = f.read_text(encoding="utf-8")
        rel = str(f.relative_to(REPO_ROOT)).replace("\\", "/")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            logger.warning("parse fail %s: %s", rel, exc)
            continue
        stats["py_files"] += 1
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                parsed = _route_url_from_decorator(dec)
                if not parsed:
                    continue
                method, url = parsed
                op_id = None
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "operation_id" and isinstance(kw.value, ast.Constant):
                            op_id = kw.value.value
                name = op_id or node.name
                upsert_node(
                    conn, kind="java_endpoint", name=name, path=rel,
                    line=node.lineno, language="python",
                    meta_json=json.dumps({"url": url, "http_method": method,
                                          "handler": node.name}, ensure_ascii=False),
                )
                stats["endpoints"] += 1
    conn.commit()
    return stats


# --- pages -> api client calls (page_calls_api) ----------------------------


def scan_page_calls(conn: sqlite3.Connection) -> dict:
    """Map page/component files to the api client fns they import from
    @/services/apis, emitting frontend_page nodes + page_calls_api edges."""
    stats = {"page_files": 0, "frontend_pages": 0, "page_calls_api_edges": 0}
    if not PAGES_DIR.exists():
        logger.warning("pages dir missing: %s", PAGES_DIR)
        return stats
    # url-keyed api node ids (by function name) for edge targets
    api_by_name: dict[str, int] = {
        row[1]: row[0]
        for row in conn.execute(
            "SELECT id, name FROM nodes WHERE kind='frontend_api'")
    }
    imp_re = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"]@/services/apis", re.S)
    for f in sorted(PAGES_DIR.rglob("*.ts*")):
        if f.suffix not in (".ts", ".tsx"):
            continue
        text = f.read_text(encoding="utf-8")
        if "@/services/apis" not in text:
            continue
        rel = str(f.relative_to(REPO_ROOT)).replace("\\", "/")
        imported: set[str] = set()
        for m in imp_re.finditer(text):
            for tok in m.group(1).split(","):
                tok = tok.strip().split(" as ")[0].strip()
                if tok:
                    imported.add(tok)
        called = {fn for fn in imported if re.search(rf"\b{re.escape(fn)}\s*\(", text)}
        targets = [fn for fn in called if fn in api_by_name]
        if not targets:
            continue
        stats["page_files"] += 1
        page_id = upsert_node(
            conn, kind="frontend_page", name=f.stem if f.stem != "index" else
            f.parent.name, path=rel, language="ts",
        )
        stats["frontend_pages"] += 1
        for fn in targets:
            upsert_edge(conn, page_id, "page_calls_api", api_by_name[fn],
                        confidence=1.0, evidence=f"import+call {fn}")
            stats["page_calls_api_edges"] += 1
    conn.commit()
    return stats


def build(*, fresh: bool = True) -> dict:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(path=DB_PATH, fresh=fresh)
    logger.info("=== 1/4 scan_frontend_apis ===")
    fa = scan_frontend_apis(conn)
    logger.info("frontend_apis: %s", fa)
    logger.info("=== 2/4 scan_fastapi_routes ===")
    ep = scan_fastapi_routes(conn)
    logger.info("endpoints: %s", ep)
    logger.info("=== 3/4 scan_page_calls ===")
    pc = scan_page_calls(conn)
    logger.info("page_calls: %s", pc)
    logger.info("=== 4/4 link_api ===")
    link = link_api(conn)
    logger.info("link_api: edges=%s unmatched_frontend=%s unused_endpoints=%s",
                link["calls_api_edges_exact"], len(link["unmatched_frontend"]),
                len(link["unused_java_endpoints"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_meta(conn, "last_build_at", now)
    set_meta(conn, "frontend_api_stats", json.dumps(fa, ensure_ascii=False))
    set_meta(conn, "fastapi_route_stats", json.dumps(ep, ensure_ascii=False))
    set_meta(conn, "page_call_stats", json.dumps(pc, ensure_ascii=False))
    set_meta(conn, "link_api_stats", json.dumps(
        {k: v for k, v in link.items() if not isinstance(v, list)}, ensure_ascii=False))
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    return {"frontend_apis": fa, "endpoints": ep, "page_calls": pc, "link_api": link}


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()
    if args.stats:
        conn = open_db(path=DB_PATH)
        kinds = {r[0]: r[1] for r in conn.execute(
            "SELECT kind, COUNT(*) FROM nodes GROUP BY kind")}
        rels = {r[0]: r[1] for r in conn.execute(
            "SELECT rel, COUNT(*) FROM edges GROUP BY rel")}
        conn.close()
        print(json.dumps({"nodes_by_kind": kinds, "edges_by_rel": rels},
                         indent=2, ensure_ascii=False))
        return
    stats = build(fresh=True)
    printable = {k: ({kk: vv for kk, vv in v.items() if not isinstance(vv, list)}
                     if isinstance(v, dict) else v) for k, v in stats.items()}
    print("\n=== BUILD COMPLETE ===")
    print(json.dumps(printable, indent=2, ensure_ascii=False))
    print("DB:", DB_PATH)


if __name__ == "__main__":
    main()
