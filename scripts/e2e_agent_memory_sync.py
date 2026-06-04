# -*- coding: utf-8 -*-
r"""e2e: agent-memory MCP 换机同步 + 隐私隔离验证(dev-agent-memory P1 核心验收)。

平台 PG 共享, "换机" = 不同 MCP 客户端连同一后端。本脚本开多个独立 SSE 会话驱动真实
agent-memory 端点(默认 19087), 验证两条断言:

  ① 记忆跟人走: 机器A(userX)`remember` 写 personal → 机器B(**同 userX**, 另一会话)
     `recall` 命中同一条(换机/重 clone 后同 token 自动召回回本人记忆)。
  ② 隐私隔离: 机器C(**不同 userY**)`recall` **看不到** userX 的 personal(personal 对他人不可见)。

跑法(WSL serve-mcp 起 agent-memory 后):
  .venv/bin/python scripts/e2e_agent_memory_sync.py [--url http://127.0.0.1:19087/sse]
退出码 0 全通过 / 1 失败。自清理(forget 写入的测试条)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


async def _call(url: str, user: str, tool: str, args: dict) -> dict:
    """开一个独立 SSE 会话(= 一台"机器"), 以 user 身份(passthrough X-User-Id)调一次工具。"""
    headers = {"X-User-Id": user, "X-Org-Id": "default"}
    async with sse_client(url, headers=headers) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = res.content[0].text if res.content else "{}"
            return json.loads(text)


async def main(url: str) -> int:
    user_x = "e2e-sync-alice"   # 机器 A / B 同一个人
    user_y = "e2e-sync-bob"     # 机器 C 另一个人
    nonce = uuid.uuid4().hex[:10]
    content = f"e2e-changemachine-marker {nonce}"
    topic = f"e2e-sync-{nonce}"
    ok = True
    eid = None

    # 机器 A: remember(personal) —— scope 省略默认 personal, scope_ref 强制本人。
    w = await _call(url, user_x, "remember", {"content": content, "topic_key": topic})
    if not w.get("ok"):
        print(f"[FAIL] 机器A remember 失败: {w}")
        return 1
    eid = w["id"]
    print(f"[A] remember ok id={eid[:8]} user={user_x}")

    try:
        # 机器 B: 同 user, 另一会话 recall —— 应命中(记忆跟人走)。
        rb = await _call(url, user_x, "recall", {"query": nonce})
        b_hit = any(nonce in e.get("content", "") for e in rb.get("entries", []))
        print(f"[B] recall(同 user) 命中={b_hit} (期望 True) — count={rb.get('count')}")
        ok = ok and b_hit

        # 机器 C: 不同 user recall —— 不应命中(personal 对他人不可见)。
        rc = await _call(url, user_y, "recall", {"query": nonce})
        c_hit = any(nonce in e.get("content", "") for e in rc.get("entries", []))
        print(f"[C] recall(他 user) 命中={c_hit} (期望 False) — count={rc.get('count')}")
        ok = ok and (not c_hit)
    finally:
        # 自清理: 机器 A forget(只能删本人, 正好验 owner 限定)。
        f = await _call(url, user_x, "forget", {"entry_id": eid})
        print(f"[A] cleanup forget ok={f.get('ok')}")

    if ok:
        print(f"[PASS] 换机同步 + 隐私隔离 e2e 通过 (nonce={nonce})")
        return 0
    print("[FAIL] 断言未全部满足")
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:19087/sse",
                    help="agent-memory SSE 端点(默认平台 19087)")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.url)))
