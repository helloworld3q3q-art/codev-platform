#!/usr/bin/env python3
"""PreToolUse hook —— 减 grep / 提 MCP 命中的轻量提醒(团队共享, .claude/settings.json 挂载)。

模型将调 Grep/Glob 时,注入一条"本仓有 MCP 索引,优先 MCP 再 grep"的提醒。
非阻断(永不拦截工具),且每会话只提醒一次(temp marker 按 session_id 去重),防 alarm fatigue。
后续要加严(改成 ask/deny)只动 settings.json,本脚本仍可复用。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

# Windows 默认 GBK stdout 编码不了 ↔ 等字符 → 强制 UTF-8(hook 输出 JSON 本就该 UTF-8)。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_REMINDER = (
    "本仓已建 MCP 索引,优先 MCP 再 grep:"
    "找代码符号/定义/调用链 → codegraph_search·codegraph_callers;"
    "找规则/设计/事故文档 → search_docs;"
    "找前端↔端点↔表跨层链路 → cross-link(find_table_refs·find_endpoint_link)。"
    "grep+Read 仅在看未提交改动 / 怀疑索引滞后 / 核对最新源码时兜底。(本提醒每会话仅一次)"
)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    sid = str(data.get("session_id") or "nosession")
    key = hashlib.md5(sid.encode("utf-8")).hexdigest()[:16]
    marker = os.path.join(tempfile.gettempdir(), f"codev-mcp-nudge-{key}")

    if os.path.exists(marker):
        print(json.dumps({"suppressOutput": True}))  # 本会话已提醒 → 静默放行
        return

    print(json.dumps({
        "suppressOutput": True,  # 对用户安静; additionalContext 仍注入模型
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": _REMINDER,
        },
    }, ensure_ascii=False))

    # marker 写在成功打印之后: 若打印失败(编码等), 不消耗"每会话仅一次", 下次仍会提醒。
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(sid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
