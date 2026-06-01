# 2026-06-01 审计整改状态

对应审计报告: `docs/audits/codev-platform-audit-report-2026-06-01.md`

本文件记录当前复核后的整改状态。

## 已完成

| 编号 | 问题 | 当前状态 |
|---|---|---|
| 1 | Chroma 顶层 import `chromadb`，轻量安装 import 失败 | 已修复，`chromadb` 延迟到 `_get_client()` |
| 2 | wheel 缺少 rules / skills，普通 pip 安装后同步失败 | 已修复，资源迁移到 `codev_platform/resources/` 并进入 package data |
| 3 | platform-docs `/healthz` 不一致导致误判 DOWN | 已修复，`/healthz` 和 `/health` 都返回最小健康信息 |
| 4 | Chroma reload 依赖全局 `.last_build.json` | 已修复，优先使用 `.last_build.<project_id>.json` |
| 5 | health cross-link 路径不尊重 `PLATFORM_DATA_DIR` | 已修复，改用 `cross_link_db_path(project_id)` |
| 6 | 生产认证默认 passthrough 风险 | 已修复基础防线，`prod` 或远程 `platform.url` 非 token 会拒绝启动 |
| 7 | 明文密钥风险 | 仓库扫描 0 命中；仍建议用户本机 key 轮换并使用环境变量 |
| 10 | SSE 非法 project_id 可能返回 None | 已修复，返回 400 JSON |

## 已验证

- `python -m compileall -q codev_platform tests`: 通过
- `python -m pytest -q`: `351 passed, 5 skipped`
- `python -m pip check`: 通过
- `python -m pip wheel . -w dist --no-deps`: 通过
- 干净 venv 安装 wheel 后:
  - `codev-platform --version`: 通过
  - `sync-rules --dry-run`: 通过
  - `sync-skills --dry-run`: 通过
- 仓库明文密钥扫描: 0 命中
- 2026-06-01 全链路复核报告: `docs/audits/fullchain-audit-2026-06-01.md`

## 未完成 / 待验证

| 编号 | 问题 | 当前状态 |
|---|---|---|
| 8 | Docker 部署验证 | 当前机器无 Docker，无法验证 |
| 9 | 源码和文档历史乱码 | 已复核，之前 59 个文件为扫描误报；真实只命中 `windows-powershell.md` 中故意保留的乱码示例 |
| 11 | cross-link / codegraph 服务状态 | 当前本机 `serve-mcp status` 显示 DOWN，端口未监听 |
| 12 | 授权、升级、U 盘授权、二进制化 | 尚未实现 |
| 13 | 当前 HEAD 索引新鲜度 | `health --mode full` 提示 `hook missed?`，需要补跑 post-commit/reindex |

## 当前结论

项目已经可以继续做内部 POC 和演示版建设。  
还不能直接作为客户正式私有化交付产品发布。

下一步优先级:

1. 让 `serve-mcp status` 三个核心服务全部 OK。
2. 在 Docker 环境跑通 compose 部署。
3. 保持 UTF-8 读写规范，避免 PowerShell 5.1 默认编码再次写坏文件。
4. 再做 license / 授权 / 二进制化 / U 盘授权。
