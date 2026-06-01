# 前后端联调:简短工作流

> 涉及后端 DTO / 枚举 / 端点改动,前端 typings 未含新字段时,AI **必须先通知用户**,等用户跑完 `pnpm run api` + `pnpm run enums` 回复确认后,再继续前端实现。**禁止用假数据 / 硬编码 map 提前接前端**。

---

## 触发条件

任一命中即触发:

- 新增 / 修改 Java DTO 字段
- 新增 / 修改 Java 枚举
- 新增 Java Controller 端点
- 后端字段语义变化(`@Schema description` 变了)

**不触发**:纯前端 UI 调整 / 已有接口的样式微调 / 列排序。

---

## AI 标准通知模板

```
⚠️ 前后端联调通知

本次后端改动: <一句话总结>

请按顺序执行:
1. 重启 Java(端口 18081):
   mvn -f apps/stock-admin-api/pom.xml spring-boot:run
2. pnpm --dir apps/stock-admin-web run api      # 重生成 typings.d.ts
3. pnpm --dir apps/stock-admin-web run enums    # 重生成 enumslocal.tsx

完成后回复"OK",我继续前端。
```

用户回复 OK 后才接前端;不愿等就把前端拆独立 TODO,先合 backend commit。

---

## ❌ 反例 vs ✅ 正例

| ❌ 反例 | ✅ 正例 |
|---|---|
| 前端硬编码 `Record<string, string>` 模拟后端枚举 | 通知用户跑 `pnpm run enums`,前端走 `useModel('enum').getFormattedEnums('XxxEnum')` |
| 强转 `record.newField as string` 访问未生成的字段 | 等 `pnpm run api` 重生成 `typings.d.ts` 后用真字段 |
| 在 N12 第一版用 FETCHER_GROUP_MAP 假数据起前端 | Phase 1+2 后端 commit 单独 push,Phase 3 等用户跑生成命令后再做 |

---

## PR 自检

- [ ] 改了 Java DTO 字段 / 枚举 / 端点?
- [ ] 通知用户跑 `pnpm run api` / `pnpm run enums` 了?
- [ ] 没用硬编码 map / 假数据替代后端枚举?
- [ ] 后端 commit 与前端 commit 拆开?

---

## grep 自检

```bash
# 前端 .tsx 业务文件硬编码业务枚举 map(高危)
grep -rEn "Record<string, string>" apps/stock-admin-web/src/pages

# 前端 .tsx 强转 API 类型(可能用未生成字段)
grep -rEn "as API\." apps/stock-admin-web/src/pages
```

---

## 关联规则

- `cross-layer-enum-consistency.md`:跨层枚举值字面量一致
- `api-contracts.md §3`:枚举来源(Java 真值源)
- `apps/stock-admin-web/.claude/rules/api-service.md`:`pnpm run api` 生成规范
- skill `backend-dto-change` / `add-enum` / `add-frontend-page`

---

## 已有 assertion 兜底

- `DtoDocumentationConsistencyTest` (Java DTO @Schema 完整性)
- `EnumMetadataServiceTest` (新枚举反射注册)
- `check_entity_dataclass_parity.py` (Python ↔ Java ↔ Flyway 字段一致性)
- `audit_fetcher_registry_parity.py` (Python ↔ Java enum 跨层值一致性)
- pre-push hook 4 项 gate

盲区:
- 缺自动检测"前端业务代码硬编码 Record<string,string> 业务 map"的 CI 钩子
- 缺 git pre-commit 提示"DTO/枚举变更需通知前端"
