# 改动后验证清单

验证要和改动半径匹配。项目本地规则可覆盖具体命令;本文件只给通用口径。

---

## 1. 按改动层级验证

| 改动层 | 最小验证 |
|---|---|
| 单文件逻辑 | 对应单测 / lint / typecheck |
| 前端 | 项目前端 lint/typecheck/组件测试 |
| 后端 | 目标 handler/service/repository 单测或接口测试 |
| 数据 / schema | migration/schema 检查 + 读写路径测试 |
| 消息 / 事件 / 任务 | 生产者和消费者两侧定向测试 |
| 认证 / 权限 | 正向授权 + 越权拒绝测试 |
| 跨层契约 | 生产者 + 消费者 + 生成链路(如有) |
| AI 工具 / MCP | status/dry-run + 目标测试 |
| 文档 / 规则 | 引用路径存在 + 断链扫描 |
| Hook / 脚本 | dry-run + 幂等测试 |

---

## 2. 测试基线

不要在通用规则里写死某项目的历史用例数。判断口径:

- 定向测试必须通过。
- 全量测试若失败,必须区分本次相关失败和既有失败。
- 新增 CLI / sync / 生成 / 安全边界行为必须有单测。
- 修复 bug 时优先补能失败的回归测试。

项目可在本地规则里写自己的全量命令和最低用例数。

---

## 3. 改动前检查

- `git status -s`:确认 dirty 范围,不覆盖用户改动。
- 判断 L1/L2/L3/L4,按 `workflow.md` 选 MCP / 本地读取路径。
- 命中生成物、迁移、依赖、认证、生产配置、MCP 配置时,先说明影响面。
- 跨层改动先确认消费者和验证闭环。

---

## 4. 快速诊断索引

| 现象 | 排查路径 |
|---|---|
| 命令缺子命令 / 参数 | CLI parser 测试 + 入口注册表 |
| 分发资源找不到 | package-data / importlib.resources 测试 |
| hook 重复写配置 | settings merge 幂等测试 |
| MCP 全红 | 服务 status、端口、进程、客户端配置 |
| 文档召回差 | 索引状态、collection、项目 id、最近重建时间 |
| codegraph 结果旧 | dirty 范围、索引时间、项目路径 |
| graph 查询缺节点 | project_id、graph store、扫描插件、节点证据 |
| 前后端契约不一致 | schema / 生成链路 / 消费者调用点 |

---

## 5. 已有 assertion 兜底

🟡 部分 assertion 化:

- resources importlib 定位测试。
- CLI 子命令注册测试。
- hook/settings merge 幂等测试。
- 项目可自行补安全、契约、生成链路测试。

盲区:

- 通用规则无法知道各项目全量验证命令。
- MCP-first 纪律除 Grep hook 外仍依赖执行者自报和 review。
- 跨层链路覆盖度依赖各项目 graph/codegraph 索引质量。
