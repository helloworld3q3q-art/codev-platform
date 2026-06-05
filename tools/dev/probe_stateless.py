"""验证 Streamable HTTP /mcp 的 stateless 多租户路由(Codex 用的 transport)。

1) 4 个服务的 /mcp 都能 initialize + list_tools  -> stateless 没破坏连接
2) 同一 graph 服务不同 ?project_id= 返回不同节点集 -> 路由按请求 project_id 生效, 不串台

跑: ~/work/codev-platform/.venv/bin/python tools/dev/probe_stateless.py
"""
import asyncio

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PORTS = {"platform-docs": 19083, "codegraph": 19091, "agent-memory": 19087, "graph": 19092}


async def _list_tools(port, pid="codev-platform"):
    url = f"http://127.0.0.1:{port}/mcp?project_id={pid}"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            t = await session.list_tools()
            return [x.name for x in t.tools]


async def _call(port, pid, tool, args, headers=None):
    url = f"http://127.0.0.1:{port}/mcp?project_id={pid}"
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool(tool, args)
            return r.content[0].text if r.content else "(empty)"


async def main():
    print("## 1) 4 服务 /mcp list_tools (stateless 连接可用性)")
    for name, port in PORTS.items():
        try:
            tools = await _list_tools(port)
            print(f"  {name:14s}: {len(tools)} tools OK")
        except Exception as e:  # noqa: BLE001
            print(f"  {name:14s}: ERROR {e!r}")

    print("\n## 2) graph 多租户路由 (同服务不同 project_id, 同一 query=stock)")
    # stock: openclaw-stock 必有股票表/端点节点; codev-platform(工具栈)必无 -> 结果不同即路由对
    a = await _call(19092, "codev-platform", "search_nodes", {"query": "stock", "limit": 5})
    b = await _call(19092, "openclaw-stock", "search_nodes", {"query": "stock", "limit": 5})
    print("  codev-platform :", a[:200].replace("\n", " "))
    print("  openclaw-stock :", b[:200].replace("\n", " "))
    print("  判定:", "DIFFERENT -> stateless 路由按 project_id 生效 OK"
          if a != b else "SAME -> 可能串台, 需排查")

    print("\n## 3) memory 跨身份隔离 (passthrough X-User-Id, stateless 每请求 bind ident)")
    import json
    ha = {"X-User-Id": "alice-probe", "X-Org-Id": "orgA-probe"}
    hb = {"X-User-Id": "bob-probe", "X-Org-Id": "orgB-probe"}
    # list_scope(personal) 回显 scope_ref=user_id; 两身份各调一次, 看是否各回显自己(不串)
    ma = await _call(19087, "codev-platform", "list_scope", {"scope": "personal"}, ha)
    mb = await _call(19087, "codev-platform", "list_scope", {"scope": "personal"}, hb)
    print("  X-User-Id=alice-probe :", ma[:200].replace("\n", " "))
    print("  X-User-Id=bob-probe   :", mb[:200].replace("\n", " "))

    def _ref(s):
        try:
            return json.loads(s).get("scope_ref")
        except Exception:  # noqa: BLE001
            return None
    ra, rb = _ref(ma), _ref(mb)
    if ra and rb:
        ok = ra != rb and "alice" in ra and "bob" in rb
        print("  判定:", f"alice->scope_ref={ra}, bob->scope_ref={rb} ::",
              "各自身份不串 OK" if ok else "串号, 需排查!")
    else:
        print("  (list_scope 未回显 scope_ref -> memory store 可能未启用 pg_dsn, 见上方原文)")


if __name__ == "__main__":
    asyncio.run(main())
