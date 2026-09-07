# 样式规范

---

## UnoCSS 原子类

优先使用 UnoCSS 原子类，不直接写 `style={{}}`。仅在动态值（运行时计算）时才用 `style`：

```tsx
// ✅ UnoCSS 原子类
<div className="mt-16 p-12 rounded-6 whitespace-pre-wrap break-all">

// ✅ 仅动态值用 style
<div style={{ width: calcWidth }}>

// ❌ 静态值用 style
<div style={{ marginRight: 8, textAlign: 'right' }}>
```

---

## 数值单位（`baseFontSize: 4`）

项目配置 `presetRemToPx({ baseFontSize: 4 })`，**类名中的数字直接等于 px**：

```
mr-8   = 8px     mt-16  = 16px    mb-12  = 12px
p-8    = 8px     p-12   = 12px    rounded-6 = 6px border-radius
text-12 = 12px   text-14 = 14px   min-h-400 = 400px
```

**禁止使用 Tailwind 语义化尺寸**（如 `text-xs`、`text-sm` 独立使用），它们在 Wind3 preset 下会产生错误的 px 值：

```
❌ text-xs  → 0.75rem × 4 = 3px（错误）
✅ text-12  → 12px（正确）

❌ mb-3     → 3px（错误）
✅ mb-12    → 12px（正确）
```

---

## HEX 颜色语法

使用自定义规则语法，**不用任意值语法**：

```tsx
// ✅
<div className="bg-#f5f5f5 text-#333333">

// ❌ 任意值语法（项目未配置 safelist，构建可能丢失）
<div className="bg-[#f5f5f5]">
```

---

## 常用组合示例

```tsx
// 内容块背景
<div className="p-12 bg-#f5f5f5 rounded-6 whitespace-pre-wrap break-all">

// 代码/输出区域
<div className="mt-8 p-12 bg-#f0f9eb rounded-6 whitespace-pre-wrap break-all">

// 小字提示
<Typography.Text className="text-12 text-right">
```
