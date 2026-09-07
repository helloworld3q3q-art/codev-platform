# 记忆系统规则

## 核心规则

**及时记录开发进度到 `.claude/memory/` 目录，方便新对话快速了解上下文。**

---

## 记录时机

### 必须记录进度

- ✅ 开发进度（不是完成才记录，开发中也要记录）
- ✅ 重要技术决策
- ✅ 用户反馈和偏好

### 记录格式要求

**所有记忆必须包含时间戳**：

```markdown
---
created: 2026-03-21 23:45
updated: 2026-03-21 23:50
---
```

### 记录位置

| 类型     | 文件命名规则                     | 示例                                   |
| -------- | -------------------------------- | -------------------------------------- |
| 开发进度 | `progress_<模块>_<作者>.md`      | `progress_user-management_zhangsan.md` |
| 开发进度 | `progress_<模块>.md`（通用进度） | `progress_theme-system.md`             |
| 用户偏好 | `user_<姓名>.md`                 | `user_zhangsan.md`                     |
| 项目决策 | `project_<主题>.md`              | `project_api-design.md`                |
| 反馈纠正 | `feedback_<主题>.md`             | `feedback_code-style.md`               |

**开发进度命名规则**：

- 按模块：`progress_<模块名>.md`（如 `progress_user-management.md`）
- 按作者：`progress_<模块>_<作者>.md`（如 `progress_theme-system_lisi.md`）
- 简化命名：使用小写字母和连字符，如 `progress_login.md`、`progress_table-component.md`

---

## 新对话加载

新对话时，按以下顺序读取：

1. **MEMORY.md** - 记忆索引（快速了解状态）
2. **progress\_\*.md** - 所有开发进度文件（匹配 `progress_*.md` 模式）
3. 根据任务需要读取其他记忆文件

---

## 维护原则

- 单一数据源：同类信息只记录在一处
- 及时更新：完成工作后立即更新进度
- 清晰命名：文件名明确表达内容
- 保持简洁：只记录关键信息
