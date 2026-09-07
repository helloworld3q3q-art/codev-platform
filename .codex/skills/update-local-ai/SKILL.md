---
name: update-local-ai
description: 在用户明确要求、已批准模型或 schema 迁移、或有证据证明索引损坏且增量恢复无效时，手工执行 Chroma、CodeGraph、graph 或 code_vec 重建。不得用于修复服务未启动、hook 入队失败、pending、超时或日志缺行。
---

# 重建本地 AI 索引

## 进入重建的必要条件

至少满足一项，并在执行前登记计划和验证范围：

- 用户明确要求“重建索引”，且理解这是前台写操作。
- embedding 模型、向量维度、collection/schema 或 runner 契约发生已批准迁移。
- manifest、完整性检查或可复现查询证明索引损坏；已尝试正式增量 hook，queue 已清空，目标 commit 仍无法被 manifest 覆盖。

以下现象一律不构成重建授权：`health --all` 连不上 18083、MCP 冷启动/超时、daemon 未运行、hook enqueue 失败、queue pending、`reindex.log` 无 `finished`、一次等待超时、单纯怀疑 stale。

## 先走增量恢复

```powershell
codev-platform serve-mcp status
codev-platform reindex-queue status # 仅在 queue owner runtime；Windows + WSL owner 改用 serve-mcp status
git hook run post-commit
codev-platform wait-for-reindex --commit HEAD --timeout-sec 300
```

- 服务不可达：运行 `codev-platform serve-mcp start`，不要用 reindex 启服务。
- Windows 不得直接运行 `codev-platform post-commit`、`reindex-queue status` 或旧 `tools/dev/post-commit.ps1` 访问 WSL file queue；改用 Git hook、HTTP wait 和 `serve-mcp status`。
- manifest/result 是完成真值；本地日志仅作诊断。

## 获批后的最小重建命令

```powershell
codev-platform reindex --chroma
codev-platform reindex --codegraph
codev-platform reindex --ingest
codev-platform reindex --code-vec
codev-platform reindex                 # 全档，仅用户明确要求或批准迁移
codev-platform reindex --chroma --force # 仅模型/schema 迁移或已证明 Chroma 损坏
```

选择能修复已证明故障的最小 scope。不得因“不确定”默认全档，也不得并发启动第二个 rebuild。完成后用 manifest、定向查询和 `ai-health` 复核。

## 相关

- 触发指南：当前客户端 surface 下的 `rules/ai-tools-mcp.md`
- 验栈 skill：`/ai-health`（跑完后建议跑一次确认）
- 增量恢复入口：`git hook run post-commit`
