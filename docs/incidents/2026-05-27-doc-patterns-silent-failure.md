# 2026-05-27 DOC_PATTERNS silent failure — 新 docs 子目录文件未被索引

> 工具栈事故复盘 — chroma `index_docs.py:DOC_PATTERNS` 是子目录白名单,新建 `docs/dev-evolution/` 后忘了加 pattern,导致目录下 4 文件 / 22 chunks 全部未入 chroma 索引,search_docs 按 `category=dev_log/tooling_incident` 召回为空。

## 现象

- 5-27 当天新建 `docs/dev-evolution/log/` + `docs/dev-evolution/incidents/` 4 个文件(2 README + 2026-05.md + N12 复盘)
- 4 次 commit 各自触发 post-commit chroma reindex,reindex.log 显示"已处理 4 文件 / 87 chunks"
- 但 `mcp__platform-docs__list_collections` 的 `by_category` **完全没有** `dev_log` / `tooling_incident`
- `mcp__platform-docs__get_by_file` 查 dev-evolution 任一文件 → "未找到任何 chunk"
- `search_docs(query, category="dev_log")` → 永远返回 `[]`
- **没报错没崩**,只是结果为空 → silent failure

## 根因

`tools/chroma/index_docs.py` 的 `DOC_PATTERNS` 是**子目录白名单**,不是通配:

```python
DOC_PATTERNS = [
    ...
    "docs/architecture/**/*.md",   # 老目录
    "docs/operations/**/*.md",     # 老目录
    "docs/memory/**/*.md",         # 老目录
    ...                            # 没有 "docs/**/*.md" 通配
]
```

`docs/dev-evolution/` 是 5-27 新建的第一个**新顶级 docs 子目录**,白名单没加。`discover_files()` glob 不到 → 文件不存在于索引 input → 永远 0 chunks。

附带发现:`docs/legal/` / `docs/migration/` / `docs/rules/` 也是历史遗漏,3 个目录共 3 文件长期未被索引。

## 修复

### 1. 补 DOC_PATTERNS(commit 2e245f5 + 本 commit)

```python
"docs/dev-evolution/**/*.md",   # 5-27 新建
"docs/legal/**/*.md",           # 历史遗漏,本次顺手补
"docs/migration/**/*.md",       # 历史遗漏
"docs/rules/**/*.md",           # 历史遗漏
```

### 2. 全量重建 chroma

`index_docs.py --force` 清空 collection 后回填 241 文件 / 4296 chunks。

### 3. 加 pre-push audit 6/6 防再犯

- 新增 `tools/chroma/audit_doc_patterns_coverage.py`
- 加进 `tools/dev/pre-push-audit.ps1` 作为 audit 6/6
- 扫 docs/ 所有顶级子目录,任一不被 DOC_PATTERNS 覆盖即 FAIL,push 拒绝
- silent failure → loud failure

### 4. 加 memory feedback 防遗忘

新增 `docs/memory/feedback_doc_patterns_whitelist.md`:加 docs/ 新顶级子目录时必须同步改 DOC_PATTERNS。autoload,**写新目录时必触发自检**。

## 预防

| 层 | 机制 |
|---|---|
| 流程 | memory `doc-patterns-whitelist` autoload(写新目录时反射查) |
| 机器兜底 | pre-push audit 6/6 (silent failure → loud failure) |
| 例外 | future 自动生成 / cache 目录加进 `audit_doc_patterns_coverage.py:_EXEMPT_SUBDIRS` |

## 关联

- 规则:`.claude/rules/ai-tools-mcp.md`(MCP 工具配置)
- 工具:`tools/chroma/index_docs.py`(本次修改源头)
- audit:`tools/chroma/audit_doc_patterns_coverage.py`(新增)
- memory:[[doc-patterns-whitelist]] / [[no-cargo-cult-dates]](同期事故)
