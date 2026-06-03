"""端点 → 后端函数 codegraph 桥接 (Track A1 — 让统一图谱 store 自成连通)。

问题: 端点本身不在 codegraph (路由装饰器不是代码符号),所以 store 里 backend_endpoint
出边 = 0,前端能链到端点、却链不到端点背后碰表的函数 → 全链路在端点处断。

做法: 端点的 **handler 函数** 在 codegraph 有节点 + `calls` 出边。本模块加载 codegraph 的
calls 调用图 (一次性入内存),从每个端点 handler 出发做**深度受限 BFS**,把可达到、且在
store 里是 `backend_function`(碰表)的函数,物化成 `backend_endpoint --calls--> backend_function`
边写回 store。于是 store 连通:
    前端 --calls_api--> 端点 --calls--> 函数 --reads/writes_table--> 表。

join key = (归一化 file, 函数名)。codegraph callee 的 startLine 是**定义行**、store
backend_function 的 line 是 **SQL 字符串行**,二者不可比,故不用 line 做 key。

边界 (plan §六 风险):
- 深度/访问上限防调用图爆炸;命中不了不挂边、不报错。
- fail-soft: codegraph 索引缺失 / 任意异常 → 返回空,绝不拖垮 ingest 其它阶段。
- 只读 codegraph (CodegraphClient 只读连接),不写 codegraph。
"""
from __future__ import annotations

import logging

from codev_platform.core.errors import PlatformError
from codev_platform.graph.schema import EdgeKind, GraphEdge, GraphNode

logger = logging.getLogger(__name__)

BRIDGE_PLUGIN = "builtin.codegraph_bridge"

_MAX_DEPTH = 6     # 调用链深度上限 (端点 handler → service → repo → SQL 通常 ≤4)
_MAX_VISIT = 400   # 单端点 BFS 访问节点上限 (防爆炸)
# 把 endpoint handler 关联到的 codegraph 节点 kind (函数/方法)。
_CALLABLE_KINDS = ("function", "method")


def _norm(path: str | None) -> str:
    """归一化相对路径: 反斜杠转正斜杠 + 去前导 ./,与 store 节点 file 对齐。"""
    if not path:
        return ""
    s = path.replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


def bridge_endpoints_to_functions(
    project_id: str,
    endpoints: list[GraphNode],
    functions: list[GraphNode],
    *,
    codegraph_db=None,
) -> list[GraphEdge]:
    """用 codegraph calls 图把 endpoint → backend_function(碰表) 边物化。

    codegraph_db: 显式 codegraph.db 路径 (测试注入);None 走 project_id 默认布局。
    任意失败 (索引缺失 / sqlite 故障) → fail-soft 返回 []。
    """
    if not endpoints or not functions:
        return []

    # store backend_function 索引: (归一化 file, name) -> node id。
    func_by_key: dict[tuple[str, str], str] = {}
    for fn in functions:
        if fn.file and fn.name:
            func_by_key.setdefault((_norm(fn.file), fn.name), fn.id)
    if not func_by_key:
        return []

    try:
        from codev_platform.web.integrations.codegraph_client import CodegraphClient

        with CodegraphClient(project_id, db_path=codegraph_db) as cg:
            conn = cg.conn
            # calls 调用图一次性入内存 (codev ~3.4k / openclaw ~10k 边, 轻量)。
            adj: dict[str, list[str]] = {}
            for src, tgt in conn.execute(
                "SELECT source, target FROM edges WHERE kind = 'calls'"
            ):
                if src is not None and tgt is not None:
                    adj.setdefault(src, []).append(tgt)
            # codegraph 函数/方法节点: id -> (归一化 file, name);name -> [(id, file)] 供 handler 查找。
            id_key: dict[str, tuple[str, str]] = {}
            name_ids: dict[str, list[tuple[str, str]]] = {}
            ph = ",".join("?" for _ in _CALLABLE_KINDS)
            for nid, name, fpath in conn.execute(
                f"SELECT id, name, file_path FROM nodes WHERE kind IN ({ph})", _CALLABLE_KINDS
            ):
                nf = _norm(fpath)
                id_key[nid] = (nf, name)
                name_ids.setdefault(name, []).append((nid, nf))

            edges: list[GraphEdge] = []
            seen: set[tuple[str, str]] = set()
            for ep in endpoints:
                handler = (ep.meta or {}).get("handler")
                if not handler or not ep.file:
                    continue
                start = _find_handler(name_ids, handler, _norm(ep.file))
                if start is None:
                    continue
                for nid in _reachable(adj, start):
                    key = id_key.get(nid)
                    target = func_by_key.get(key) if key else None
                    if target and (ep.id, target) not in seen:
                        seen.add((ep.id, target))
                        edges.append(
                            GraphEdge(
                                source=ep.id,
                                target=target,
                                kind=EdgeKind.CALLS.value,
                                confidence=0.7,  # 经调用图推导, 低于直接锚点
                                meta={"bridge": "codegraph", "via_handler": handler},
                            )
                        )
            return edges
    except PlatformError as exc:
        logger.debug("[bridge] codegraph 不可用, 跳过 endpoint->function 桥接: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001 — 桥接是增强, 失败不拖垮 ingest
        logger.warning("[bridge] codegraph 桥接异常, 跳过: %r", exc)
        return []


def _find_handler(
    name_ids: dict[str, list[tuple[str, str]]], handler: str, ep_file: str
) -> str | None:
    """按 handler 名找 codegraph 节点 id, 优先同文件 (端点 handler 一般定义在端点所在文件)。"""
    cands = name_ids.get(handler)
    if not cands:
        return None
    for nid, nf in cands:
        if nf == ep_file:
            return nid
    # 无同文件命中: 唯一同名才用 (安全); 多个同名歧义 → 放弃挂边 (宁缺毋滥, 桥接置信本就 0.7,
    # 防 Java 同名方法跨 Controller 误挂)。
    return cands[0][0] if len(cands) == 1 else None


def _reachable(adj: dict[str, list[str]], start_id: str) -> set[str]:
    """从 start_id 沿 calls 出边做深度/访问受限 BFS, 返回可达节点 (含 start)。"""
    visited = {start_id}
    queue: list[tuple[str, int]] = [(start_id, 0)]
    while queue and len(visited) <= _MAX_VISIT:
        nid, depth = queue.pop(0)
        if depth >= _MAX_DEPTH:
            continue
        for tgt in adj.get(nid, ()):
            if tgt not in visited:
                visited.add(tgt)
                queue.append((tgt, depth + 1))
    return visited
