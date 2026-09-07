# Antd 6 废弃 API 适配规范（2026-05-16 起）

> 项目 `antd ^6.3.1`，多个 antd 4/5 时代的 props 已 `@deprecated`，新代码必须用新 API。本规范为今日（2026-05-16）一次性修复 60+ 处旧用法（22 Card + 35+ Statistic + 10 Alert）后的沉淀。相关现存条目：`.claude/rules/component-patterns.md §Antd 6.x 废弃 API 规范`。

---

## 1. 一览速查表

| 旧 API | 新 API | 关键差异 |
| --- | --- | --- |
| `<Card className="mb-16">` | `<Card classNames={{ root: 'i:mb-16' }}>` | `className` 在 antd 6 Card 上**不再作用于 root 节点**，需用 `classNames.root` |
| `<Statistic valueStyle={{ color }}>` | `<Statistic styles={{ content: { color } }}>` | `valueStyle` 已 deprecated，改 `styles.content` |
| `<Alert message="...">` | `<Alert title="...">` | `message` 重命名为 `title`（description 不变） |
| `<Drawer destroyOnClose>` | `<Drawer destroyOnHidden>` | 命名统一（Modal 同理） |
| `<Modal destroyOnClose>` | `<Modal destroyOnHidden>` | 同上 |
| `Modal.confirm({...})` | `import { showConfirm } from '@/components/Modal'; showConfirm({...})` | 项目封装版，避免直接拿 antd 静态方法（context 丢失问题） |
| `<Select showSearch filterOption={fn}>` | `<Select showSearch={{ filterOption: fn }}>` | 顶层 `filterOption` 已 deprecated（见 component-patterns.md） |

---

## 2. Card 的 `i:` 前缀（关键设计决策）

### 为什么 `classNames.root` 还要加 `i:` 前缀？

Antd 6 Card 内部 root 节点带默认 `margin` / `padding`，UnoCSS 生成的 `mb-16` 优先级与 antd 内置样式同级，**经常被覆盖**。`i:` 是 UnoCSS 的 `!important` 前缀，确保布局类胜出。

```tsx
// ❌ 不生效（被 antd 内置 margin-bottom 覆盖）
<Card classNames={{ root: 'mb-16' }}>

// ✅ 用 i: 前缀强制
<Card classNames={{ root: 'i:mb-16' }}>
```

仅布局类（`mb-*` / `mt-*` / `mx-*` / `p-*`）需要 `i:`；颜色/字体类一般无冲突。

### 完整修复示例

```tsx
// ❌ before
<Card className="mb-16" title="胜率汇总">
  <Statistic title="命中率" value={hitRate} suffix="%" valueStyle={{ color: '#cf1322' }} />
</Card>

// ✅ after
<Card classNames={{ root: 'i:mb-16' }} title="胜率汇总">
  <Statistic
    title="命中率"
    value={hitRate}
    suffix="%"
    styles={{ content: { color: '#cf1322' } }}
  />
</Card>
```

---

## 3. Statistic `valueStyle` → `styles.content`

### ⚠️ 例外：`@ant-design/pro-components` 的 `StatisticCard` 不要改！

`StatisticCard` 来自 pro-components，**未跟进 antd 6 styles.content**，其 `statistic={{...}}` 子对象仍然只认 `valueStyle`。

```tsx
// ✅ pro-components StatisticCard 保持 valueStyle
import { StatisticCard } from '@ant-design/pro-components';

<StatisticCard
  statistic={{
    title: 'X',
    value: v,
    valueStyle: { color: priceColor(v) },  // ✓ 不是 styles.content
  }}
/>

// ❌ 错（会让样式失效）
<StatisticCard
  statistic={{
    title: 'X',
    value: v,
    styles: { content: { color: priceColor(v) } },
  }}
/>
```

**判定**：import 来自 `antd` 用 `styles.content`；import 来自 `@ant-design/pro-components` 用 `valueStyle`。

### Antd 原生 Statistic：用 styles.content

`valueStyle` 仍能跑但控制台 warn；新代码全部用 `styles.content`。颜色推荐用 `@/utils/stockcolor.ts`（见 `stock-color-convention.md`）：

```tsx
import { priceColor, healthColor } from '@/utils/stockcolor';

// ❌ before
<Statistic title="收益率" value={ret} valueStyle={{ color: ret >= 0 ? '#cf1322' : '#389e0d' }} />

// ✅ after
<Statistic title="收益率" value={ret} styles={{ content: { color: priceColor(ret) } }} />

// ✅ 健康类指标（高绿低红）
<Statistic title="命中率" value={hitRate} styles={{ content: { color: healthColor(hitRate) } }} />
```

---

## 4. Alert `message` → `title`

```tsx
// ❌ before
<Alert type="warning" message="样本不足 50 笔" description="Track 4 解锁需 ≥50 CLOSED" />

// ✅ after
<Alert type="warning" title="样本不足 50 笔" description="Track 4 解锁需 ≥50 CLOSED" />
```

注意：`description` 字段名不变。

---

## 5. Drawer / Modal `destroyOnClose` → `destroyOnHidden`

参考 `component-patterns.md §destroyOnClose → destroyOnHidden`，行为完全一致，仅命名变化。

---

## 6. `Modal.confirm` → `showConfirm`

```tsx
// ❌ before
import { Modal } from 'antd';
Modal.confirm({ title: '确认删除？', onOk: handleDelete });

// ✅ after
import { showConfirm } from '@/components/Modal';
showConfirm({ title: '确认删除？', onOk: handleDelete });
```

**为什么**：antd 6 静态方法（`Modal.confirm` / `message.success` / `notification.open`）需要 `App.useApp()` 上下文才能拿到正确的 theme / ConfigProvider，项目 `@/components/Modal` 已封装 `useApp` hook 调用，业务代码无需关心。

---

## 7. grep 自检命令

PR 提交前在 `apps/stock-admin-web/src/` 下扫一遍：

```bash
# Card className 误用（应为 classNames.root）
grep -rn "<Card[^>]*className=" src/pages src/components

# Statistic valueStyle 残留
grep -rn "valueStyle=" src/

# Alert message= 残留
grep -rn "<Alert[^>]*message=" src/

# destroyOnClose 残留
grep -rn "destroyOnClose" src/

# 直接拿 Modal.confirm（应用 showConfirm）
grep -rn "Modal\.confirm" src/pages
```

每条 grep 输出应为空（或仅命中 `@/components/Modal` 内部实现）。

---

## 8. PR 自检清单

- [ ] 新增 Card 是否用 `classNames={{ root: 'i:xxx' }}` 而非 `className`？
- [ ] Statistic 是否用 `styles={{ content: {...} }}` 而非 `valueStyle`？
- [ ] Alert 是否用 `title` 而非 `message`？
- [ ] Drawer / Modal 是否用 `destroyOnHidden`？
- [ ] 确认弹窗是否用 `showConfirm` 而非 `Modal.confirm`？
- [ ] 颜色是否走 `@/utils/stockcolor.ts` 而非散落硬编码 hex？

---

## 9. 受影响范围（2026-05-16 一次性修复）

- 22 处 `<Card className="...">` → `classNames.root`
- 35+ 处 `<Statistic valueStyle={...}>` → `styles.content`
- 10 处 `<Alert message="...">` → `title`
- 主要文件：`pages/recommendpnl/` / `pages/backtest/` / `pages/dashboard/` / `pages/sampleprogress/` / `pages/dataintegrity/` / `components/StockDetailDrawer/*`

历史漂移成本极高 —— 后续新增页面**必须**遵循本规范，避免下次 antd 大版本再次大面积扫除。
