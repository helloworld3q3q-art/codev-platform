---
name: web-ui-optimize-imports
description: 检查并优化 web-ui TypeScript/JavaScript 导入语句。
---

# 优化导入语句

## 描述

检查和优化 TypeScript/JavaScript 文件的导入语句。规则定义见 `.codex/rules/code-quality.md` 的"Import 书写规范"节，本 skill 只负责执行检查。

## 使用场景

- 代码审查时优化导入顺序
- 移除未使用的导入
- 修复导入路径（相对路径 → 别名路径）

## 执行步骤

### 1. ESLint 检查未使用的导入

```bash
pnpm run lint
```

按报错逐一删除未使用的 import。

### 2. 检查导入顺序

按 `.codex/rules/code-quality.md` 中定义的四组顺序整理，组间空一行。

### 3. 检查路径规范

```bash
# 找出使用三层及以上相对路径的 import
grep -rn "from '\.\./\.\./\.\." src/
```

逐一改为 `@/` 别名。

### 4. 检查类型导入

```bash
# 找出未使用 import type 的类型导入（通常是接口/类型别名）
grep -rn "^import {" src/ | grep -i "type\|interface\|context"
```

纯类型导入改为 `import type`。

## 相关规范

- `.codex/rules/code-quality.md` — Import 书写规范（顺序、别名、合并、type 分离）
