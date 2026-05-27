---
name: feedback-commit-phasing
description: "大改动按 Phase 分阶段提交,每 Phase 独立 commit,backend/frontend/docs 拆开,不混提"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 07722d89-6bc3-42bf-a872-2afc6d86c0c3
---

涉及跨层 / 多步改动时,**按 Phase 拆 commit,每 Phase 单独提**,不要等全部做完一次性大 commit。

**典型模式**(以 N12 为例):
- Phase 1:Python 真值源(单独 commit)
- Phase 2:Java 端(单独 commit,通知用户重启 + pnpm run api)
- Phase 3:前端接入(单独 commit)
- Phase 4:跨层对账 + 规则文档(单独 commit)

**Why**:
- 每个 commit 焦点单一,review / revert 都简单
- 用户可在 Phase 之间介入(如 N12 用户跑 pnpm run api 后再继续)
- backend commit 单独成立 → 即使前端 Phase 没做完也能 ship
- 失败时回滚粒度小

**How to apply**:
- ≥3 文件 + 跨 ≥2 子模块 的改动 → 拆 Phase
- 单纯 docs / config 改动 → 可合并
- 用户原话(2026-05-23):"你看着 分阶段提交 git"

关联:[[feedback-dev-order]] / [[reference-mcp-tools]]
