# 文件规模 + 目录纪律

本规则约束跨项目通用文件纪律。具体语言/框架阈值由项目自己的 `.claude/rules/` 覆盖。

---

## 1. 单文件规模

通用阈值:

| 范围 | 建议 |
|---|---|
| 普通源码文件 | 目标 ≤ 600 行;超过时评估按职责拆分 |
| 测试文件 | 目标 ≤ 700 行;共享 fixture / helper 下沉 |
| 页面 / 组件文件 | 目标 ≤ 700 行;页面、组件、hooks、services 分离 |
| 规则文档 | 目标 ≤ 250 行;只保留可执行约束 |
| 脚本 | 目标 ≤ 500 行;复杂逻辑进模块并补测试 |

项目若已有更严格阈值,以项目本地规则为准。

---

## 2. 重复代码处理

写新 helper 前先查:

- 同模块是否已有同义函数 / 类。
- 同项目是否已有相似 CLI、脚本、service、repository、component。
- 公共路径解析、配置加载、鉴权、日志、错误处理是否已有统一 helper。

发现 80% 相似实现:复用或抽公共 helper,不要复制第二份。若旧实现不适合复用,在改动说明里写清楚差异。

---

## 3. 根目录纪律

禁止在仓根新增临时 `.py`、`.json`、`.txt`、截图或报告散文件。按类型归位:

| 内容 | 位置 |
|---|---|
| 设计 / plan | `docs/plans/` 或项目约定目录 |
| 事故复盘 | `docs/incidents/` 或项目约定目录 |
| 审计报告 | `docs/audits/` |
| 脚本 | `scripts/`、`tools/` 或项目约定目录 |
| demo / 样例 | `demo/` |
| 测试夹具 | `tests/fixtures/` |
| 运行输出 | `output/` 或 gitignored 目录 |

---

## 4. docs 目录归类

项目应在 `docs/README.md` 或 `project-profile.md` 说明文档目录。没有项目规则时采用:

| 内容 | 放哪 |
|---|---|
| 迭代 plan / design / daily | `docs/plans/roadmap-YYYY-MM-DD/` |
| 审计 | `docs/audits/` |
| 事故复盘 | `docs/incidents/` |
| 操作手册 | `docs/operations/` |
| 架构说明 | `docs/architecture/` |
| 用户偏好 / 记忆材料 | `docs/memory/` |

自检:

```powershell
Get-ChildItem docs -File | Where-Object Name -ne 'README.md'
Get-ChildItem docs\plans -File | Where-Object Name -ne 'README.md'
```

命中时按项目规则归位。

---

## 5. 分发规则纪律

- `codev_platform/resources/rules/` 只放跨项目通用规则。
- 项目特有架构、业务字段、表名、枚举、合规口径放项目本地 `.claude/rules/`。
- 改分发真值源后必须做关键字残留扫描,避免把单项目画像分发给所有项目。

---

## 6. 已有 assertion 兜底

⚪ 主要靠 review + 扫描。

建议后续补:

- CI 扫单文件超阈值。
- CI 扫 `docs/` 顶层散文件。
- CI 扫分发规则中的项目专属关键字。
