---
name: feedback-no-wrapper-utils
description: "前端不要为单一第三方库再做一层封装(如 dayjs / lodash 等),业务层直接调用即可"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 07722d89-6bc3-42bf-a872-2afc6d86c0c3
---

业务层时间格式化、字符串处理这类**只是包了一层第三方库 + 加空值判断**的"工具函数",一律不做。直接在业务代码里 `dayjs(v).format('YYYY/MM/DD HH:mm:ss')`,空值判断在调用点 `record.x ? ... : '—'` 显式写。

**Why**:
- 二次封装(如 `formatDateTime(v)`)增加一层抽象但没带来真业务价值
- 团队协作时新人要查 `@/utils/datetime` 才知道实现 = 心智负担
- 直接 `dayjs(...).format(...)` 一眼明了,与 dayjs 文档对齐
- 空值兜底语义按页面定(`-` / `--` / `—`),硬塞工具函数反而失去灵活性

**How to apply**:
- 时间显示 → 直接 `dayjs(v).format('YYYY/MM/DD HH:mm:ss')`(项目统一斜杠 + 含秒)
- 列宽紧 → `format('YYYY/MM/DD HH:mm')`
- 仅日期 → `format('YYYY/MM/DD')`
- 空值在业务层显式判断,不通过工具函数兜底
- 同理:不要为 lodash / 数字格式化等再写一层 wrapper

**2026-05-23 事故**:我建议加 `@/utils/datetime` 含 `formatDateTime`/`formatDateTimeShort`/`formatDate` 三个 wrapper,被用户当场否决并改了规则。`apps/stock-admin-web/.claude/rules/code-quality.md §日期时间格式化规范` 明确禁止自写封装。

**例外**:金融语义函数(如 `priceColor` / `healthColor` / `formatMaxDrawdown` 含业务判断逻辑)继续保留 wrapper —— 这些有真实业务计算,不是单纯调一个库的格式化。判断标准:**wrapper 内是否含业务规则**?有 → 保留;只是"调库 + null 判断" → 禁止。

关联:[[feedback-assertions-over-rules]] / [[feedback-stock-display]]
