# 架构原则

## 概述

本文档定义项目的架构设计原则，确保代码结构清晰、可维护性高。

## 1. 轻量聚合

### index.tsx 文件职责

- **仅负责组件编排**，不包含复杂逻辑
- **保持主文件专注于**：UI 组合和简单事件处理
- 行数限制详见 `.codex/rules/code-quality.md`

### utils.tsx 文件职责

- **复杂逻辑**：数据转换、验证、计算
- **函数命名规范**：
  - `convertParams` - 转换 API 请求参数
  - `convertFormValues` - 转换表单值（表单 → API）
  - `convertValuesForm` - 转换 API 数据（API → 表单）
  - `validateXXX` - 数据验证
  - `formatXXX` - 数据格式化
- 行数限制详见 `.codex/rules/code-quality.md`

## 2. 关注点分离

### 文件职责划分

- **index.tsx**: 页面容器、状态管理、事件处理（委托给 utils）
- **Columns.tsx**: 表格列定义、搜索字段配置
- **ToolBarRender.tsx**: 工具栏按钮、批量操作
- **{DrawerName}/**: 表单抽屉，使用语义化命名（如 `UserFormDrawer/`、`RolePermissionDrawer/`）
- **utils.tsx**: 数据转换、验证、计算

## 3. Context 和回调函数规范

### 设计原则

1. **数据和引用通过 context 传递**
   - `actionRef`、`formRef` 等引用对象
   - `selectedRowKeys`、`selectedRows` 等父组件状态数据
   - `siteCode`、`tenantId`、`currentSiteId` 等上下文数据
   - `getFormattedEnums` 等全局 Model 方法

2. **回调函数通过 on×××× 方法传递**
   - `onAdd`、`onEdit`、`onDelete` 等操作回调
   - `onOpenUploadModal` 等弹窗控制回调

3. **React Hooks 使用规范**

   先判断文件是 **React 组件** 还是 **普通函数**：

   | 类型 | 特征 | 能用 `useModel`？ |
   | --- | --- | --- |
   | React 组件 | 返回 JSX，以 `<Comp />` 方式使用（`index.tsx`、`XxxDrawer/index.tsx`） | ✅ 可以直接调用 |
   | 普通函数 | 返回数组/对象/元素，以函数调用方式使用（`createColumns`、`toolBarRender`、`utils` 函数、model 工具函数） | ❌ 不能调用 |
   - ❌ **禁止在 model 内部调用 `useModel`**（model 本身是 Hook，不能嵌套调用其他 model）
   - ❌ **禁止在 `Columns.tsx`、`ToolBarRender.tsx`、`utils.tsx` 等普通函数中调用 `useModel`**
   - ❌ **禁止在 model 内直接读 `localStorage`**（model state 是缓存，绕过它会导致数据不一致）
   - ✅ model 或普通函数需要跨模块数据时，由 React 组件同时调用多个 `useModel`，在组件层聚合后通过参数传入

## 4. 枚举使用规范

### 数据来源

枚举值统一通过 `useModel('enum')` 获取，**禁止从 `enumslocal.tsx` 导入**（该文件仅供 AI 识别业务结构，不用于实际取值）：

```typescript
const { getEnumOptions, getFormattedEnums } = useModel('enum');

// 下拉选项
const statusOptions = getEnumOptions('YesNo');
// → [{ value: 'Yes', label: '是' }, { value: 'No', label: '否' }]

// 显示文本（Badge、表格渲染）
const yesNoMap = getFormattedEnums('YesNo');
// → { Yes: '是', No: '否' }
```

### 使用位置

- **React 组件**（`index.tsx`、`XxxDrawer/index.tsx`）：直接调用 `useModel('enum')`
- **普通函数**（`Columns.tsx`、`ToolBarRender.tsx`、`utils.tsx`）：不是 React 组件，不能调用 Hook，由调用它的 React 组件获取后通过 `context` 传入

```typescript
// index.tsx - 父组件获取并传入
const { getEnumOptions, getFormattedEnums } = useModel('enum');
const columns = useMemo(() => createColumns({
  context: {
    yesNoOptions: getEnumOptions('YesNo'),
    yesNoMap: getFormattedEnums('YesNo'),
  },
  ...
}), [getEnumOptions, getFormattedEnums, ...]);

// Columns.tsx - 从 context 接收
interface CreateColumnsProps {
  context: {
    yesNoOptions: { label: string; value: string }[];
    yesNoMap: Record<string, string>;
  };
}
```

### model 跨模块数据的正确处理方式

model 需要另一个 model 的数据时，将相关逻辑上移到使用它的 React 组件中：

```typescript
// ✅ 正确 - 组件层同时调用多个 useModel，在组件内聚合
// TabContainer/index.tsx（React 组件）
const { tabs, setTab } = useModel('tabcontainer');
const { userInfo } = useModel('user');

const getMenuTitle = useCallback(
  (pathname: string): string => {
    const menus = userInfo.menus || [];
    // ...使用 menus 查找并翻译标题
  },
  [userInfo.menus],
);

// ❌ 错误 - model 内嵌套 useModel
export default function useTabContainer() {
  const { userInfo } = useModel('user'); // 运行时报错
}

// ❌ 错误 - model 内直接读 localStorage（绕过 model 缓存）
export function getMenuTitle(pathname: string) {
  const user = JSON.parse(localStorage.getItem('user')); // 数据可能与 model state 不一致
}
```

### UI 颜色映射

Badge 颜色等纯 UI 映射保留在 `utils.tsx` 中作为常量，不属于枚举数据：

```typescript
// utils.tsx
export const BADGE_STATUS: Record<string, 'success' | 'default'> = {
  Yes: 'success',
  No: 'default',
};
```

---

## 5. 组件拆分原则

### 何时拆分

- `index.tsx` 超过 500 行时，考虑拆分（export default + return JSX 最大 900 行）
- 单个函数超过 50 行时，提取到 utils.tsx（ESLint 限制 100 行，return JSX 不计入）
- 抽屉/弹窗超过 300 行时，拆分为多个子组件
- Columns.tsx 文件不受函数行数限制（列配置天然较长）

## 注意事项

1. **保持主文件简洁** - index.tsx 应该只负责组件编排和简单的事件处理
2. **复杂逻辑下沉** - 数据转换、验证等复杂逻辑应该在 utils.tsx 中实现
3. **遵循单一职责** - 每个文件只负责一个特定的功能
4. **Hooks 使用规范** - useModel 等 React Hooks 只能在 React 组件中使用

## 相关文件

- `.codex/rules/component-naming.md` - 组件命名规范
- `.codex/rules/code-quality.md` - 代码质量规范（类型安全、React 最佳实践）
- `.kiro/steering/react-page-development.md` - React 页面开发详细指南
