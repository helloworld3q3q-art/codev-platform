# 代码质量规范

本文档定义项目代码质量最佳实践，规则来自 [.eslintrc.js](../../.eslintrc.js) 的强制配置。

---

## ESLint 强制规则速查

| 规则 | 说明 |
| --- | --- |
| `no-var` | 禁止使用 `var` |
| `prefer-const` | 未重新赋值的变量必须用 `const` |
| `no-unused-vars` | 禁止声明未使用的变量、import |
| `max-depth: 4` | 代码嵌套最多 4 层 |
| `max-lines: 1200` | 文件最大 1200 行（warn） |
| `max-function-lines: 100` | 普通函数逻辑最大 100 行（JSX 不计入） |
| `max-function-lines: 900` | `export default` 组件函数最大 900 行 |
| `react/jsx-no-bind` | JSX props 中禁止内联函数（箭头函数、`.bind()`、匿名函数） |
| `filenames` (components) | `src/**/components/` 下文件名必须 PascalCase，且与 default export 名一致 |
| `filenames` (其他) | `src/` 其他文件名必须全小写 |

**特殊豁免**：`Columns.tsx` 同时豁免「函数行数」和「`react/jsx-no-bind`」（参见 `.eslintrc.js:110-115`）—— 列定义里 `onClick={() => fn(record)}` 内联箭头合法。新代码推荐拆 `ActionXxx` 子组件 + `useCallback`（参见 `src/pages/backtest/components/Columns.tsx` 的 `DetailAction` 模式），但内联写法不会被 lint 拦截。

---

## TypeScript 类型安全

- 禁止 `any`，始终声明具体类型
- 优先用可选链 `?.` 和空值合并 `??` 处理空值
- `src/services/apis/typings.d.ts` 是 `declare namespace API {}` 全局声明，**不是模块，禁止 import**，直接使用 `API.XxxType`

---

## 变量与 Import

- 属性赋值不构成重赋值，对象/数组变量统一用 `const`
- 所有 import 必须在文件顶部，禁止在函数或 `useEffect` 内动态 import
- 无未使用的变量和 import，无空的 `else-if` 块

### Import 书写规范

**排列顺序**（组间空一行）：

```typescript
// 1. React / umi
import { useCallback, useState } from 'react';
import { useModel } from '@umijs/max';

// 2. 第三方库（antd、pro-components 等）
import { Button, Form } from 'antd';
import type { ProColumns } from '@ant-design/pro-components';

// 3. 项目内部（@ 别名）
import Drawer from '@/components/Drawer';
import { postCreate } from '@/services/apis/userapi';

// 4. 当前模块相对路径
import { createColumns } from './components/Columns';
import type { FormDrawerContext } from './components/NodeFormDrawer';
```

**其他规则**：

- 三层及以上相对路径（`../../../`）一律改为 `@/` 别名
- 同一模块多次 import 合并为一条
- 纯类型导入使用 `import type`，与值导入分开

---

## 函数写法规范

函数体必须使用花括号显式写法，禁止箭头函数直接返回表达式（不便于打断点调试）。

```typescript
// ❌ 禁止隐式返回
const collectPaths = (items: Item[]) => items.flatMap((r) => [...]);

const double = (x: number) => x * 2;

// ✅ 显式函数体
const collectPaths = (items: Item[]): string[] => {
  const paths: string[] = [];
  // 可在此处打断点
  return paths;
};

const double = (x: number): number => {
  const result = x * 2;
  return result;
};
```

---

## 日期时间格式化规范(2026-05-23 起)

**业务层时间显示统一直接用 `dayjs`**,禁止自己写 `formatDateTime` / `formatDate` 这类二次封装函数。

统一格式:

- 标准日期时间:`dayjs(value).format('YYYY/MM/DD HH:mm:ss')`
- 列表列宽受限:`dayjs(value).format('YYYY/MM/DD HH:mm')`
- 仅日期:`dayjs(value).format('YYYY/MM/DD')`

空值在业务层显式判断,按页面语义返回 `-` / `--` / `—`。

```tsx
// ✅ 标准用法
import dayjs from 'dayjs';
<span>{record.updatedAt ? dayjs(record.updatedAt).format('YYYY/MM/DD HH:mm:ss') : '—'}</span>

// 列宽受限场景(列表),用简短版去秒
<span>{record.alertTime ? dayjs(record.alertTime).format('YYYY/MM/DD HH:mm') : '--'}</span>

// 仅日期
<span>{record.scoreDate ? dayjs(record.scoreDate).format('YYYY/MM/DD') : '-'}</span>

// ❌ 禁止自写封装函数
import { formatDateTime } from '@/utils/datetime';
<span>{formatDateTime(record.updatedAt)}</span>

// ❌ 禁止自己拼字符串或用 Date/Intl 另起一套时间格式
<span>{val.replace('T', ' ').slice(0, 19)}</span>
<span>{new Date(val).toLocaleString()}</span>

// ❌ 禁止短横线旧格式
<span>{dayjs(val).format('YYYY-MM-DD HH:mm')}</span>

// ❌ 禁止直接展示后端原始 ISO 字符串(含微秒)
<span>{record.intradayDiagnosticUpdatedAt}</span>   // → 2026-05-22T22:16:30.504748 难读
```

**接受的输入**:ISO 8601(含 / 不含微秒)/ 标准日期字符串 / Date / null / undefined / 空串。金额 / 数量格式化不属于本规则,可继续用 `Number(...).toLocaleString('zh-CN')`。

**grep 自检**:

```bash
# 禁止自写日期格式化封装
grep -rEn "@/utils/datetime|formatDateTime|formatDate\(" apps/stock-admin-web/src apps/stock-admin-web/components

# 自己拼 ISO 字符串 / Date / Intl 展示时间
grep -rEn "replace\('T'|slice\(0, 19\)|new Date\(|Intl\.DateTimeFormat" apps/stock-admin-web/src/pages apps/stock-admin-web/src/components

# 短横线时间格式残留:YYYY-MM-DD HH 是历史格式,统一改斜杠
grep -rn "YYYY-MM-DD HH" apps/stock-admin-web/src/pages apps/stock-admin-web/src/components
```

历史短横线格式按需逐步迁移,不为了改文档做无关大改。

---

## 代码检查清单

- [ ] **不使用 `useRequest`**（全局禁用，详见 `react-patterns.md` §禁用 useRequest）
- [ ] 无 `any`，Props 有类型定义，列表用唯一 `key`
- [ ] 变量用 `const`，无未使用的变量/import，无空 `else-if`，import 全在顶部
- [ ] 不从 `declare namespace` 文件 import 类型
- [ ] 枚举值通过 `useModel('enum')` 获取，禁止硬编码、禁止从 `enumslocal.tsx` 导入
- [ ] `useEffect` 紧挨 `return` 前，内部只做条件判断 + 调用提取好的函数，不写内联请求逻辑
- [ ] 同一数据源 / 总是一起更新的 state 合并为一个对象
- [ ] JSX props 无内联函数；`.map()` 闭包场景提取子组件
- [ ] 数据聚合到 `context` 传递，回调用 `onOk` / `onCancel`
- [ ] `ResizableTable` 用 `requestWrapper`，`toolBarRender` 直接调用外部函数
- [ ] Drawer 用 `size`，Select 搜索用 `showSearch={{ filterOption: ... }}`
- [ ] 样式优先 UnoCSS 原子类，动态值才用 `style`
- [ ] 时间显示直接用 `dayjs(value).format('YYYY/MM/DD ...')`，禁止自写日期格式化函数 / 工具封装
