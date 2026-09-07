# 组件使用模式规范

---

## ResizableTable 使用规范

```typescript
// utils.tsx：定义参数转换函数（ResizableTable 内部已将 current → pageNum）
export const convertParams = (params: Record<string, any>) => ({
  pageNumber: params.pageNum,
  pageSize: params.pageSize,
  // ...其他业务参数
});

// index.tsx
import ResizableTable, { requestWrapper } from '@/components/ResizableTable';
import toolBarRender from './components/ToolBarRender';
import { convertParams } from './components/utils';

const handleTableRequest = useCallback(
  async (params: Record<string, any>, sort: any, filter: any) =>
    requestWrapper(params, sort, filter, postPageQuery, convertParams),
  [],
);

const handleToolBarRender = useCallback(() => toolBarRender({ onAdd: handleAdd }), [handleAdd]);

<ResizableTable
  request={handleTableRequest}
  toolBarRender={handleToolBarRender}
  scroll={{ x: 1200 }}
  pagination={{ defaultPageSize: 10 }}
  search={{ span: 6, layout: 'vertical', defaultCollapsed: true }}
  form={{ layout: 'vertical', colon: false }}
/>
```

**标准配置说明**：

- `search.span: 6` — 搜索项每行 4 列（24/6=4）
- `search.defaultCollapsed: true` — 默认折叠搜索区
- `form.layout: 'vertical'` — 搜索表单标签上方
- `form.colon: false` — 去掉标签后的冒号

**注意**：`Columns.tsx` 中 `ProColumns` 必须从 `@ant-design/pro-components` 导入，不能从 `@/components/ResizableTable` 导入（类型不兼容）。

---

## ToolBarRender 规范

`ToolBarRender` 是**返回 React 元素数组的普通函数**，不是 React 组件。使用 `PermissionButton` + `i18nMessages`，以小驼峰导入后直接调用。

回调传递规则：**同一个按钮有多个回调时用对象分组，只有一个回调时平铺**：

```typescript
// ToolBarRender.tsx
interface ToolBarRenderProps {
  // 单个回调 → 平铺
  onImport: () => void;
  // 同一按钮多个回调 → 分组
  add: {
    onAdd: () => void;
    onBatchAdd: () => void;
  };
}

const ToolBarRender = ({ add, onImport }: ToolBarRenderProps) => [
  <PermissionButton key="add" onClick={add.onAdd}>{i18nMessages('add', '新增')}</PermissionButton>,
  <PermissionButton key="import" onClick={onImport}>{i18nMessages('import', '导入')}</PermissionButton>,
];

// index.tsx
const handleToolBarRender = useCallback(() => toolBarRender({
  add: { onAdd: handleAdd, onBatchAdd: handleBatchAdd },
  onImport: handleImport,
}), [handleAdd, handleBatchAdd, handleImport]);
```

---

## 操作列 ≥4 按钮：2×2 grid + 更多下拉模板（2026-05-16）

ProTable / ResizableTable 的"操作"列若有 4+ 按钮，**禁止平铺单行**（导致列宽 > 400px）。统一用 **3 主操作可见 + "更多 ▼" 下拉**（4 个按钮可改 2×2 grid）。

```tsx
// ❌ 4 按钮平铺，列宽 400+
render: (_, record) => [
  <Typography.Link>详情</Typography.Link>,
  <Typography.Link>指标</Typography.Link>,
  <Typography.Link>K线</Typography.Link>,
  <Typography.Link>合规留痕</Typography.Link>,
  <Typography.Link>生成计划</Typography.Link>,
];

// ✅ 3 主 + 更多下拉（列宽 ~200）
const moreItems: MenuProps['items'] = [
  { key: 'snapshot', label: '合规留痕', onClick: () => onSnapshot(record) },
  { key: 'plan', label: '生成计划', onClick: () => onPlan(record) },
  {
    key: 'convert',
    label: '转交易计划',
    onClick: () => showConfirm({ title: '...', onOk: async () => onConvert(record) }),
  },
];
return (
  <div className="grid grid-cols-2 gap-x-8 gap-y-4">
    <Typography.Link onClick={() => onDetail(record)}>详情</Typography.Link>
    <Typography.Link onClick={() => onIndicators(record)}>指标</Typography.Link>
    <Typography.Link onClick={() => onChart(record)}>K线</Typography.Link>
    <Dropdown menu={{ items: moreItems }} trigger={['click']}>
      <Typography.Link>
        更多 <DownOutlined className="text-12" />
      </Typography.Link>
    </Dropdown>
  </div>
);
```

关键点：

- 列宽设 120-220（按主操作数）
- **Dropdown menu 内嵌 Popconfirm 会冲突** → 改用 `showConfirm` 弹窗（参考 `@/components/Modal`）
- 主操作 ≤ 3 个 + 1 个下拉，超过则下沉到下拉

---

## Drawer / Modal 规范

统一使用 `@/components/Drawer` 和 `@/components/Modal`，而非 antd 原生组件。

### 内置能力（不要重复实现）

两个组件都内置了以下能力，**禁止在业务代码中手动复现**：

| 能力                        | Drawer                                | Modal            |
| --------------------------- | ------------------------------------- | ---------------- |
| 提交按钮 loading 状态       | ✅ `onOk` 执行期间自动 loading        | ✅ 同 Drawer     |
| 关闭逻辑                    | ✅ `onOk` 返回 `undefined` 时自动关闭 | ✅ 同 Drawer     |
| 默认底部按钮（取消 + 保存） | ✅ 内置                               | ✅ 内置          |
| 初始化 loading（Skeleton）  | ✅ `onLoad` prop                      | ✅ `onLoad` prop |

### Drawer 标准用法

```typescript
import Drawer from '@/components/Drawer';

// ✅ 标准：onOk 处理提交，Drawer 自动管理 loading 和关闭
const handleSubmit = useCallback(async (): Promise<boolean | void> => {
  const values = await form.validateFields(); // 校验失败会 throw，Drawer 保持打开
  await postCreate(values);
  message.success('创建成功');
  onOk();          // 通知父组件刷新
  // 不 return（即 undefined）→ Drawer 自动关闭
}, [form, onOk]);

<Drawer
  title="新增业务节点"
  open={open}
  onCancel={onCancel}   // 取消/关闭逻辑
  onOk={handleSubmit}   // 提交逻辑，loading 由组件管理
  size="large"          // large=736px / default=378px，禁止直接写 width
>
  <Form>...</Form>
</Drawer>

// ❌ 禁止：自行维护 submitLoading + 自定义 footer 重写默认按钮
const [submitLoading, setSubmitLoading] = useState(false); // ← 冗余
<Drawer
  footer={
    <div>
      <Button onClick={onCancel}>取消</Button>
      <Button loading={submitLoading} onClick={handleSubmit}>保存</Button>
    </div>
  }
>
```

**`onOk` 返回值语义**：

- 返回 `undefined`（函数无 return）→ Drawer 自动关闭
- 返回 `false` → Drawer 保持打开（校验失败等场景）
- 抛出异常 → Drawer 保持打开，loading 自动停止

**仅在以下情况使用 `footer` prop**：

- 需要三个及以上按钮
- 按钮文案、顺序与默认不同
- 有额外非按钮内容

**`showOkButton` / `showCancelButton`**：只需隐藏某个按钮时，用这两个 prop，无需重写整个 footer：

```typescript
// ✅ 隐藏确定按钮（纯展示 Drawer）
<Drawer open={open} onCancel={onCancel} showOkButton={false}>

// ✅ 异步初始化用 onLoad（自动显示 Skeleton）
<Drawer open={open} onCancel={onCancel} onOk={handleSubmit} onLoad={loadDetail}>
```

### Modal 标准用法

```typescript
import Modal from '@/components/Modal';

// ✅ 标准：有提交行为
<Modal
  title="标题"
  open={open}
  onCancel={onCancel}
  onOk={handleSubmit}  // 同 Drawer，自动管理 loading
>
  ...
</Modal>

// ✅ 纯展示（无底部按钮）
<Modal title="标题" open={open} onCancel={onCancel} footer={null}>
  ...
</Modal>

// ✅ 确认对话框：使用 showConfirm（禁止 Modal.confirm）
import { showConfirm } from '@/components/Modal';

showConfirm({
  title: '确认删除？',
  content: '此操作不可恢复',
  onOk: async () => { ... },
});
```

---

## 封装 Select 组件规范

需要远程数据的下拉选择器统一封装为独立组件，放在 `src/components/Form/Select/` 目录下，严格按照以下模式：

```typescript
// ✅ 标准模式：使用 @jlogi/ui Select + fetchOptions
import { postBusinessTypesPage } from '@/services/apis/businesstypeapi';
import type { SelectProps } from '@jlogi/ui';
import { Select } from '@jlogi/ui';
import React, { useCallback, useRef } from 'react';

interface BusinessTypeSelectProps extends Omit<SelectProps, 'fetchOptions'> {
  extraParams?: Record<string, unknown>;
}

const BusinessTypeSelect: React.FC<BusinessTypeSelectProps> = ({ extraParams, ...restProps }) => {
  // extraParams 用 useRef 追踪，避免对象引用变化导致 useCallback 重建
  const extraParamsRef = useRef(extraParams);
  extraParamsRef.current = extraParams;

  const handleFetchOptions = useCallback(
    async (params: { keyWord: string; page: number; pageSize: number }) => {
      try {
        const result = await postBusinessTypesPage({
          pageNumber: 1,
          pageSize: 10,
          ...(extraParamsRef.current ?? {}),
        });
        const list = result?.data ?? [];
        const options = list.map((item) => ({
          value: item.code ?? '',
          label: item.name ?? '',
          title: item.name ?? '',
          code: item.code,
          name: item.name ?? '',
          data: item,
        }));
        const filteredOptions = params.keyWord
          ? options.filter((opt) => opt.name?.toLowerCase().includes(params.keyWord.toLowerCase()))
          : options;
        return { data: filteredOptions, totalCounts: filteredOptions.length };
      } catch {
        return { data: [], totalCounts: 0 };
      }
    },
    [], // extraParams 通过 ref 读取，不放入依赖
  );

  return (
    <Select
      hideHeader
      hideCodeColumn
      placeholder={restProps.placeholder ?? '请选择'}
      style={restProps.style ?? { width: '100%' }}
      fetchOptions={handleFetchOptions}
      allowClear={restProps.allowClear ?? true}
      showSearchPanel={false}
      showPagination={false}
      columnsWidth={[80, 120]}
      {...restProps}
    />
  );
};
```

**关键规则**：

- 使用 `@jlogi/ui` Select，**禁止** antd Select 用于远程数据场景
- `fetchOptions` 接收 `{ keyWord, page, pageSize }`，返回 `{ data, totalCounts }`
- 组件内做客户端 `keyWord` 过滤
- `extraParams` 用 `useRef` 追踪，不加入 `useCallback` 依赖数组
- 有联动关系的依赖（如 `businessTypeCode`）是字符串，可直接放依赖数组
- `extraParams` 类型用 `Record<string, unknown>`，**禁止** `any`

**ProColumns 中使用封装 Select**：用 `formItemRender`，函数提取到列定义外部：

```typescript
// ✅ 在 createColumns 函数外定义，避免每次调用重建
const renderBusinessTypeSelect = () => <BusinessTypeSelect />;

// 列定义中使用（formItemRender 是 pro-components v3 的正确 API，v2 是 renderFormItem）
{
  dataIndex: 'businessTypeCode',
  formItemRender: renderBusinessTypeSelect,
  render: (_, record) => businessTypeMap[record.businessTypeCode ?? ''] ?? '-',
}
```

---

## Antd 6.x 适配 — 详见专项规则

项目使用 antd `^6.3.1`，多个 props 已废弃。**完整清单 + 修复模板 + grep 自检** 见 `.claude/rules/antd6-adapt.md`（含 Card.classNames.root / Statistic.styles.content / Alert.title / destroyOnHidden / Modal.confirm → showConfirm / Select.showSearch 对象形式 等 7 类）。

本文件只声明**组件层独有**约束：**禁止直接 import antd 原生 Drawer / Modal / Table，必须用项目封装版**。

| 禁止使用                        | 应使用                                                     |
| ------------------------------- | ---------------------------------------------------------- |
| `import { Drawer } from 'antd'` | `import Drawer from '@/components/Drawer'`                 |
| `import { Modal } from 'antd'`  | `import Modal from '@/components/Modal'`                   |
| `import { Table } from 'antd'`  | `import ResizableTable from '@/components/ResizableTable'` |

原因：封装版内置 loading 自动管理、`onOk` 关闭语义、`showConfirm` 替代 `Modal.confirm` 解决 antd 6 静态方法 context 丢失问题。详见本文 §Drawer / Modal 规范。

---

## Antd 组件规范

```typescript
// antd 6 Select 搜索：filterOption 必须放在 showSearch 对象内
// 顶层 showSearch（boolean）+ 顶层 filterOption 在 antd 6 已废弃（@deprecated），TS 会报警告
// filterOption 是纯函数，提取到组件外部避免 react/jsx-no-bind
const filterOption = (input: string, option?: { label?: string }) =>
  String(option?.label ?? '').toLowerCase().includes(input.toLowerCase());

// ✅ antd 6 正确写法
<Select showSearch={{ filterOption }} options={options} />

// ❌ 禁止：顶层 filterOption（antd 6 已废弃）
<Select showSearch filterOption={filterOption} options={options} />

// ProTable fieldProps 同理
fieldProps: {
  showSearch: { filterOption },
}

// ProColumns：search: false 时不写 formItemRender
{ dataIndex: 'status', search: false }
```
