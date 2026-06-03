"""平台数据聚合 —— 服务端逻辑,供 HTTP 控制面端点(daemon `/platform/status`)调用。

原则(用户拍板 2026-05-30):**访问平台数据一律走 HTTP/HTTPS** —— 子应用 / 健康检查 /
本地访问全都是,本地=localhost、远程=远程地址,没有"直接读文件路径"这回事。

本模块是**服务端**聚合:跑在平台主机上,读本机 data/ + PG + 本机 co-located 仓的 .codegraph,
通过 daemon 的 `/platform/status` 以 JSON 暴露。客户端(`health --all` / 子应用)只 HTTP GET,
绝不碰文件路径 —— 这是"服务地址 not 路径"的落地。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from codev_platform.core.config import get as _cfg_get, env_or_config

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    # codev_platform/platform_status.py -> parents[1] = codev-platform 仓根
    return Path(__file__).resolve().parents[1]


def _data_dir(cfg: dict) -> Path:
    d = _cfg_get(cfg, "data.platform_data_dir")
    return (Path(d).expanduser() if d else _repo_root() / "data")


def _sqlite_counts(db: Path, *tables: str) -> Any:
    res: dict[str, int] = {}
    try:
        conn = sqlite3.connect(str(db))
        cur = conn.cursor()
        names = {x[0] for x in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in tables:
            res[t] = cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0] if t in names else -1
        conn.close()
    except Exception as exc:  # noqa: BLE001 - best effort
        logger.debug("_sqlite_counts(%s) 失败(返回 None): %s", db, exc)
        return None
    return res


def _parse_dt(text: str) -> datetime | None:
    text = (text or "").strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.fromisoformat(text) if fmt is None else datetime.strptime(text, fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _query_codegraph_api(base_url: str) -> Any:
    """通过 codegraph-api(Java HTTP 服务, 默认 :18082)取 codegraph 统计 —— "访问 codegraph
    数据走 HTTP" 的落地。成功返回 {'nodes','edges','source':'http'};服务没起/出错返回
    'api_down' / 'api_error'(调用方退回本地 sqlite 或标注)。"""
    import urllib.request
    url = base_url.rstrip("/") + "/v1/codegraph/stats"
    try:
        req = urllib.request.Request(
            url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - service 没起 / 网络
        return "api_down"
    if d.get("result") == 0 and isinstance(d.get("data"), dict):
        s = d["data"]
        return {"nodes": int(s.get("totalNodes") or 0),
                "edges": int(s.get("totalEdges") or 0), "source": "http"}
    return "api_error"


def _query_crosslink_api(base_url: str) -> Any:
    """通过 codegraph-api(:18082)取 cross-link 统计 —— POST /v1/cross-link/stats。
    nodesByKind / edgesByRel 求和得总数。成功返回 {'nodes','edges','source':'http'};
    服务没起 'api_down';无 cross-link 数据(库空/不适用)'no_data'。"""
    import urllib.request
    url = base_url.rstrip("/") + "/v1/cross-link/stats"
    try:
        req = urllib.request.Request(
            url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return "api_down"
    if d.get("result") == 0 and isinstance(d.get("data"), dict):
        nk = d["data"].get("nodesByKind") or {}
        ek = d["data"].get("edgesByRel") or {}
        if nk:
            return {"nodes": sum(int(v) for v in nk.values()),
                    "edges": sum(int(v) for v in ek.values()), "source": "http"}
        return "no_data"
    return "api_error"


def _local_crosslink(data_dir: Path, pid: str) -> Any:
    """平台本地 cross_layer.sqlite(中心化, 平台自有)读 nodes 数。未建返回 'not_built'。"""
    xdb = data_dir / "codegraph_ext" / pid / "cross_layer.sqlite"
    if not xdb.is_file():
        return "not_built"
    c = _sqlite_counts(xdb, "nodes") or {}
    return {"nodes": c.get("nodes", 0), "source": "local"}


def _self_project_id(repo_root: Path) -> str | None:
    pj = repo_root / ".claude" / "project.json"
    if pj.is_file():
        try:
            return json.loads(pj.read_text(encoding="utf-8")).get("project_id")
        except Exception as exc:  # noqa: BLE001
            logger.debug("_self_project_id 读 %s 失败(返回 None): %s", pj, exc)
            return None
    return None


def _usage_7d(repo_root: Path) -> dict[str, dict[str, int]]:
    cutoff = datetime.now() - timedelta(days=7)
    usage: dict[str, dict[str, int]] = {}

    def bump(pid: str | None, key: str) -> None:
        k = pid or "(legacy)"
        usage.setdefault(k, {"search_docs": 0, "cross_link": 0, "codegraph": 0})[key] += 1

    from codev_platform.core.paths import logs_dir
    for path, key in (
        # search_recall.jsonl 已迁 data_root/logs (与 _obslog 写入一致); 其余 usage.jsonl 未迁。
        (logs_dir() / "search_recall.jsonl", "search_docs"),
        (repo_root / "codev_platform" / "cross_link" / "cross_link_usage.jsonl", "cross_link"),
        (repo_root / "codev_platform" / "codegraph" / "codegraph_usage.jsonl", "codegraph"),
    ):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_dt(str(o.get("ts", "")))
            if ts is not None and ts < cutoff:
                continue
            bump(o.get("project_id"), key)
    return usage


def build_platform_status(cfg: dict) -> dict[str, Any]:
    """聚合所有项目 x 三库 + 记忆 + 7d 使用率。服务端调用(读本机资源),返回 JSON-able dict。"""
    repo_root = _repo_root()
    data = _data_dir(cfg)
    chroma_data = data / "chroma"
    self_pid = _self_project_id(repo_root)
    errors: list[str] = []

    reg = repo_root / "platform_meta" / "projects"
    registered = sorted(p.name for p in reg.iterdir() if p.is_dir()) if reg.is_dir() else []

    # chroma: 列所有 on-disk collection -> per pid chunks
    chroma_pid: dict[str, int] = {}
    try:
        import chromadb
        cl = chromadb.PersistentClient(path=str(chroma_data))
        for col in cl.list_collections():
            if col.name.endswith("__platform_docs"):
                chroma_pid[col.name[: -len("__platform_docs")]] = col.count()
    except Exception as e:  # noqa: BLE001
        errors.append("chroma:" + repr(e))

    # memory PG: 按 scope 分
    mem_proj: dict[str, int] = {}
    mem_org = 0
    try:
        import psycopg
        dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
        if dsn:
            with psycopg.connect(dsn) as con:
                q = ("SELECT scope,scope_ref,count(*) FROM memory_entries "
                     "WHERE status='active' GROUP BY scope,scope_ref")
                for scope, ref, n in con.execute(q):
                    if scope == "org":
                        mem_org += n
                    elif scope == "project":
                        mem_proj[ref] = mem_proj.get(ref, 0) + n
        else:
            errors.append("memory:pg_dsn 未配")
    except Exception as e:  # noqa: BLE001
        errors.append("memory:" + repr(e))

    # MCP 端点 reachability (P5): cross-link / codegraph SSE 端点是否常驻可达。
    # chroma 自身就是本 daemon, 不重复探。失败不阻塞整体 status。
    mcp_endpoints: list[dict] = []
    try:
        from codev_platform import mcp_serve
        mcp_endpoints = mcp_serve.probe_all(cfg)
    except Exception as e:  # noqa: BLE001
        errors.append("mcp_endpoints:" + repr(e))

    usage = _usage_7d(repo_root)
    pids = sorted(set(registered) | set(chroma_pid))
    projects: dict[str, Any] = {}
    for pid in pids:
        # codegraph: 优先走 codegraph-api(HTTP, "访问 codegraph 数据走 HTTP");
        # 没配 codegraph_api_url 才退回读本机 co-located 仓的 .codegraph 文件(repo_path 服务端配置)。
        codegraph: Any
        api_url = _cfg_get(cfg, f"projects.{pid}.codegraph_api_url")
        if api_url:
            codegraph = _query_codegraph_api(api_url)
        else:
            if self_pid and pid == self_pid:
                repo: Path | None = repo_root
            else:
                rp = _cfg_get(cfg, f"projects.{pid}.repo_path")
                repo = Path(rp).expanduser() if rp else None
                if repo and not repo.exists():
                    repo = None
            if repo is None:
                codegraph = "no_repo_path"
            else:
                db = repo / ".codegraph" / "codegraph.db"
                if db.is_file():
                    counts = _sqlite_counts(db, "nodes", "edges") or {}
                    codegraph = {"nodes": counts.get("nodes", 0),
                                 "edges": counts.get("edges", 0), "source": "local"}
                else:
                    codegraph = "no_db"

        # cross-link: 优先 codegraph-api /v1/cross-link/stats(HTTP);否则 / API 无数据时
        # 退回平台本地 cross_layer.sqlite(中心化, 平台自有)。
        cross_link: Any
        if api_url:
            cl = _query_crosslink_api(api_url)
            cross_link = cl if isinstance(cl, dict) else _local_crosslink(data, pid)
        else:
            cross_link = _local_crosslink(data, pid)

        projects[pid] = {
            "chroma_chunks": chroma_pid.get(pid, 0),
            "codegraph": codegraph,
            "cross_link": cross_link,
            "memory_project": mem_proj.get(pid, 0),
            "usage_7d": usage.get(pid, {"search_docs": 0, "cross_link": 0}),
            "registered": pid in registered,
        }

    return {
        "schema": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_root": str(data),
        "memory_org": mem_org,
        "registered": registered,
        "projects": projects,
        "mcp_endpoints": mcp_endpoints,
        "usage_legacy": usage.get("(legacy)"),
        "errors": errors,
    }
