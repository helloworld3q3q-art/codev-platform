# AGENTS.md - codev-platform web-ui

> Codex 前端子项目入口。上层总规则见仓根 `AGENTS.md`；本文件只补充 `web-ui` 的前端约束。
>
> `web-ui/CLAUDE.md` 和 `web-ui/.claude/**` 仅作为迁移期兼容副本保留。Codex 优先读取本文件、仓根 `.codex/rules/`，以及迁移后的 `web-ui/.codex/**`。
>
> `web-ui/.codex/skills/**/SKILL.md` 若未被当前会话自动列为 skill，就按普通流程文档手动读取。

---

## 技术栈

- Umi Max 4 / React 18 / TypeScript
- Ant Design 6 / Pro Components
- UnoCSS / Less
- API client 由 OpenAPI 生成到 `src/services/apis/**`
- 图谱页面使用 Three.js / react-force-graph-3d / 自定义 `Graph3DCanvas`

---

## 前端工作协议

1. 改前先按仓根 `AGENTS.md` 读取 `.codex/rules/workflow.md`。
2. 涉及页面、组件、图谱、API 契约时，优先用 MCP 查上下文：
   - 前端符号/组件：`codegraph_context` / `codegraph_search`
   - 前后端调用链：`graph find_api_callers` / `find_page_dependencies`
   - 设计/历史决策：`platform-docs search_docs`
3. `src/services/apis/**` 是生成 API 层，默认不手改。后端 schema 变更后走 `pnpm run api`。
4. 不在前端引入新的服务包装层，优先直用生成 API client 和本地纯函数。
5. 大图谱性能相关改动优先复用 `Graph3DCanvas` / `GraphInstancedLayer` 的单一实现，不在两个图谱页面重复逻辑。
6. 前端按本仓现状不强制写单元测试；风险较高时至少跑 `tsc` 和 `lint:js`，必要时用浏览器手测。

---

## 常用命令

```powershell
npm --prefix web-ui run start:dev
npm --prefix web-ui run api
npm --prefix web-ui run tsc
npm --prefix web-ui run lint:js
npm --prefix web-ui run build
```

若在 `web-ui` 目录内执行，可去掉 `--prefix web-ui`。

---

## 重点目录

| 路径 | 说明 |
|---|---|
| `src/pages/` | 页面 |
| `src/components/` | 前端公共组件 |
| `src/services/apis/` | OpenAPI 生成 API client，默认禁手改 |
| `src/menus.tsx` | 静态菜单和角色过滤 |
| `config/routes.ts` | Umi 路由 |
| `src/pages/codegraph/` | 节点图谱相关页面 |
| `src/pages/unifiedgraph/` | 统一图谱页面 |
| `src/components/Graph3DCanvas*` | 图谱 3D 渲染核心，修改需关注大图性能 |

---

## web-ui 规则索引

| 规则 | 用途 |
|---|---|
| `web-ui/.codex/rules/architecture.md` | 前端架构与目录边界 |
| `web-ui/.codex/rules/code-quality.md` | TypeScript / React 质量红线 |
| `web-ui/.codex/rules/api-service.md` | OpenAPI client 与接口调用规范 |
| `web-ui/.codex/rules/component-patterns.md` | 组件抽取和页面组织 |
| `web-ui/.codex/rules/component-naming.md` | 组件命名 |
| `web-ui/.codex/rules/react-patterns.md` | Hooks / 状态 / 副作用模式 |
| `web-ui/.codex/rules/antd6-adapt.md` | Ant Design 6 适配 |
| `web-ui/.codex/rules/styles.md` | 样式组织 |
| `web-ui/.codex/rules/stock-color-convention.md` | 业务颜色约定 |

---

## 最小验证矩阵

| 改动 | 验证 |
|---|---|
| 普通页面/组件 | `npm --prefix web-ui run tsc` |
| API client / schema | 后端启动后 `npm --prefix web-ui run api` + `tsc` |
| 图谱渲染 | `tsc` + 本地浏览器打开 `/codegraph/graph` 和 `/codegraph/unified` |
| 路由/菜单 | `tsc` + 登录后侧栏/路径手测 |
| 样式大改 | `lint:js` + 桌面/窄屏截图检查 |
