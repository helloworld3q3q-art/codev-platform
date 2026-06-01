# DB NOT NULL 与 Python 写入护栏三件套

## 事故复盘

2026-05-20 09:10 早盘跑批 NotNullViolation 全 batch 失败 — Flyway 加 `position_sizing_mode SET NOT NULL`,但 `_build_forward_update` 未透传该字段,dict 中 `position_sizing_mode=None`,PG 在 INSERT 阶段(不等 ON CONFLICT)检查 NOT NULL 直接报错。**UPDATE 路径的 COALESCE 救不了 INSERT 时就 NULL 的场景**。

→ 完整复盘 + 3 道防线 + Flyway checksum 禁手动登记见引入本规则的源仓事故归档(规则随分发,具体事故文档不分发)。

## 1. 加 NOT NULL 约束的强制流程

新加 `NOT NULL` 约束（Flyway `ALTER COLUMN SET NOT NULL`）之前，**必须**完成 3 步：

### 步骤 1：grep 全栈写入路径

```bash
# 找所有写该字段的 SQL（含 INSERT / UPSERT / UPDATE）
grep -rn "INSERT INTO <table>\|insert into <table>" --include="*.py" --include="*.java"

# 找所有 dataclass / Entity 字段定义
grep -rn "<field_name>" --include="*.py" python/stock-pipeline/stock_pipeline/models/
grep -rn "<field_name>" --include="*.java" apps/stock-admin-api/src/main/java/.../entity/
```

### 步骤 2：确保 3 道防线全在位

**防线 A — SQL VALUES 子句 COALESCE 兜底**

```sql
-- 不安全：%s 传 None 会撞 NOT NULL
INSERT INTO stock_recommendation_track (..., position_sizing_mode)
VALUES (..., %s)

-- 安全：COALESCE 兜底默认值
INSERT INTO stock_recommendation_track (..., position_sizing_mode)
VALUES (..., COALESCE(%s, 'formula'))
```

**防线 B — Python dataclass 透传字段**

构造行 dict 的函数（如 `_build_forward_update` / `_build_closed`）必须显式传该字段：

```python
return RecommendationTrack(
    ...
    policy_version=track.policy_version,           # ← 必须透传
    position_sizing_mode=track.position_sizing_mode,  # ← 必须透传
)
```

**防线 C — 入库前 sanity assert**

```python
def upsert_recommendation_tracks(self, tracks):
    for t in tracks:
        if t.position_sizing_mode is None:
            raise ValueError(f"position_sizing_mode 不能为 None, stock={t.stock_code}")
```

### 步骤 3：单测覆盖

新加单测验证：
- 字段必须非 None 入库
- SQL COALESCE 兜底分支
- sanity assert 抛异常

## 2. 历史 NULL 数据处理

加 NOT NULL 之前 DB 已有 NULL 行：

```sql
-- 必须先 update 默认值
UPDATE <table> SET <field> = '<default>' WHERE <field> IS NULL;

-- 再 alter
ALTER TABLE <table> ALTER COLUMN <field> SET NOT NULL;
```

**禁止**：直接 `ALTER COLUMN SET NOT NULL` 而不先 UPDATE — Flyway 会失败。

## 3. CHECK 约束同源

加 `CHECK (... IN (..., ..., ...))` 必须：

- 值清单**严格按 Java 枚举源真值** grep 出来（如 `TrackExitReasonEnum.values()`），不要凭空写
- 5-19 兄弟 M 教训：任务清单给的"EVENT_STOP/RISK_CONTROL"实际不是真值，Java 枚举是"MANUAL/STOP_LOSS/TAKE_PROFIT/HOLD_PERIOD"

```bash
# CHECK 值清单的获取方式
grep -A 20 "^public enum <EnumName>" apps/stock-admin-api/src/main/java/.../enums/<EnumName>.java
```

## 4. Flyway checksum 手动登记禁止

**禁止**手动 `INSERT flyway_schema_history` 登记迁移：

```python
# ❌ 不要这么做
cur.execute("insert into flyway_schema_history(...) values (..., crc32(...), ...)")
```

兄弟 M 5-19 晚踩坑：手算 CRC32 与 Flyway 9.x 实际算法不一致，Java 启动 validate 失败。

**正确做法**：
1. 写 Flyway SQL 文件到 `apps/stock-admin-api/src/main/resources/db/migration/V*.sql`
2. 重启 Java 让 Flyway 自动 apply + 自动写 schema_history
3. 紧急时（不能重启 Java）：在 DB 端 alter 后 **置 checksum=NULL**（Flyway 跳过 validate），下次 Java 启动会重算正确 checksum

```sql
-- 紧急救命方案：checksum 置 NULL（不是手算）
UPDATE flyway_schema_history SET checksum = NULL WHERE version = '<v>';
```

## 5. 已遵守 + 已加护栏的字段（清单）

| 表 | 字段 | NOT NULL? | 3 道防线 |
|---|---|---|---|
| stock_recommendation_track | `policy_version` | ✅ | SQL COALESCE 兜底 + Python 透传 + sanity assert（pct-sign-convention）|
| stock_recommendation_track | `position_sizing_mode` | ✅ + CHECK | 同上 |
| stock_recommend_result | `signal` | ❌ 仍可空 | varchar(64) 加宽（5-19 修复）|

新字段加 NOT NULL 时必须扩充此表。

## 6. PR 自检清单

新加 Flyway `SET NOT NULL` 或 `CHECK` 约束时：

- [ ] grep 全栈写入路径核对每条 INSERT / UPDATE 都传该字段？
- [ ] SQL VALUES 加 COALESCE 兜底？
- [ ] Python dataclass 构造函数（如 `_build_*`）透传该字段？
- [ ] 入库前 sanity assert（如 None 抛 ValueError）？
- [ ] 单测覆盖 None / 默认值 / 正常值 3 种场景？
- [ ] 加 NOT NULL 前先 UPDATE 历史 NULL 行为默认值？
- [ ] CHECK 值清单来自 Java 枚举源真值（grep 而非凭空写）？
- [ ] **不要**手动 INSERT flyway_schema_history（让 Java 自动管理）？

## 7. 5 视角支撑

- **量化**：写入护栏防脏数据入库 = 样本统计可信度提升
- **架构**：3 道防线 = 单点防御失效不会全栈崩
- **合规**：sanity assert 抛异常 → 跑批失败 → PIPELINE_FAILURE 告警 → 前端可见
- **投研**：约束清晰 = 字段语义边界明确
- **投资者**：DB 一致性 = UI 展示无 NULL 漏洞

---

## 已有 assertion 兜底

🟡 部分 assertion 化

已有：
- Schema 层：Flyway `ALTER COLUMN SET NOT NULL` + `CHECK` 约束（PG 引擎层硬护栏）
- 测试：`python/stock-pipeline/tests/test_recommendation_tracker.py`（行入库 dataclass 透传）
- 测试：`python/stock-pipeline/tests/test_run_daily_pipeline_policy_version.py`（policy_version NOT NULL 完整传递）
- 测试：`python/stock-pipeline/tests/test_recommendation_track_review_fixes_2026_05_19.py`（5-19 review NOT NULL 补字段）
- Python 入库 assert：`_write_tracks.upsert_recommendation_tracks` 行首 sanity（详见 `pct-sign-convention.md`）
- Flyway 启动校验自动 apply schema_history + checksum 重算

盲区（建议未来补）：
- 缺：约定测试断言"Flyway `SET NOT NULL` 迁移必有配套 `UPDATE <table> SET <field> = '<default>' WHERE <field> IS NULL` 前置"（静态扫迁移文件）
- 缺：约定测试断言"Python `_build_forward_update` / `_build_closed` 等行构造函数透传 schema 所有 NOT NULL 列"（反射 DB schema + AST 扫描）
- 缺：约定测试断言"Flyway CHECK 值清单来自 Java 枚举源真值"（grep 枚举 + 解析 SQL CHECK 比对）
- 缺：禁止"`INSERT flyway_schema_history` 手动插入"的 grep 红线扫描
