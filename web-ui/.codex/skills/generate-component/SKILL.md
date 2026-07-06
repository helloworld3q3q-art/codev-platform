---
name: web-ui-generate-component
description: 为 web-ui 生成符合项目规范的 React 组件、Hook 或工具函数。
---

# 生成组件

## 描述

快速生成符合项目规范的 React 组件、Hooks 或工具函数。

## 使用场景

- 创建 React 组件（页面/公共/业务组件）
- 创建自定义 Hook
- 创建工具函数

## 执行步骤

### 1. 确认组件类型

询问用户：

- **页面组件** - `src/pages/` 下
- **公共组件** - `src/components/` 下
- **业务组件** - `src/pages/{module}/components/` 下
- **自定义 Hook** - `src/hooks/` 下
- **工具函数** - `src/utils/` 下

### 2. 确认组件信息

收集：

- **组件名称**（英文，组件用 PascalCase，Hook/工具用 camelCase）
- **组件功能**（简要描述）
- **Props 接口**（如有）
- **样式方案**（默认 UnoCSS，可选 Less）
- **测试文件**（默认不需要）

### 3. 生成组件代码

#### 页面组件

```typescript
import { PageContainer } from '@/components';
import { i18nMessages } from '@/utils/i18n';

interface {ComponentName}Props {
  // Props 接口
}

const {ComponentName}: React.FC<{ComponentName}Props> = (props) => {
  return (
    <PageContainer title={i18nMessages('pages.{componentName}', '{中文名称}')}>
      <div className="p-4">
        {/* 页面内容 */}
      </div>
    </PageContainer>
  );
};

export default {ComponentName};
```

#### 公共组件/业务组件

```typescript
import { i18nMessages } from '@/utils/i18n';

interface {ComponentName}Props {
  // Props 接口
}

/**
 * {组件功能描述}
 */
const {ComponentName}: React.FC<{ComponentName}Props> = (props) => {
  return (
    <div className="w-full p-4">
      {/* 组件内容 */}
    </div>
  );
};

export default {ComponentName};
```

#### Hook

```typescript
import { useState, useEffect } from 'react';

interface Use{HookName}Return {
  // 返回值类型
}

/**
 * {Hook 功能描述}
 */
export const use{HookName} = (): Use{HookName}Return => {
  const [state, setState] = useState();

  useEffect(() => {
    // 副作用逻辑
  }, []);

  return { state };
};
```

#### 工具函数

```typescript
/**
 * {函数功能描述}
 */
export const functionName = (params: ParamType): ReturnType => {
  // 函数逻辑
  return result;
};
```

### 4. 生成配套文件（仅用户明确要求时）

**样式文件** (index.less)：

```less
.container {
  // 样式定义
}
```

**测试文件** (index.test.tsx)：

```typescript
import { render } from '@testing-library/react';
import {ComponentName} from './index';

describe('{ComponentName}', () => {
  it('should render', () => {
    render(<{ComponentName} />);
  });
});
```

## 文件结构

```
{ComponentName}/
├── index.tsx        # 必需
├── index.less       # 可选（Less方案）
├── types.ts         # 可选
├── const.ts         # 可选
└── utils.ts         # 可选
```

## 相关文件

- `.codex/rules/component-naming.md` - 命名规范
- `.codex/rules/code-quality.md` - 代码质量
