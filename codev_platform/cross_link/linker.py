"""按 URL path + HTTP method 精确匹配 frontend_api ↔ java_endpoint，建 calls_api 边。

匹配规则：
- frontend_api.meta.url == java_endpoint.meta.url
- frontend_api.meta.http_method == java_endpoint.meta.http_method
- 注意：项目约定接口都 POST，但容差到 method match 防止例外（如 RequestMapping 默认）

边：
- frontend_api --calls_api--> java_endpoint  (confidence=1.0 精确匹配)
- 如果命中但 http_method 不一致：confidence=0.7（仍建边便于发现，evidence 提示）

未匹配的 frontend_api（前端调了但后端没找到对应 Controller）→ 输出到 stats.unmatched_frontend
未被引用的 java_endpoint（后端有但前端没调）→ 输出到 stats.unused_java_endpoints
"""
from __future__ import annotations

import json
import logging
import sqlite3

from codev_platform.cross_link.schema import upsert_edge

logger = logging.getLogger(__name__)


def link_api(conn: sqlite3.Connection) -> dict[str, int | list]:
    """跑前端 → Java 精确匹配，建 calls_api 边。

    要求 conn 中已有 frontend_api + java_endpoint 节点（先跑 scan_frontend_apis + scan_java_controllers）。
    """
    stats: dict[str, int | list] = {
        "frontend_api_count": 0,
        "java_endpoint_count": 0,
        "calls_api_edges_exact": 0,
        "calls_api_edges_method_mismatch": 0,
        "unmatched_frontend": [],   # 前端 API URL Java 找不到
        "unused_java_endpoints": [], # Java endpoint 没被前端调
    }

    # 拉所有 frontend_api / java_endpoint
    cur = conn.execute(
        "SELECT id, name, path, line, meta_json FROM nodes WHERE kind='frontend_api'"
    )
    frontend_rows = cur.fetchall()
    stats["frontend_api_count"] = len(frontend_rows)

    cur = conn.execute(
        "SELECT id, name, path, line, meta_json FROM nodes WHERE kind='java_endpoint'"
    )
    java_rows = cur.fetchall()
    stats["java_endpoint_count"] = len(java_rows)

    # 建索引：url → list of java_endpoint (id, http_method, name)
    java_by_url: dict[str, list[tuple[int, str, str]]] = {}
    for jid, jname, _jpath, _jline, jmeta_json in java_rows:
        try:
            jmeta = json.loads(jmeta_json) if jmeta_json else {}
        except Exception:
            jmeta = {}
        url = (jmeta.get("url") or "").strip()
        method = (jmeta.get("http_method") or "POST").upper()
        if not url:
            continue
        java_by_url.setdefault(url, []).append((jid, method, jname))

    matched_java_ids: set[int] = set()

    # 遍历 frontend，匹配
    for fid, fname, fpath, _fline, fmeta_json in frontend_rows:
        try:
            fmeta = json.loads(fmeta_json) if fmeta_json else {}
        except Exception:
            fmeta = {}
        url = (fmeta.get("url") or "").strip()
        method = (fmeta.get("http_method") or "POST").upper()
        if not url:
            continue
        candidates = java_by_url.get(url) or []
        if not candidates:
            stats["unmatched_frontend"].append({"name": fname, "url": url, "path": fpath})
            continue
        # 选 method 一致的 candidate；若没有则取第一个标记 mismatch
        chosen = None
        for jid, jmethod, jname in candidates:
            if jmethod == method:
                chosen = (jid, jmethod, jname, 1.0, "exact")
                break
        if chosen is None:
            jid, jmethod, jname = candidates[0]
            chosen = (jid, jmethod, jname, 0.7, f"method_mismatch frontend={method} java={jmethod}")
        jid, _jmethod, _jname, conf, evidence_tag = chosen
        upsert_edge(
            conn, fid, "calls_api", jid,
            confidence=conf,
            evidence=f"{evidence_tag} url={url}",
        )
        if conf >= 1.0:
            stats["calls_api_edges_exact"] += 1
        else:
            stats["calls_api_edges_method_mismatch"] += 1
        matched_java_ids.add(jid)

    # 未被前端调用的 java endpoint
    for jid, jname, jpath, _jline, jmeta_json in java_rows:
        if jid in matched_java_ids:
            continue
        try:
            jmeta = json.loads(jmeta_json) if jmeta_json else {}
        except Exception:
            jmeta = {}
        stats["unused_java_endpoints"].append({
            "name": jname,
            "url": jmeta.get("url"),
            "path": jpath,
        })

    conn.commit()
    return stats
