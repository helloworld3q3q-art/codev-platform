# 组件命名检查

## 描述

检查项目中的文件命名是否符合规范。命名规则定义见 `.claude/rules/component-naming.md`，本 skill 只负责执行检查。

## 使用场景

- 代码审查时检查命名规范
- 新成员入职时检查现有代码
- 提交前检查命名问题

## 执行步骤

### 1. 检查组件文件命名（PascalCase）

```bash
find src/components src/pages -path "*/components/*.tsx" -o -path "*/components/*.ts" | grep -v "index\." | grep -v "utils\." | grep -v "types\." | grep -v "const\." | while read file; do
  filename=$(basename "$file")
  if [[ ! "$filename" =~ ^[A-Z][a-zA-Z0-9]*\.(tsx|ts)$ ]]; then
    echo "❌ 组件文件应使用 PascalCase: $file"
  fi
done
```

### 2. 检查工具文件命名（lowercase）

```bash
find src -type f \( -name "*.ts" -o -name "*.tsx" \) | grep -v "/components/" | while read file; do
  filename=$(basename "$file")
  if [[ "$filename" =~ [A-Z] ]] && [[ ! "$filename" =~ ^index\. ]] && [[ ! "$filename" =~ ^typings\. ]]; then
    echo "❌ 工具文件应使用 lowercase: $file"
  fi
done
```

### 3. 检查抽屉/弹窗目录命名

```bash
find src -type d \( -name "*Drawer*" -o -name "*Modal*" \) | while read dir; do
  dirname=$(basename "$dir")
  # 完全无语义
  if [[ "$dirname" == "Drawer" ]] || [[ "$dirname" == "Modal" ]]; then
    echo "❌ 无意义名称: $dir — 加上功能描述，如 NodeFormDrawer、LogModal"
  fi
  # 纯技术通用名，同页面多个抽屉时会冲突
  if [[ "$dirname" == "FormDrawer" ]] || [[ "$dirname" == "DetailDrawer" ]]; then
    echo "⚠️  过于通用: $dir — 保留区分实体的词，如 NodeFormDrawer、NodeDetailDrawer"
  fi
  # 与所在目录路径完全重复的前缀（冗余）
  module=$(echo "$dir" | sed 's|.*/pages/[^/]*/\([^/]*\)/components/.*|\1|')
  if [[ -n "$module" ]] && echo "$dirname" | grep -qi "^$module"; then
    echo "⚠️  前缀冗余: $dir — '$module' 已由目录提供，去掉重复前缀"
  fi
done
```

### 4. ESLint 检查

```bash
pnpm run lint
```

## 发现问题后的修复步骤

1. 重命名目录/文件
2. 更新文件内的组件名、Props 类型名、export default
3. 更新所有 import 引用
4. 运行 `pnpm run lint` 验证

## 规范参考

`.claude/rules/component-naming.md`
