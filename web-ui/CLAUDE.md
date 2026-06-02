# CLAUDE.md — codev-platform 控制台 (web-ui)

> 本目录是 codev-platform 管理后端的 React 控制台,**整仓克隆自 `apps/stock-admin-web`**(架构 / 样式 / 组件 / 请求层逐字保留)。
> 前端开发规则与 stock-admin-web **完全一致**,见 `.claude/rules/*`,改本目录代码默认遵循。
> 运行 / 后端对接 / 代码生成见 `README.md`。

---

## 技术栈

Umi Max 4.x · React 18 · Ant Design 6.x · antd-style · UnoCSS · Less · TypeScript · pnpm。
与 stock-admin-web 相同;唯一区别是后端换成 codev-platform FastAPI(`:18088`),业务页面换成本平台模块(项目 / 任务 / 图谱 / 枚举)。

## 场景 → 规则对照(同 stock-admin-web)

| 改动场景 | 必读规则 |
|---|---|
| 新建组件 | `component-naming.md` + `architecture.md` + `code-quality.md` |
| 表格 / ProTable / 弹窗 / 远程下拉 | `component-patterns.md` |
| 状态 / Hooks | `react-patterns.md`(禁 useRequest / JSX 内联函数 / useEffect 简洁) |
| Antd 6 组件改造 | `antd6-adapt.md` |
| 使用业务枚举 | `architecture.md` §枚举(`useModel('enum')`,禁前端硬编码) |
| 调 API | `api-service.md`(`pnpm run api` 生成,不手改 `src/services/apis/**`) |
| UnoCSS / 样式 | `styles.md` |
| import 整理 | `code-quality.md` §Import |
| 代码 review / 命名检查 / 生成组件 / 整理 import | skills:`code-review` / `check-naming` / `generate-component` / `optimize-imports` |
| Git 提交 | `git-commit.md` + 平台 `commit-pr-conventions.md`(禁 AI 痕迹) |

## 与 stock-admin-web 的差异(开发时注意)

- 后端 envelope 已对齐 `BaseApiResponse`(`result:0`+`errors[]`),`src/utils/fetch` 逐字复用,无需改请求层。
- **无后端动态菜单系统(sys_menu)**:菜单走 `config/routes.ts` + `src/menus.tsx` 静态配置(`app.tsx` 不再调业务 `postMenus`)。新增页面更新这两处。
- **`stock-color-convention.md` 是股票业务规则(涨跌红绿)**:本控制台暂无股票场景,该规则当前不触发;若将来加类似涨跌 UI 再按它做。
- 代码生成指向本平台后端:`pnpm run api` → `:18088/openapi.json`;`pnpm run enums` → `/api/v1/enums/list`(详见 `README.md` 与 `scripts/swaggerauth.ts`)。

## 规则自动加载

以下为前端编码规范,写代码时默认遵循;确有例外在注释说明原因:

@.claude/rules/architecture.md @.claude/rules/react-patterns.md @.claude/rules/component-patterns.md @.claude/rules/component-naming.md @.claude/rules/code-quality.md @.claude/rules/antd6-adapt.md @.claude/rules/styles.md @.claude/rules/api-service.md @.claude/rules/git-commit.md
