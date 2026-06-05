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


def _local_graph(pid: str) -> Any:
    """统一图谱 store 节点/边数 (graph_store/<pid>.sqlite, 替代退役的 cross_layer)。未建返回 'not_built'。"""
    from codev_platform.graph.store import graph_store_path
    gdb = graph_store_path(pid)
    if not gdb.is_file():
        return "not_built"
    c = _sqlite_counts(gdb, "nodes", "edges") or {}
    return {"nodes": c.get("nodes", 0), "edges": c.get("edges", 0), "source": "local"}


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
        usage.setdefault(k, {"search_docs": 0, "codegraph": 0})[key] += 1

    from codev_platform.core.paths import logs_dir
    for path, key in (
        # search_recall.jsonl 已迁 data_root/logs (与 _obslog 写入一致); 其余 usage.jsonl 未迁。
        (logs_dir() / "search_recall.jsonl", "search_docs"),
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


def mcp_usage_report(repo_root: Path) -> dict[str, Any]:
    """MCP 调用分析:每项目 + 合计,7 天窗 + 全时段累计,按来源(agent/dev)分桶。

    - chroma(platform-docs):agent / dev 调用 + 命中(search_recall.jsonl 含 client 字段;
      老日志无 client → 计 dev)
    - codegraph:调用数(纯 dev —— web 端 agent 的 codegraph/impact 工具直读
      sqlite/store,不走这个 MCP server)
    - 自部署模型:embed 调用(每次搜索 1 次)+ rerank 调用(rerank_used 为真),即本机 Qwen
      embedding/reranker 的实际推理次数。
    """
    from codev_platform.core.paths import logs_dir
    cutoff = datetime.now() - timedelta(days=7)

    def _blank() -> dict[str, dict[str, int]]:
        return {
            "chroma": {"agentCalls": 0, "devCalls": 0, "agentHits": 0, "devHits": 0},
            "codegraph": {"calls": 0},   # 纯开发端(agent 不走此 MCP)
            "model": {"agentEmbed": 0, "devEmbed": 0, "agentRerank": 0, "devRerank": 0},
        }

    acc: dict[str, dict[str, dict]] = {"last7d": {}, "allTime": {}}

    def _get(window: str, pid: str | None) -> dict:
        return acc[window].setdefault(pid or "(legacy)", _blank())

    def _windows(o: dict) -> list[str]:
        ts = _parse_dt(str(o.get("ts", "")))
        return ["allTime", "last7d"] if (ts is None or ts >= cutoff) else ["allTime"]

    def _iter(path: Path):
        if not path.is_file():
            return
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    recall = logs_dir() / "search_recall.jsonl"
    cg = repo_root / "codev_platform" / "codegraph" / "codegraph_usage.jsonl"

    for o in _iter(recall):
        a = (o.get("client") or "dev") == "agent"   # agent(web) vs dev(开发端)
        hit = (o.get("hit") or 0) > 0
        rerank = bool(o.get("rerank_used"))
        for w in _windows(o):
            ch = _get(w, o.get("project_id"))["chroma"]
            mo = _get(w, o.get("project_id"))["model"]
            ch["agentCalls" if a else "devCalls"] += 1
            if hit:
                ch["agentHits" if a else "devHits"] += 1
            mo["agentEmbed" if a else "devEmbed"] += 1   # 每次搜索 1 次 embed
            if rerank:
                mo["agentRerank" if a else "devRerank"] += 1
    for path, key in ((cg, "codegraph"),):
        for o in _iter(path):
            for w in _windows(o):
                _get(w, o.get("project_id"))[key]["calls"] += 1

    def _shape(window: str) -> dict[str, Any]:
        total = _blank()
        projects = []
        for pid, m in sorted(acc[window].items()):
            projects.append({"projectId": pid, **m})
            for grp, kv in m.items():
                for k, v in kv.items():
                    total[grp][k] += v
        return {"projects": projects, "total": total}

    return {"last7d": _shape("last7d"), "allTime": _shape("allTime")}


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

    # MCP 端点 reachability (P5): codegraph / graph SSE 端点是否常驻可达。
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
        # codegraph: 读本机 co-located 仓的 .codegraph 文件(repo_path 服务端配置)。
        # (Java codegraph-api :18082 HTTP 取数路径已退役 2026-06-04 —— 查询面由 codev web
        #  routes graph 接口替代; 跨机取统计未来走 web routes, 不再用 Java api。)
        codegraph: Any
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

        projects[pid] = {
            "chroma_chunks": chroma_pid.get(pid, 0),
            "codegraph": codegraph,
            "graph": _local_graph(pid),
            "memory_project": mem_proj.get(pid, 0),
            "usage_7d": usage.get(pid, {"search_docs": 0}),
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
