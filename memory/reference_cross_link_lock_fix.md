---
name: reference-cross-link-lock-fix
description: "cross-link 重建在 Windows 上要用 SQLite backup API,不能 os.replace 主文件 + unlink wal/shm(会被 MCP reader 锁死)"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 07722d89-6bc3-42bf-a872-2afc6d86c0c3
---

cross-link 索引重建(`tools/cross_link/build_index.py`)在 Windows + MCP server 长连接环境下,**不能用 os.replace + 强删 wal/shm 的发布方式**。

**Why**:
- MCP server 用 sqlite3.connect 默认 WAL 模式打开 cross_layer.sqlite
- Windows 文件系统给 wal/shm 独占句柄(SQLite 默认 FILE_SHARE_READ|WRITE,不含 FILE_SHARE_DELETE)
- build 工具 unlink wal/shm → WinError 32 → rebuild 全失败
- os.replace 主文件 → WinError 5 → 同样失败

**How(2026-05-23 commit bd993b7 修复)**:
1. tmp 库写完后,**不删正式 wal/shm**
2. 用 SQLite backup API: 主进程 `sqlite3.connect(正式DB)` 作 dst, `src.backup(dst)` page-by-page copy
3. SQLite 内部用 IMMEDIATE 锁协调,MCP reader 在下个事务边界自动看到新数据
4. tmp 文件 + tmp wal/shm 可以删(无 reader 持有)
5. 正式 DB 不存在时 fallback os.replace(首次创建,无 reader 冲突)

**配套修复**:
- `ai-health.ps1 §9.3` mcp/commit ratio 改 INFO(commit 772ae3c),不再被高频小提交误报为 WARN
- 用户文风:WARN 阈值要符合本仓库高频小提交习惯

详见 `tools/cross_link/build_index.py:_build_into` 130-150 行的发布逻辑。

关联:[[reference-mcp-tools]]
