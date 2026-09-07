# 组件命名规范

## 文件命名规则

项目使用 `eslint-plugin-filenames` 强制执行命名规范。

### 组件文件（大驼峰）

**适用目录**：

- `src/components/`
- `src/pages/*/components/`

**命名规则**：

- 使用 PascalCase（大驼峰）
- 文件名必须与导出的组件名匹配
- 多个单词时每个单词首字母大写

**正确示例**：

```
✅ Button.tsx
✅ UserProfile.tsx
✅ DataTable.tsx
✅ SearchSelect.tsx
✅ ResizableTable.tsx
```

**错误示例**：

```
❌ button.tsx
❌ userProfile.tsx
❌ data-table.tsx
❌ search_select.tsx
```

### 工具文件（小写）

**适用目录**：所有非组件目录

**命名规则**：

- 使用小写字母和数字
- 简洁明了，体现文件用途

**正确示例**：

```
✅ utils.ts
✅ fetch.ts
✅ index.ts
✅ const.ts
✅ types.ts
```

### 特殊文件（必须小写）

以下文件必须使用小写，无论在哪个目录：

- `index.ts` / `index.tsx`
- `utils.ts` / `utils.tsx`
- `types.ts` / `types.tsx`
- `const.ts` / `const.tsx`
- `typings.d.ts`

### 忽略规则

以下文件不受命名规范检查：

- `src/locales/**/*` - 国际化文件
- `*.less` - 样式文件
- `*.md` - 文档文件
- `*.json` - JSON 文件
- `*.config.ts` - 配置文件

## 目录命名规则

### 页面目录（小写，无符号）

页面目录使用小写字母，**不含符号**（无中划线、无下划线）：

```
✅ src/pages/user/
✅ src/pages/transportorder/
✅ src/pages/businessnode/
❌ src/pages/UserManagement/
❌ src/pages/transport-order/
❌ src/pages/transportOrder/
```

### 组件目录（大驼峰）

组件目录使用大驼峰：

```
✅ src/components/UserFormDrawer/
✅ src/pages/user/components/PermissionDrawer/
❌ src/components/user-form-drawer/
❌ src/pages/user/components/permissionDrawer/
```

## 语义化命名

### 抽屉/弹窗组件

**命名原则：去掉与目录同义的冗余前缀，但保留足以区分同页面内多个组件的语义。**

页面组件目录（`src/pages/{module}/components/`）中，模块名往往由多个词组成（如 `businessnode` = Business + Node），命名时只需去掉与目录完全同义的冗余部分，保留区分用途的核心词：

```
businessnode/components/ 下：

✅ NodeFormDrawer      - 去掉 "Business"（与目录 businessnode 重复），保留 "Node" 区分实体
✅ NodeDetailDrawer    - 同上
✅ LogModal            - "Log" 已足够具体，无需加前缀

❌ BusinessNodeFormDrawer   - "BusinessNode" 与目录 businessnode 完全重复，冗余
❌ FormDrawer               - 太通用，同页面出现第二个表单抽屉就会冲突
❌ Drawer / Modal           - 无语义
```

**共享组件（`src/components/`）**：无目录上下文，名称必须含完整模块语义：

```
✅ UserFormDrawer       - 用户表单抽屉
✅ RolePermissionDrawer - 角色权限抽屉
✅ SkillConfigModal     - 技能配置弹窗

❌ FormDrawer          - 无模块语义，太通用
❌ Drawer / Modal      - 无语义
```

### 列配置文件

统一使用 `Columns.tsx`：

```
✅ Columns.tsx
❌ columns.tsx
❌ ColumnConfig.tsx
❌ TableColumns.tsx
```

### 工具栏文件

统一使用 `ToolBarRender.tsx`：

```
✅ ToolBarRender.tsx
❌ toolbar-render.tsx
❌ ToolBar.tsx
❌ Toolbar.tsx
```

## 命名最佳实践

### 1. 组件命名体现功能

```typescript
// ✅ 好的命名 - 一眼看出功能
export const UserPermissionDrawer: React.FC<Props> = () => {};

// ❌ 不好的命名 - 需要打开文件才能知道做什么
export const Drawer1: React.FC<Props> = () => {};
export const FormComponent: React.FC<Props> = () => {};
```

### 2. 高阶组件命名

```typescript
// ✅ 使用 with 前缀
export const withAuth = (Component) => {};
export const withRouter = (Component) => {};

// ✅ 返回的组件保持原名
const AuthButton = withAuth(Button);
```

### 3. Hook 命名

```typescript
// ✅ 使用 use 前缀
export const useUserList = () => {};
export const usePermission = () => {};

// ❌ 不符合规范
export const getUserList = () => {};
export const userListHook = () => {};
```

### 4. 工具函数命名

```typescript
// ✅ 动词开头，体现功能
export const formatUserInfo = () => {};
export const validatePhoneNumber = () => {};
export const transformMenuData = () => {};

// ❌ 名词开头
export const userInfoFormat = () => {};
export const phoneNumberValidator = () => {};
```

## ESLint 配置

项目在 `.eslintrc.js` 中配置了命名检查：

```javascript
module.exports = {
  extends: [require.resolve('@umijs/lint/dist/config/eslint')],
  plugins: ['filenames'],
  rules: {
    'filenames/match-exported': ['error', 'match'],
    'filenames/match-regex': ['error', '^[a-zA-Z0-9]+$', true],
  },
};
```

## 命名检查脚本

```bash
# 检查所有文件命名
pnpm run lint

# 自动修复（仅限可自动修复的问题）
pnpm run lint:fix
```
