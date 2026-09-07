# CLAUDE.md — React 管理后台

> 本文件是前端层的独立工作规范。全局架构约定见上层 `platform/CLAUDE.md`,本文件只记录前端层特有的内容。
>
> **本文件默认不自动加载到 context**。改 `apps/stock-admin-web/**` 代码前请通过 `search_docs(module="stock-admin-web")` 拉取规则(详见平台 `ai-tools-mcp.md §2.5`)。

---

## 零、场景 → 规则/skill 对照表

| 改动场景 | 必读规则 | 必走 skill |
| --- | --- | --- |
| 新建组件 | `component-naming.md` + `architecture.md` §1-2 + `code-quality.md` | `/generate-component` + `/check-naming` |
| 新建页面 | `architecture.md` | 平台 `/add-frontend-page`(含路由 + sys_menu Flyway SQL 模板) |
| 远程数据下拉 | `component-patterns.md` §封装 Select 组件规范 | (无) |
| 表格列定义 / ProTable | `component-patterns.md` §ResizableTable + §操作列 2×2 grid + 4 按钮 + 更多下拉 | (无) |
| Drawer / Modal 弹窗 | `component-patterns.md` §Drawer/Modal 规范(用 @/components 封装版) | (无) |
| 状态管理 / Hooks | `react-patterns.md`(尤其 §禁用 useRequest + §JSX 内联函数 + §useEffect 简洁) | (无) |
| 涉及 Antd 6 组件改造 | `antd6-adapt.md`(Card.classNames.root / Statistic.styles.content / Alert.title / destroyOnHidden) | (无) |
| 涉及涨跌 / PnL 颜色 | `stock-color-convention.md`(priceColor / healthColor / riskColor 三函数) | (无) |
| 使用业务枚举 | `architecture.md` §4 枚举使用规范(useModel('enum')) | (无) |
| 调用 API | `api-service.md`(`pnpm run api` 生成,不手改) | (无) |
| 写 UnoCSS / 样式 | `styles.md`(baseFontSize=4, 数字直接=px) | (无) |
| import 整理 | `code-quality.md` §Import 书写规范 | `/optimize-imports` |
| 代码 review | (本目录所有规则) | `/code-review` |
| Git 提交 | `git-commit.md`(scope 必填) | (无) |

> 不确定改动属于哪个场景 → `search_docs("你要改的主题", module="stock-admin-web")` 让 reranker 帮你选。

---

## 项目概述

AI基础服务平台是一个基于 Ant Design Pro 和 Umi Max 框架的企业级 React 应用。

**核心技术栈**: Umi Max 4.x, React 18, Ant Design 6.x, @jlogi/ui, TypeScript 4.9, UnoCSS, Less, pnpm

**项目特色**:

- 🔄 自动化API生成（从Swagger）
- 🎨 主题变量自动转换（Less → JSON → UnoCSS）
- 🔐 完善的Token刷新机制
- 📊 动态菜单系统（后端驱动）
- 🚀 请求去重优化

## 开发命令

```bash
# 安装依赖
pnpm install

# 启动开发服务器（自动生成主题变量）
pnpm dev

# 指定环境启动
pnpm run start:dev      # 开发环境 (MOCK=none, REACT_APP_ENV=dev)
pnpm run start:test     # 测试环境
pnpm run start:pre      # 预发布环境

# 生产构建
pnpm run build          # 会自动运行 prebuild 脚本生成主题变量

# 代码检查
pnpm run lint           # 检查代码风格
pnpm run lint:fix       # 自动修复问题

# 从 Swagger 生成 API 服务
pnpm run api            # 从 scripts/swaggerauth.ts 配置的 URL 读取

# 从后端生成枚举定义
pnpm run enums

# TypeScript 类型检查
pnpm run tsc
```

## ⚠️ 关键开发流程

**开始工作前**，运行 `pnpm dev` 或 `pnpm start` 确保主题变量已生成。`predev` 脚本会自动清理缓存并生成所需文件。

**后端 API 变更后**，运行 `pnpm run api` 重新生成 TypeScript 服务到 `src/services/apis/`。生成的代码不可手动修改。

**新增页面模块后**，更新 `config/routes.ts` 和国际化文件（`src/locales/zh-CN/menu.ts` 等）。

## 架构与核心模式

### 1. 自动化 API 服务生成

项目从 Swagger/OpenAPI 自动生成 TypeScript API 服务：

```bash
pnpm run api  # 从 scripts/swaggerauth.ts 配置的 Swagger URL 读取
```

**生成结果** (`src/services/apis/`):

- 每个 API 标签生成独立文件（如 `loginapi.ts`）
- TypeScript 接口在 `typings.d.ts` 的 `API` 命名空间下
- 方法语义化命名：`get<ResourceName>`, `post<ResourceName>`
- 请求/响应类型完全类型化

**关键文件**: `scripts/swagger-generator.ts`

### 2. 自定义 Fetch 封装

所有 HTTP 请求通过 `src/utils/fetch/fetch.ts` 封装，提供：

**Token 管理**:

- 自动注入 token
- Token 过期自动刷新（401响应）
- 刷新期间请求排队

**请求去重**:

- 1秒内相同请求自动合并
- 由 `DuplicateRequestManager` 类实现

**文件操作**:

- `uploadFile()` / `uploadFiles()` - 带进度跟踪
- `downloadFile()` / `downloadFileByPost()` - 自动提取文件名

### 3. 主题管理流程

主题变量集中管理，实现三端统一：

1. **定义** - `src/styles/variables.less`:

   ```less
   @primary: rgb(103, 206, 19);
   @textPrimary: rgb(10, 10, 10);
   ```

2. **生成** - 自动转换:
   - `scripts/generateThemeVars.ts` 运行
   - 生成 `src/styles/theme.json`

3. **使用** - 三种方式:
   ```less
   /* Less 中 */
   @import '~@/styles/variables.less';
   color: @primary;
   ```
   ```typescript
   // JS/TS 中
   import themeVars from '@/styles/theme.json';
   const color = themeVars.primary;
   ```
   ```tsx
   // UnoCSS 中
   <div className="text-theme-primary">主题色文本</div>
   ```

### 4. 动态菜单系统

菜单从后端API加载，而非静态配置：

1. **登录返回** - 用户权限和菜单结构
2. **状态存储** - `src/models/user.ts` 的 `user` model
3. **菜单获取** - ProLayout 通过 `src/app.tsx` 的 `menu.request` 回调
4. **格式转换** - `transformMenuData()` 将后端格式转为 ProLayout 格式

### 5. 组件命名规范

由 `eslint-plugin-filenames` 强制执行：

- **组件文件**（`src/components/`, `src/pages/components/`）: PascalCase
  - 例如: `Button.tsx`, `UserProfile.tsx`

- **其他文件**: lowercase + numbers
  - 例如: `utils.ts`, `api123.ts`

- **例外文件**: 必须小写
  - `index.ts`, `types.ts`, `const.ts`, `typings.d.ts`

- **忽略**: `.less`, `.md` 文件和 `locales/` 目录

## 认证流程

### 登录流程

1. 用户提交凭证 → `login()` API
2. 存储 token 和用户信息到 localStorage
3. 重定向到目标页面或首页

### Token刷新机制

1. 请求拦截器检查 token，添加 `Authorization: Bearer <token>` 头
2. 收到 401 响应 → 调用 `postRefresh()` 刷新 token
3. 用新 token 更新 localStorage
4. 重试失败请求（并发请求排队等待）
5. 刷新失败 → 清除 localStorage → 重定向登录页

## 关键实现示例

### 使用 API 服务

```typescript
import { getUser, updateProfile } from '@/services/apis/user';

// GET 请求
const user = await getUser({ userId: '123' });

// POST 请求
const result = await updateProfile({
  username: 'newname',
  email: 'contact@example.invalid',
});

// 文件下载
await loginApi.downloadReport({ reportId: '456' });
```

### 使用全局状态 (Models)

```typescript
import { useModel } from '@umijs/max';

function MyComponent() {
  const { userInfo, setUser, clearUser } = useModel('user');
  const { getFormattedEnums } = useModel('enum');

  // userInfo 自动与 localStorage 同步
}
```

### UnoCSS 使用

```tsx
// 原子类
<div className="text-primary bg-secondary p-4 m-2 rounded-lg">

// 主题颜色（来自 Less 变量）
<div className="text-theme-primary bg-theme-cardBackground">

// 响应式
<div className="text-sm md:text-base lg:text-lg">
```

## Git 提交规范

详见 `.claude/rules/git-commit.md`(conventional commits + commitlint, type/scope/subject 必填) 与平台 `.claude/rules/commit-pr-conventions.md`(AI 痕迹禁止红线)。

## 项目特定注意事项

1. **主题变量依赖**: 修改 `variables.less` 后需重新运行开发服务器
2. **API 自动生成**: `src/services/apis/` 下文件不可手动修改
3. **请求合并**: 相同请求1秒内自动合并，注意可能影响业务逻辑
4. **动态菜单**: 菜单由后端返回，新增菜单需更新后端权限配置
5. **代码分割**: Webpack配置在 `config/splitChunksConfig.ts`
6. **环境变量**: `REACT_APP_ENV` (dev/test/pre/prod), `UMI_ENV`, `MOCK`

## AI 辅助开发配置

### 记忆系统

- 记忆规则: `.claude/memory/memory-system.md`
- 记忆索引: `.claude/memory/MEMORY.md`
- 包含: 用户偏好、反馈、项目状态、外部引用

### Claude Code 规则

以下是项目编码规范，写代码时默认遵循；确有例外时在注释中说明原因：

@.claude/rules/code-quality.md @.claude/rules/react-patterns.md @.claude/rules/component-patterns.md @.claude/rules/styles.md @.claude/rules/architecture.md @.claude/rules/api-service.md @.claude/rules/component-naming.md @.claude/rules/git-commit.md

### Claude Code 技能

项目包含 `.claude/skills/` 目录，提供代码审查等可复用工作流。
