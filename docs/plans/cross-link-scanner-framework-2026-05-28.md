# Plan: cross_link scanner 框架化(P3-k + P3-l)

> 状态:📋 草案,未启动
> 性质:**等真有第二个业务项目接入 cross-link 时再做**
> 当前:platform 仓 `tools/cross_link/scan_*.py` + `build_index.py` 业务路径硬编码,但算法通用

---

## 一、目标

把 scanner 框架抽到 codev-platform,让任何业务项目用一致的方式扩展 cross-link KG。

业务项目只需:
1. 写一个 `<biz_scanner>.py` 注册到 codev-platform runner
2. 配 `.claude/index.json` 或 CLI 参数告诉 runner 扫哪些路径
3. 跑 `python -m codev_platform.cross_link.runner` 即可

不再需要在 platform 仓 hardcode `apps/stock-admin-api/...` / `python/stock-pipeline/...` 这类路径。

---

## 二、当前痛点

scanner 文件分析(brother A 审计):

| scanner | 行数 | 业务硬编码 | 算法通用部分 |
|---|---|---|---|
| `scan_flyway.py` | 252 | path: `apps/stock-admin-api/.../db/migration` + `_SKIP_TABLE_NAMES={sys_menu, ...}` | sqlglot 解析 + Flyway 命名 |
| `scan_java_mappers.py` | 306 | path: `apps/stock-admin-api/...` | MyBatis XML + Java @Select sqlglot 解 |
| `scan_java_controllers.py` | 218 | path: `apps/stock-admin-api/...` | javalang @RequestMapping/@PostMapping 解 |
| `scan_java_call_graph.py` | 304 | path + `com/openclaw/` 包前缀 | javalang call chain 解 |
| `scan_frontend_apis.py` | 151 | path: `services/apis/*.ts` | ts AST + axios 调用解 |
| `scan_frontend_page_calls.py` | 196 | path: `apps/stock-admin-web/...` | 跨文件 import 解 |
| `scan_python_repos.py` | 292 | path: `python/stock-pipeline/...` | python AST + sqlglot 解 SQL |

**80% 通用** + **20% 业务硬编码**(路径前缀 + 命名习惯)。

---

## 三、设计

### 3.1 codev_platform/cross_link/scanners/

```
codev_platform/cross_link/
├── runner.py                       # 主 orchestrator (现 build_index 主流程)
├── scanners/
│   ├── __init__.py                 # 注册表
│   ├── flyway.py                   # 通用 SQL migration 扫描 (sqlglot)
│   ├── java_mappers.py             # 通用 MyBatis + @Select 扫描 (javalang+sqlglot)
│   ├── java_controllers.py         # 通用 @RequestMapping 扫描 (javalang)
│   ├── java_call_graph.py          # 通用 java method call chain
│   ├── frontend_apis.py            # 通用 ts AST + axios 扫描 (ts-morph)
│   ├── frontend_page_calls.py      # 通用 import chain
│   └── python_repos.py             # 通用 python AST + sqlglot 扫描
└── config.py                       # ScannerConfig (path, exclude, name patterns)
```

### 3.2 业务项目接入

`<biz-repo>/.claude/index.json` 加 cross_link section:

```json
{
  "doc_patterns": [...],
  "cross_link": {
    "scanners": [
      {
        "name": "flyway",
        "path": "apps/stock-admin-api/src/main/resources/db/migration",
        "exclude_table_names": ["sys_menu", "sys_user", ...]
      },
      {
        "name": "java_mappers",
        "path": "apps/stock-admin-api/src/main/java",
        "package_prefix": "com/openclaw/"
      },
      {
        "name": "python_repos",
        "path": "python/stock-pipeline"
      }
    ]
  }
}
```

### 3.3 runner 入口

```python
# codev_platform.cross_link.runner
def main():
    config = load_cross_link_config()  # from .claude/index.json
    with acquire_lock(...):
        for scanner_cfg in config.scanners:
            scanner = SCANNER_REGISTRY[scanner_cfg.name](**scanner_cfg.kwargs)
            scanner.scan(conn)
        link_api(conn)  # 已在 codev_platform.cross_link.linker
        ...
```

---

## 四、Phase 拆分

| Phase | 内容 | 时间 |
|---|---|---|
| **K1** | runner 框架 + ScannerConfig + 注册表(0 个 scanner 实现) | 2h |
| **K2** | flyway / python_repos 两个 scanner 参数化迁(选项 path / exclude_table_names) | 3h |
| **K3** | java_mappers / java_controllers / java_call_graph 三个迁(选项 path / package_prefix) | 4h |
| **K4** | frontend_apis / frontend_page_calls 两个迁(选项 path / exclude_patterns) | 2h |
| **K5** | platform 仓 build_index.py 改 thin wrapper(调 codev-platform runner) + 移除 7 scanner | 1h |
| **K6** | 测试 + 验证 post-commit hook 仍 work | 1h |

总:**~13h**(全职)/ **~3-4 天**(副业)。

---

## 五、启动条件

**等满足才动**:
1. 真的有第二个业务项目要接 cross-link(否则 YAGNI)
2. 当前业务侧 7 个 scanner 稳定运行 ≥ 1 个月,无 bug
3. 用户拍板"开干"

---

## 六、风险

| 风险 | 严重度 | 缓解 |
|---|---|---|
| post-commit hook 触发 build_index 失败 | 🔴 高 | K6 必须先在 dry-run 模式验证 + 平台业务持续测试 1 周 |
| sqlglot / javalang 跨项目 SQL 方言差异 | 🟡 中 | scanner 参数加 `sql_dialect` / `java_version` 选项 |
| ts-morph 跨项目 tsconfig 不一致 | 🟡 中 | scanner 接受 `tsconfig_path` 参数 |
| 工作量爆炸 | 🔴 高 | K1-K6 按 phase 验证,任一 phase 跑通才进下一个 |

---

## 七、关联

- `codev_platform.cross_link.{schema, server, query, linker}` — 已就绪
- `codev_platform.core.spawn_lock` — 已抽,runner 直接复用
- platform 仓 `tools/cross_link/scan_*.py` + `build_index.py` — 当前真值源
- `docs/plans/team-deploy-2026-05-27-design.md` — 多项目 namespace 设计(本 plan 是其子任务)
