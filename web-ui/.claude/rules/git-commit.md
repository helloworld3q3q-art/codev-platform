# Git 提交规范

项目使用 `@commitlint/config-conventional`，Husky 在提交前自动校验格式。**type、scope、subject 三者必须填写。**

## 格式

```
<type>(<scope>): <subject>
```

- **scope**：**必填**，表示影响范围，如 `login`、`config`、`prompt`、`rules`
- **subject**：**必填**，简短描述，使用中文

## 类型（type）

| type       | 用途                           |
| ---------- | ------------------------------ |
| `feat`     | 新功能                         |
| `fix`      | 修复 Bug                       |
| `refactor` | 重构（不新增功能，不修复 Bug） |
| `perf`     | 性能优化                       |
| `style`    | 代码格式（不影响运行）         |
| `docs`     | 文档变更                       |
| `test`     | 增加测试                       |
| `chore`    | 构建或辅助工具变动             |
| `revert`   | 回退提交                       |
| `build`    | 打包相关                       |

## 示例

```bash
git commit -m "feat(login): 添加用户登录功能"
git commit -m "fix(prompt): 修复提示词列表分页问题"
git commit -m "refactor(config): 优化业务节点 useEffect 模式"
git commit -m "perf(claude): 补充枚举使用规范"
```
