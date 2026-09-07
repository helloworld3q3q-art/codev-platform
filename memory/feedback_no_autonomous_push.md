---
name: no-autonomous-push
description: 禁止主动 git push;commit 可以,但推送远程前必须先问用户
metadata:
  type: feedback
---

**禁止主动 `git push`。** commit 本地随便提,但 push 到远程(任何仓:platform / codev-platform / codev-platform-widget)前必须先征得用户同意。

**Why:** 2026-05-28 多仓 P1/P2 优化时,我把 daemon 命令 + Phase-2 计划 commit 后直接连续 push 了多个仓,用户明确制止"禁止你主动push"。push 是对外可见、影响共享状态的动作,用户要保留推送时机的控制权。

**How to apply:** 做完改动 → 可以 commit(本地、可逆)→ 但停在 push 前,问"要推吗 / 推哪些仓"。即使同一会话里之前推过、即使用户说过"提交",也不等于授权 push。三个仓都适用。与 [[feedback_commit_phasing]] 配合:commit 分阶段照旧,push 永远等指令。
