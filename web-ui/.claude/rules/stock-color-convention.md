# A 股涨跌颜色规范（红涨绿跌）

> A 股市场习惯**红涨绿跌**，与欧美市场（绿涨红跌）反向。前端任何涉及涨跌、PnL、收益率、止盈止损方向的视觉颜色，**必须**遵循本规范。唯一真值源：`apps/stock-admin-web/src/utils/stockcolor.ts`。

---

## 1. 颜色常量（src/utils/stockcolor.ts）

| 常量               | 值        | 语义                                 |
| ------------------ | --------- | ------------------------------------ |
| `STOCK_UP_COLOR`   | `#cf1322` | 红涨：上涨 / 盈利 / 买入 / 止盈方向  |
| `STOCK_DOWN_COLOR` | `#389e0d` | 绿跌：下跌 / 亏损 / 卖出 / 止损方向  |
| `STOCK_FLAT_COLOR` | `#8c8c8c` | 灰中性：持平 / 无信号 / 未知         |
| `STOCK_WARN_COLOR` | `#faad14` | 黄警示：风险提示 / 待确认 / 接近阈值 |

---

## 2. 三个判定函数（分清语义！）

### 2.1 `priceColor(v)` —— 涨跌方向语义（正红负绿）

适用场景：**值的正负代表"涨"或"跌"**

- 股价涨跌额 / 涨跌幅 %
- 收益率 / 累计收益
- 持仓盈亏 PnL
- 主力资金净流入（正=流入红，负=流出绿）
- **止盈价**：相对入场价上涨 → 红
- **止损价**：相对入场价下跌 → 绿

```tsx
import { priceColor } from '@/utils/stockcolor';

<Statistic value={pnl} styles={{ content: { color: priceColor(pnl) } }} />;
```

### 2.2 `healthColor(v, goodThreshold=60, badThreshold=30)` —— 健康度语义（高绿低红）

适用场景：**值高=好（绿），值低=坏（红）**

- 命中率 / 胜率（>=60% 绿，<30% 红，中间黄）
- IC（信息系数）
- 数据完整性 / 数据健康度
- 模型置信度

**与 priceColor 反向**：因为"健康"和"涨跌"是两套不同语义系统 —— "胜率高"是好事用绿色（成功色），但"股价跌"才用绿色（下跌方向）。

```tsx
import { healthColor } from '@/utils/stockcolor';

<Statistic value={hitRate} suffix="%" styles={{ content: { color: healthColor(hitRate) } }} />;
// 默认阈值：>=60 绿、>=30 黄、<30 红
// IC 等小数指标传自定义阈值：healthColor(ic, 0.05, 0.02)
```

### 2.3 `riskColor()` —— 恒红警告

适用场景：**单调风险类指标，值越大越糟**

- 最大回撤 max_drawdown
- 风险提示 / 风险等级
- 错误率 / 异常次数

```tsx
import { riskColor } from '@/utils/stockcolor';

<Statistic value={maxDD} suffix="%" styles={{ content: { color: riskColor() } }} />;
```

---

## 3. 5 视角语义判定矩阵

| 数据类型 | 量化视角 | 投研视角 | 合规视角 | 投资者视角 | 架构视角 | 用哪个函数 |
| --- | --- | --- | --- | --- | --- | --- |
| 股价涨跌幅 | 涨=正信号 | 涨=机会 | 涨=正常波动 | 涨=赚钱（红色喜） | 时序信号 | `priceColor` |
| 持仓 PnL | 正=盈利 | 正=策略有效 | 正=合规盈利 | 正=高兴（红） | 累计指标 | `priceColor` |
| 止盈价 | 上方目标 | 退出点 | 风险释放 | 涨到这卖（红箭头向上） | 阈值 | `priceColor(diff)` |
| **止损价** | **下方触发** | **风控线** | **强制减仓** | **跌到这斩（绿箭头向下）** | **阈值** | **`priceColor(diff)` 自然为负→绿** |
| 命中率 | 越高越好 | 策略成熟度 | 历史业绩 | 信赖度 | KPI | `healthColor` |
| IC 因子 | 越高越好 | 因子有效性 | - | - | 模型指标 | `healthColor(ic, 0.05, 0.02)` |
| 最大回撤 | 越大越糟 | 风险暴露 | 风控警报 | 心慌（红色警） | 风险指标 | `riskColor()` |

**关键易错点**：**止损价用绿** —— 很多新人会下意识"止损 = 风险 = 红"，但 A 股语义下止损在入场价**下方**（跌方向），与卖出/亏损同源，必须绿。`priceColor(stopLossPrice - entryPrice)` 自然为负 → 绿，**不要硬编码红色**。

---

## 4. ❌ 常见误用 / ✅ 修正

### 误用 1：散落 colorByValue 函数

```tsx
// ❌ 各组件自建一份，颜色值还不一致
const colorByValue = (v: number) => (v >= 0 ? 'red' : 'green');

// ✅ 统一引用
import { priceColor } from '@/utils/stockcolor';
```

### 误用 2：颜色和语义错位

```tsx
// ❌ 把胜率（健康类）当涨跌处理
<span style={{ color: hitRate >= 50 ? '#cf1322' : '#389e0d' }}>{hitRate}%</span>
// 这会让"胜率 49%"显示绿色（看似"跌"），但 49% 胜率其实是中等表现，应黄色

// ✅ 用 healthColor
<span style={{ color: healthColor(hitRate) }}>{hitRate}%</span>
```

### 误用 3：止损硬编码红

```tsx
// ❌ 直觉错误
<span style={{ color: '#cf1322' }}>止损价: {stopLoss}</span>;

// ✅ 用方向判定
const diff = stopLoss - entryPrice; // 必为负
<span style={{ color: priceColor(diff) }}>止损价: {stopLoss}</span>;
```

### 误用 4：欧美习惯写反

```tsx
// ❌ 来自国际化项目的复用代码
const upColor = '#52c41a'; // 绿色 = 涨（欧美）
const downColor = '#ff4d4f'; // 红色 = 跌（欧美）

// ✅ A 股反向，必须用本项目常量
import { STOCK_UP_COLOR, STOCK_DOWN_COLOR } from '@/utils/stockcolor';
```

---

## 5. ECharts / 图表场景

K 线图 / 资金流图同理走 `STOCK_UP_COLOR` / `STOCK_DOWN_COLOR`：

```ts
import { STOCK_UP_COLOR, STOCK_DOWN_COLOR } from '@/utils/stockcolor';

const klineOption = {
  series: [
    {
      type: 'candlestick',
      itemStyle: {
        color: STOCK_UP_COLOR, // 阳线（涨）红
        color0: STOCK_DOWN_COLOR, // 阴线（跌）绿
        borderColor: STOCK_UP_COLOR,
        borderColor0: STOCK_DOWN_COLOR,
      },
    },
  ],
};
```

---

## 6. ⚠️ UnoCSS 动态类名陷阱（2026-05-16 实战教训）

### 反面案例（颜色失效）

```ts
// ❌ utils.ts：函数返回 UnoCSS hex 类名字符串
export const colorByChange = (v) => {
  if (v > 0) return 'text-#cf1322';
  if (v < 0) return 'text-#389e0d';
  return 'text-#888';
};

// ❌ MarketIndexCard.tsx：模板字符串拼接
<div className={`text-20 font-600 ${colorByChange(v)}`}>{value}</div>
```

**症状**：Dashboard 大盘指数 "-1.12%" 等下跌数据全部显示**灰色 #888**，不是预期绿色。

**根因**：UnoCSS scanner 静态扫源码字面量类名，**返回值动态拼接的 `text-#hex` 模式扫不到** → CSS 未生成 → 类失效 → fallback 到继承色。

### 正确做法 ✅

**模式 A：直接用 `@/utils/stockcolor.ts` hex 常量 + `style` 注入**（推荐）

```tsx
import { STOCK_UP_COLOR, STOCK_DOWN_COLOR, STOCK_FLAT_COLOR } from '@/utils/stockcolor';

// utils.ts 返回 hex 而非类名
export const colorByChange = (v): string => {
  if (v > 0) return STOCK_UP_COLOR;
  if (v < 0) return STOCK_DOWN_COLOR;
  return STOCK_FLAT_COLOR;
};

// 组件用 style 注入（不依赖 UnoCSS 扫描）
<div className="text-20 font-600" style={{ color: colorByChange(v) }}>
  {value}
</div>;
```

**模式 B：在 className 字符串里写"字面量"hex 类**（仅静态场景）

```tsx
// ✅ UnoCSS scanner 看得见 'text-#cf1322' 字面量
<div className={isUp ? 'text-#cf1322' : 'text-#389e0d'}>{value}</div>
```

**注意**：模式 B 不能用函数返回类名 + 模板拼接的方式。**只要 className 表达式里出现 `${fn(...)}` 返回 hex 类，UnoCSS 都可能漏扫**。

### 何时用 style 何时用 className

| 场景                                                 | 推荐                                 |
| ---------------------------------------------------- | ------------------------------------ |
| 颜色由 JS 逻辑决定（涨跌、健康度判定）               | **style + hex 常量**（模式 A）       |
| 颜色固定且只有 2-3 种                                | className 字面量（模式 B）           |
| 跑 UnoCSS shortcuts / 主题色                         | className（如 `text-theme-primary`） |
| Antd `<Statistic styles={{ content }}>` / valueStyle | **style 形式**（模式 A）             |

### 已修复案例

- `pages/dashboard/components/utils.ts` + `MarketIndexCard.tsx`：colorByChange 改返回 hex + style 注入（2026-05-16）
- 其他散落 colorByXxx 函数返回 className 字符串的位置 → 后续 PR 统一改 hex 返回

---

## 7. grep 自检命令

```bash
# 散落的 colorByValue 自建函数（应全部下线）
grep -rn "colorByValue\|getColor" src/pages src/components | grep -v stockcolor

# 硬编码红绿（除 stockcolor.ts 本身）
grep -rEn "'#(cf1322|389e0d|52c41a|ff4d4f|f5222d|73d13d)'" src/ \
  --include='*.tsx' --include='*.ts' | grep -v stockcolor.ts

# 反向用色（欧美 #52c41a 绿涨）
grep -rn "#52c41a" src/pages

# UnoCSS 动态类名陷阱：函数返回 'text-#hex' 类名再被模板拼接（高危）
grep -rEn "return 'text-#" src/pages src/components
```

---

## 8. PR 自检清单

- [ ] 涨跌 / PnL / 收益率 → `priceColor(v)`
- [ ] 命中率 / IC / 健康度 → `healthColor(v, goodThreshold, badThreshold)`
- [ ] 最大回撤 / 风险指标 → `riskColor()`
- [ ] 止损价 → `priceColor(stopLoss - entryPrice)`（不要硬编码红！）
- [ ] 无散落自建 colorByValue 函数
- [ ] 无硬编码 `#cf1322` / `#389e0d`，全部走 `@/utils/stockcolor.ts`
- [ ] ECharts itemStyle 用 `STOCK_UP_COLOR` / `STOCK_DOWN_COLOR`
- [ ] **未在工具函数返回 `'text-#hex'` 类名字符串再被模板拼接**（UnoCSS scanner 漏扫陷阱，详见 §6）

---

## 9. 相关规则联动

- `antd6-adapt.md` —— Statistic 用 `styles.content` 注入颜色
- `styles.md` —— UnoCSS 与动态颜色配合
- `.claude/rules/roles-5-perspectives.md` —— 5 视角语义判定
