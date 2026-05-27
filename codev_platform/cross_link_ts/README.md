# codev_platform.cross_link_ts

TypeScript AST 扫描器(ts-morph)— 解前端 import / call,扩展 cross-link KG 到 TS 层。

**当前状态**:从 platform 仓 `tools/cross_link_ts/` 整目录迁来。`apps/stock-admin-web/src/services/apis/**` 排除路径仍硬编码,新项目接入前需改 CLI 参数。

## 用法

```powershell
cd codev_platform/cross_link_ts
pnpm install
pnpm run scan -- <ts-root> [--exclude pattern1,pattern2]
```

## TODO

- 排除路径改 CLI `--exclude` 参数 / 读 .claude/index.json
- 输出格式对齐 cross_link.schema
- 增量扫描(只看 git diff 变更的 .ts 文件)
