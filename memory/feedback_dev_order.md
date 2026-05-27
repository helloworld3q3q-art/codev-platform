---
name: 开发顺序规范
description: 该项目的全栈开发顺序：Python → Java → Web，前端最后整体开发
type: feedback
originSessionId: bc8c355b-36f5-495e-a895-8820ef8577bd
---
开发顺序必须严格遵循：Python → Java → Web（前端最后整体开发）。

**Why:** 用户明确要求，前两层写完后前端才开始，不要交替开发。

**How to apply:** 
- 任何新功能，先把 Python 侧（数据管道、模型、规则）写完
- 再写 Java 侧（entity、mapper、facade、controller、DTO）
- 最后写 Web 前端（所有 Java API 都准备好后，前端整体开发）
- 不要在 Python 或 Java 未完成时提前跳到前端实现
