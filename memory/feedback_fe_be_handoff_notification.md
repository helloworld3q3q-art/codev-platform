---
name: feedback-fe-be-handoff-notification
description: "涉及前后端联调时,AI 必须先通知用户启动 Java + pnpm run api/enums,再做前端,禁止用假数据提前开发"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 07722d89-6bc3-42bf-a872-2afc6d86c0c3
---

后端 DTO / 枚举 / 端点改动后,前端 typings 未含新字段时,**先通知用户跑生成命令,等用户回 OK,再做前端**。

**通知模板**(精简版):

```
⚠️ 前后端联调通知
本次后端改动: <一句话>

请按顺序执行:
1. 重启 Java(端口 18081): mvn -f apps/stock-admin-api/pom.xml spring-boot:run
2. pnpm --dir apps/stock-admin-web run api
3. (如有新枚举) pnpm --dir apps/stock-admin-web run enums

完成后回复"OK",我继续前端。
```

**Why**:
- 用假数据 / 硬编码 map 起前端 → 后续要返工
- typings.d.ts 是 auto-gen,手改下次会被覆盖
- 用户自己跑 pnpm run api 才能拿到真实后端契约

**How to apply**:
- 任一命中即触发通知:新 Java DTO 字段 / 新 Java 枚举 / 新 Controller 端点 / DTO @Schema description 变化
- 不触发:纯前端 UI 调整 / 列重新排序 / 样式微调

**典型案例(2026-05-23 N12 Phase 3)**:
后端 commit be3b5c8 加了 DataFetcherEnum + DataSourceFreshness.fetcherType,我按规则正式发通知,用户跑完 pnpm run api/enums 回 OK 后才接前端。整个 Phase 3 顺滑。

详见规则 `.claude/rules/frontend-backend-handoff.md`(精简到 90 行,原本 380 行被用户砍掉)。

关联:[[feedback-commit-phasing]] / [[feedback-value-source-truth]]
