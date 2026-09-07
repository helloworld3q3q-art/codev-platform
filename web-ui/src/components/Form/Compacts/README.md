# Compacts 通用紧凑表单组件

## 问题背景

在使用 Ant Design 的 `Space.Compact` 包裹各种表单组件时，会遇到以下问题：

- 无法正确回显值
- Form.Item 的 value 属性无法正确传递
- 表单验证可能失效

## 解决方案

`Compacts` 组件通过显式传递 `value` 和 `onChange` 属性，解决了 `Space.Compact` 与各种表单组件组合时的兼容性问题。支持在主表单组件后面添加额外的表单元素，相关 API 可以从外部传入。

## 支持的组件类型

- ✅ `input` - 输入框（默认类型）
- ✅ `textArea` - 文本域
- ✅ `select` - 选择器
- ✅ `datePicker` - 日期选择器
- ✅ `inputNumber` - 数字输入框
- ✅ `enhancedPagedSearchSelect` - 增强分页搜索选择器

## 使用方法

### 1. 默认输入框（不指定 type）

```tsx
import Compacts from '@/components/Form/Compacts';

<Form.Item name="username" label="用户名">
  <Compacts
    inputProps={{
      placeholder: '请输入用户名',
      maxLength: 50,
    }}
  />
</Form.Item>;
```

### 2. 日期选择器

```tsx
<Form.Item name="date" label="日期">
  <Compacts
    type="datePicker"
    datePickerProps={{
      placeholder: '请选择日期',
      format: 'YYYY-MM-DD',
    }}
  />
</Form.Item>
```

### 3. 文本域

```tsx
<Form.Item name="description" label="描述">
  <Compacts
    type="textArea"
    textAreaProps={{
      placeholder: '请输入描述',
      rows: 4,
      maxLength: 500,
    }}
  />
</Form.Item>
```

### 4. 选择器

```tsx
<Form.Item name="status" label="状态">
  <Compacts
    type="select"
    selectProps={{
      placeholder: '请选择状态',
      options: [
        { label: '启用', value: 'active' },
        { label: '禁用', value: 'inactive' },
      ],
    }}
  />
</Form.Item>
```

### 5. 数字输入框

```tsx
<Form.Item name="amount" label="金额">
  <Compacts
    type="inputNumber"
    inputNumberProps={{
      placeholder: '请输入金额',
      min: 0,
      precision: 2,
    }}
  />
</Form.Item>
```

### 6. 增强分页搜索选择器

```tsx
<Form.Item name="supplier" label="供应商">
  <Compacts
    type="enhancedPagedSearchSelect"
    enhancedPagedSearchSelectProps={{
      url: 'SUPPLIER', // 必需的属性
      labelInValue: true,
      fetchOptions: baseType,
      placeholder: '请选择供应商',
      params: { status: 'active' }, // 可选的查询参数
    }}
  />
</Form.Item>
```

**重要说明**：对于 `enhancedPagedSearchSelect` 类型，`enhancedPagedSearchSelectProps` 是必需的，且必须包含 `url` 属性。

### 7. 显示额外的表单元素

```tsx
// 显示额外的 FormItem 包裹 Input（默认行为）
<Form.Item name="mainField" label="主字段">
  <Compacts
    type="input"
    extraFormItemProps={{
      name: "extraField",
      // 其他 FormItem 属性
    }}
    extraInputProps={{
      placeholder: "额外输入框",
      style: { width: 200 }
    }}
    inputProps={{
      placeholder: "主输入框"
    }}
  />
</Form.Item>

// 只显示额外的 Input（不包裹 FormItem）
<Form.Item name="mainField2" label="主字段2">
  <Compacts
    type="datePicker"
    showExtraFormItem={false}
    showExtraInput={true}
    extraElementWidth="40%"
    extraInputProps={{
      placeholder: "额外输入框"
    }}
    datePickerProps={{
      placeholder: "请选择日期"
    }}
  />
</Form.Item>

// 完全不显示额外元素
<Form.Item name="mainField3" label="主字段3">
  <Compacts
    type="select"
    showExtraFormItem={false}
    showExtraInput={false}
    selectProps={{
      placeholder: "请选择",
      options: [
        { label: '选项1', value: 'option1' },
        { label: '选项2', value: 'option2' }
      ]
    }}
  />
</Form.Item>
```

### 8. 自定义渲染额外元素

```tsx
import { Form, Select, DatePicker, Input, InputNumber } from 'antd';

// 自定义渲染多个额外元素
<Form.Item name="mainField" label="主字段">
  <Compacts
    type="input"
    renderExtraElements={() => (
      <>
        <Form.Item name="extraField1" noStyle>
          <Select
            placeholder="请选择状态"
            style={{ width: 120 }}
            options={[
              { label: '启用', value: 'active' },
              { label: '禁用', value: 'inactive' }
            ]}
          />
        </Form.Item>
        <Form.Item name="extraField2" noStyle>
          <DatePicker
            placeholder="请选择日期"
            style={{ width: 140 }}
          />
        </Form.Item>
      </>
    )}
    inputProps={{
      placeholder: "主输入框"
    }}
  />
</Form.Item>

// 复杂的自定义渲染
<Form.Item name="complexField" label="复杂字段">
  <Compacts
    type="select"
    renderExtraElements={() => (
      <Form.Item name="extraInfo" noStyle>
        <Input.Group compact>
          <Input style={{ width: '50%' }} placeholder="前缀" />
          <InputNumber style={{ width: '50%' }} placeholder="数值" />
        </Input.Group>
      </Form.Item>
    )}
    selectProps={{
      placeholder: "请选择类型",
      options: [
        { label: '类型1', value: 'type1' },
        { label: '类型2', value: 'type2' }
      ]
    }}
  />
</Form.Item>
```

### 9. 自定义额外元素宽度

可以通过 `extraElementWidth` 属性自定义额外元素的宽度：

```tsx
// 设置额外元素宽度为 50%
<Form.Item name="field1" label="字段1">
  <Compacts
    type="input"
    extraElementWidth="50%"
    renderExtraElements={() => (
      <Form.Item name="extraField1" noStyle>
        <Select placeholder="请选择" options={options} />
      </Form.Item>
    )}
    inputProps={{ placeholder: "主输入框" }}
  />
</Form.Item>

// 设置额外元素宽度为固定像素值
<Form.Item name="field2" label="字段2">
  <Compacts
    type="datePicker"
    extraElementWidth={200}
    extraInputProps={{ placeholder: "备注" }}
    datePickerProps={{ placeholder: "请选择日期" }}
  />
</Form.Item>

// 设置额外元素宽度为 auto
<Form.Item name="field3" label="字段3">
  <Compacts
    type="select"
    extraElementWidth="auto"
    renderExtraElements={() => (
      <Form.Item name="extraField3" noStyle>
        <Input placeholder="自适应宽度" />
      </Form.Item>
    )}
    selectProps={{ placeholder: "请选择", options: options }}
  />
</Form.Item>
```

### 10. 自定义 onChange 处理

如果需要自定义 onChange 处理逻辑，应该在 Form.Item 级别处理：

```tsx
<Form.Item
  name="supplier"
  label="供应商"
  onChange={(value) => {
    // 处理自定义业务逻辑
    console.log('供应商变化:', value);
    // 可以设置其他表单字段的值
    form.setFieldsValue({ supplierRegion: value?.region });
  }}
>
  <Compacts
    type="enhancedPagedSearchSelect"
    enhancedPagedSearchSelectProps={{
      url: 'SUPPLIER',
      labelInValue: true,
      placeholder: '请选择供应商',
    }}
  />
</Form.Item>
```

## 属性说明

### 通用属性

| 属性                | 类型                 | 默认值  | 说明                            |
| ------------------- | -------------------- | ------- | ------------------------------- |
| type                | CompactsType         | 'input' | 表单组件类型                    |
| compact             | boolean              | true    | 是否使用紧凑布局                |
| value               | any                  | -       | 表单项的值（由 Form.Item 传递） |
| onChange            | (value: any) => void | -       | 值变化回调（由 Form.Item 传递） |
| style               | CSSProperties        | -       | 样式                            |
| showExtraFormItem   | boolean              | true    | 是否显示额外的 FormItem         |
| extraFormItemProps  | FormItemProps        | {}      | 额外 FormItem 的属性            |
| showExtraInput      | boolean              | true    | 是否显示额外的 Input            |
| extraInputProps     | InputProps           | {}      | 额外 Input 的属性               |
| renderExtraElements | () => ReactNode      | -       | 自定义渲染额外元素的函数        |
| extraElementWidth   | string \| number     | '30%'   | 额外元素的宽度                  |

### 特定属性

| 类型 | 属性名 | 说明 |
| --- | --- | --- |
| input | inputProps | Input 组件的属性（除了 value 和 onChange） |
| textArea | textAreaProps | TextArea 组件的属性（除了 value 和 onChange） |
| select | selectProps | Select 组件的属性（除了 value 和 onChange） |
| datePicker | datePickerProps | DatePicker 组件的属性（除了 value 和 onChange） |
| inputNumber | inputNumberProps | InputNumber 组件的属性（除了 value 和 onChange） |
| enhancedPagedSearchSelect | enhancedPagedSearchSelectProps | EnhancedPagedSearchSelect 组件的属性（除了 value 和 onChange）。**注意：此属性是必需的，必须包含 url** |

**重要说明**：所有的 `xxxProps` 都使用了 `Omit<OriginalProps, 'value' | 'onChange'>` 类型，这意味着：

- ✅ 可以传递原组件的其他所有属性（如 `placeholder`、`disabled`、`style` 等）
- ❌ 不能传递 `value` 和 `onChange`，这些由 `Compacts` 自动处理

## 特性

- ✅ 完全兼容各种表单组件的属性
- ✅ 正确处理表单值回显
- ✅ 支持表单验证
- ✅ 可选择是否启用紧凑布局
- ✅ 完整的 TypeScript 类型支持
- ✅ 统一的 API 设计
- ✅ 支持在主表单组件后面添加额外的表单元素
- ✅ 灵活配置额外元素的显示和属性
- ✅ 支持自定义渲染额外元素，具有最大灵活性
- ✅ 可配置额外元素的宽度，支持百分比、像素值或 auto
- ✅ 使用强类型定义，避免 any 类型的使用
- ✅ 简化的 onChange 处理逻辑，更加直接和高效

## 禁用紧凑布局

如果不需要紧凑布局，可以设置 `compact={false}`：

```tsx
<Form.Item name="date" label="日期">
  <Compacts
    type="datePicker"
    compact={false}
    datePickerProps={{
      placeholder: '请选择日期',
      format: 'YYYY-MM-DD',
    }}
  />
</Form.Item>
```

## 完整示例

```tsx
import React from 'react';
import { Form, Row, Col, Select, DatePicker, Input, InputNumber } from 'antd';
import Compacts from '@/components/Form/Compacts';

const MyForm = () => {
  const [form] = Form.useForm();

  return (
    <Form form={form}>
      <Row gutter={16}>
        <Col span={6}>
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Compacts
              style={{ width: '100%' }}
              showExtraFormItem={false}
              showExtraInput={false}
              inputProps={{
                placeholder: '请输入用户名',
                maxLength: 50,
              }}
            />
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item
            name="availabilityDate"
            label="生效日期"
            rules={[{ required: true, message: '请选择生效日期' }]}
          >
            <Compacts
              type="datePicker"
              style={{ width: '100%' }}
              showExtraFormItem={false}
              showExtraInput={false}
              datePickerProps={{
                format: 'YYYY-MM-DD',
                placeholder: '请选择生效日期',
              }}
            />
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item name="amount" label="金额" rules={[{ required: true, message: '请输入金额' }]}>
            <Compacts
              type="inputNumber"
              style={{ width: '100%' }}
              showExtraFormItem={false}
              showExtraInput={false}
              inputNumberProps={{
                placeholder: '请输入金额',
                min: 0,
                precision: 2,
              }}
            />
          </Form.Item>
        </Col>
        <Col span={6}>
          <Form.Item
            name="supplier"
            label="供应商"
            rules={[{ required: true, message: '请选择供应商' }]}
            onChange={(value) => {
              // 处理供应商变化的业务逻辑
              console.log('供应商变化:', value);
            }}
          >
            <Compacts
              type="enhancedPagedSearchSelect"
              style={{ width: '100%' }}
              extraElementWidth="25%"
              renderExtraElements={() => (
                <Form.Item name="supplierCode" noStyle>
                  <Input placeholder="供应商代码" />
                </Form.Item>
              )}
              enhancedPagedSearchSelectProps={{
                labelInValue: true,
                url: 'SUPPLIER',
                fetchOptions: baseType,
                placeholder: '请选择供应商',
              }}
            />
          </Form.Item>
        </Col>
      </Row>
    </Form>
  );
};
```

## 使用方式总结

### 1. 基础用法（不显示额外元素）

```tsx
<Form.Item name="date" label="日期">
  <Compacts
    type="datePicker"
    showExtraFormItem={false}
    showExtraInput={false}
    datePickerProps={{
      placeholder: '请选择日期',
      format: 'YYYY-MM-DD',
    }}
  />
</Form.Item>
```

### 2. 使用默认额外元素配置

```tsx
<Form.Item name="field" label="字段">
  <Compacts
    type="input"
    // showExtraFormItem={true} // 默认为 true
    // showExtraInput={true} // 默认为 true
    extraFormItemProps={{ name: 'extraField' }}
    extraInputProps={{ placeholder: '额外输入框' }}
    inputProps={{ placeholder: '主输入框' }}
  />
</Form.Item>
```

### 3. 使用自定义渲染函数（推荐）

```tsx
<Form.Item name="field" label="字段">
  <Compacts
    type="input"
    renderExtraElements={() => (
      <Form.Item name="extraField" noStyle>
        <Select placeholder="请选择" options={options} />
      </Form.Item>
    )}
  />
</Form.Item>
```

### 4. 自定义额外元素宽度

```tsx
<Form.Item name="field" label="字段">
  <Compacts
    type="input"
    extraElementWidth="40%"
    renderExtraElements={() => (
      <Form.Item name="extraField" noStyle>
        <Select placeholder="请选择" options={options} />
      </Form.Item>
    )}
    inputProps={{ placeholder: '主输入框' }}
  />
</Form.Item>
```

### 5. 自定义 onChange 处理

```tsx
<Form.Item
  name="field"
  label="字段"
  onChange={(value) => {
    // 自定义业务逻辑
    console.log('字段变化:', value);
  }}
>
  <Compacts type="input" inputProps={{ placeholder: '请输入' }} />
</Form.Item>
```

## 对比原生方式

```tsx
// 原来的方式（有问题）
<Form.Item name="date" label="日期">
  <Space.Compact>
    <DatePicker placeholder="请选择日期" /> {/* 无法回显值 */}
  </Space.Compact>
</Form.Item>

// 现在的方式（完美解决）
<Form.Item name="date" label="日期">
  <Compacts
    type="datePicker"
    showExtraFormItem={false}
    showExtraInput={false}
    datePickerProps={{
      placeholder: "请选择日期",
      format: "YYYY-MM-DD"
    }}
  />
</Form.Item>

// 带额外元素的复合组件（自定义宽度）
<Form.Item name="complexField" label="复合字段">
  <Compacts
    type="datePicker"
    extraElementWidth="35%"
    renderExtraElements={() => (
      <Form.Item name="timeField" noStyle>
        <Input placeholder="时间补充" />
      </Form.Item>
    )}
    datePickerProps={{
      placeholder: "请选择日期"
    }}
  />
</Form.Item>
```

## 最佳实践

1. **优先使用自定义渲染函数**：`renderExtraElements` 提供最大的灵活性，可以实现任何复杂的额外元素组合
2. **合理使用 Form.Item noStyle**：在自定义渲染函数中使用 `noStyle` 避免额外的布局影响
3. **保持组件简洁**：主表单组件专注于核心功能，额外元素通过自定义渲染函数实现
4. **充分利用 Space.Compact**：组件会自动处理紧凑布局，无需手动处理 `value` 和 `onChange` 传递问题
5. **使用强类型定义**：所有属性都使用正确的 TypeScript 类型定义，避免使用 `any` 类型
6. **在 Form.Item 级别处理自定义逻辑**：如果需要自定义 onChange 处理，应该在 Form.Item 级别实现
7. **合理设置额外元素显示**：根据实际需要设置 `showExtraFormItem` 和 `showExtraInput`，避免不必要的额外元素
8. **合理设置额外元素宽度**：根据实际需要设置 `extraElementWidth`，支持百分比、像素值或 auto

## 常见问题

### Q: 使用 `enhancedPagedSearchSelect` 时报错"缺少属性 url"

**A**: 对于 `enhancedPagedSearchSelect` 类型，`enhancedPagedSearchSelectProps` 是必需的，且必须包含 `url` 属性。

```tsx
// ❌ 错误：缺少 enhancedPagedSearchSelectProps
<Compacts type="enhancedPagedSearchSelect" />

// ❌ 错误：缺少 url 属性
<Compacts
  type="enhancedPagedSearchSelect"
  enhancedPagedSearchSelectProps={{
    placeholder: "请选择"
  }}
/>

// ✅ 正确：包含必需的 url 属性
<Compacts
  type="enhancedPagedSearchSelect"
  enhancedPagedSearchSelectProps={{
    url: "SUPPLIER",
    placeholder: "请选择供应商"
  }}
/>
```

### Q: 报错"onChange 不在类型中"

**A**: 不要在各种 `xxxProps` 中传递 `onChange` 和 `value`，这些属性由 `Compacts` 自动处理。

```tsx
// ❌ 错误：不要在 enhancedPagedSearchSelectProps 中传递 onChange
<Compacts
  type="enhancedPagedSearchSelect"
  enhancedPagedSearchSelectProps={{
    url: "SUPPLIER",
    onChange: (value) => console.log(value), // 🚫 这会导致类型错误
    value: someValue  // 🚫 这也会导致类型错误
  }}
/>

// ✅ 正确：在 Form.Item 级别处理 onChange
<Form.Item
  name="supplier"
  onChange={(value) => console.log('表单值变化:', value)}
>
  <Compacts
    type="enhancedPagedSearchSelect"
    enhancedPagedSearchSelectProps={{
      url: "SUPPLIER",
      placeholder: "请选择供应商"
    }}
  />
</Form.Item>
```

**原因**: `Compacts` 会自动从 `Form.Item` 接收 `value` 和 `onChange`，并直接传递给内部组件。所有的 `xxxProps` 类型都使用了 `Omit<OriginalProps, 'value' | 'onChange'>` 来排除这些属性。

### Q: 如何处理复杂的 onChange 逻辑？

**A**: 应该在 Form.Item 级别处理自定义 onChange 逻辑，而不是在组件内部。

```tsx
// ✅ 正确：在 Form.Item 级别处理复杂逻辑
<Form.Item
  name="supplier"
  onChange={(value) => {
    // 处理复杂的业务逻辑
    console.log('供应商变化:', value);
    // 联动设置其他字段
    form.setFieldsValue({
      supplierRegion: value?.region,
      supplierType: value?.type,
    });
  }}
>
  <Compacts
    type="enhancedPagedSearchSelect"
    enhancedPagedSearchSelectProps={{
      url: 'SUPPLIER',
      labelInValue: true,
      placeholder: '请选择供应商',
    }}
  />
</Form.Item>
```

### Q: 默认显示额外元素，如何关闭？

**A**: 通过设置 `showExtraFormItem={false}` 和 `showExtraInput={false}` 来关闭额外元素的显示。

```tsx
// ✅ 关闭额外元素显示
<Form.Item name="field" label="字段">
  <Compacts
    type="input"
    showExtraFormItem={false}
    showExtraInput={false}
    inputProps={{ placeholder: '请输入' }}
  />
</Form.Item>
```

### Q: 如何自定义额外元素的宽度？

**A**: 通过 `extraElementWidth` 属性设置额外元素的宽度，支持百分比、像素值或 auto。

```tsx
// ✅ 使用百分比
<Form.Item name="field1" label="字段1">
  <Compacts
    type="input"
    extraElementWidth="50%"
    renderExtraElements={() => (
      <Form.Item name="extraField" noStyle>
        <Select placeholder="请选择" options={options} />
      </Form.Item>
    )}
  />
</Form.Item>

// ✅ 使用像素值
<Form.Item name="field2" label="字段2">
  <Compacts
    type="datePicker"
    extraElementWidth={150}
    extraInputProps={{ placeholder: "备注" }}
  />
</Form.Item>

// ✅ 使用 auto 自适应
<Form.Item name="field3" label="字段3">
  <Compacts
    type="select"
    extraElementWidth="auto"
    renderExtraElements={() => (
      <Form.Item name="extraField" noStyle>
        <Input placeholder="自适应宽度" />
      </Form.Item>
    )}
  />
</Form.Item>
```
