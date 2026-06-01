# 跨层业务枚举一致性硬约束(N12 教训沉淀)

> **规则定位**:`api-contracts.md §3 枚举来源` 的细化版,专门讲"业务标识符跨语言(Python ↔ Java ↔ DTO ↔ 前端)一致性"。
> 关联规则:`model-field-consistency.md`(跨层字段一致性)/ `api-contracts.md`(枚举来源) / `add-enum` skill(6 步流程)。

---

## 1. 事故复盘

N12(2026-05-23)— L1 agent 在前端 `apps/stock-admin-web/src/pages/dataintegrity/utils.ts` 落地硬编码 `FETCHER_GROUP_MAP`,把"DB 表名 → fetcher 分组"业务标识符当成 UI 常量发明真值。用户当场否决:"py / sql / java / 前端 字段值最好都能从源头保持一致"。延期 1.5-2 天重做四层联动。

→ 完整复盘 + 修复 Phase 1-4 + parity 工具见引入本规则的源仓事故归档(规则随分发,具体事故文档不分发)。

---

## 2. 判定标准:什么算"业务枚举值"

下表列出判定维度,**任一条命中即必须走单一真值源链路**:

| 维度 | 判定问句 | 真枚举案例 | 非枚举案例(允许前端 map) |
|---|---|---|---|
| 是否表示**业务概念** | "这个字符串代表业务语义还是纯展示标签?" | fetcher 类型 / 推荐信号 BUY/HOLD / 风险等级 | 颜色 #cf1322 / 图标名 |
| 是否**跨语言传递** | "Python 写 / Java 读 / 前端展示三层会共享这个值吗?" | shadow_mode / policy_version / exit_reason | UI 控件 size 'small'/'large' |
| 是否**前端硬编码会漂移** | "Python 改名后前端 map 会不会跟着改?" | 表名分组 / fetcher 类型 | 当前页面专用的 tab 顺序 |
| 是否**仅 UI 显示派生** | "这只是给已有枚举值做中文 label 映射吗?" | (非枚举本身) | `EXIT_REASON_LABEL: STOP_LOSS → '止损'` 是 i18n |
| 是否**值本身有限可枚举** | "可能的取值是有限离散集合吗?" | 9 种 AlertType / 5 种 RiskLevel | 自由文本字段(stock_name / reason) |

**经验法则**:**只要业务后端(Python / Java)需要识别这个标识,就不是 UI 派生数据,必须有真值源**。

**2026-05-25 强化判定**:**前端筛选 / 下拉的 `value` 只要会进入 API request,且取值是有限离散集合,一律按业务枚举处理**。不能因为它写在 `Columns.tsx` / `utils.ts`、看起来只是 ProTable `fieldProps.options`,就把它当纯 UI 常量。

典型命中:
- ProTable 搜索项 `valueType: 'select'` 的 `fieldProps.options`
- 表单 `<Select options={...}>` 提交给后端 DTO
- Radio / Segmented / Checkbox.Group 的值进入 request body / query params
- 前端本地 `convertParams()` 把某个字符串值透传给后端

正确流程:
`Java XxxEnum implements BaseEnum` → `EnumMetadataService.register` → DTO `@Schema(description="...(XxxEnum)")` → 用户重启 Java → `pnpm run enums` → React 组件 `useModel('enum').getEnumOptions('XxxEnum')` → 通过 `context` 传给 `Columns.tsx` / 子组件。

反例:

```ts
// ❌ 会进入 API request 的业务筛选值,不能在前端本地造真值源
export const LIQUIDITY_BUCKET_OPTIONS = [
  { label: '< 5000 万', value: 'LOW_LT_50M' },
  { label: '5000 万-1 亿', value: 'WATCH_50M_100M' },
];
```

正例:

```tsx
// ✅ index.tsx
const liquidityBucketOptions = useMemo(
  () => getEnumOptions('LiquidityBucketEnum'),
  [getEnumOptions],
);

// ✅ Columns.tsx 普通函数只从 context 接收,不调用 hook,不定义业务 options
fieldProps: { options: liquidityBucketOptions }
```

---

## 3. 强制原则:单一真值源四层传播链

任何业务枚举的传播路径**必须**走以下四层,缺一不可:

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1: Python 真值源(registry / enum / 白名单)                    │
│  位置:python/stock-pipeline/stock_pipeline/{fetchers,models}/      │
│  形式:`class FetcherType(str, Enum)` 或 `_ALLOWED = frozenset(...)`  │
└─────────────────────────────────────────────────────────────────────┘
                              ↓ 跨语言对齐(值字面量一致)
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 2: Java 枚举(BaseEnum + EnumMetadataService 注册)            │
│  位置:apps/stock-admin-api/src/main/java/.../enums/                │
│  形式:`enum XxxEnum implements BaseEnum { ... }` + `register(map)`  │
└─────────────────────────────────────────────────────────────────────┘
                              ↓ DTO 字段引用(@Schema 必填)
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 3: DTO Response 字段(@Schema description + example)         │
│  位置:apps/stock-admin-api/src/main/java/.../dto/                  │
│  形式:`@Schema(description="数据源类型", example="QUOTE")`           │
│        `private String fetcherType;`                                │
└─────────────────────────────────────────────────────────────────────┘
                              ↓ pnpm run api + pnpm run enums
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 4: 前端 useModel('enum')(禁止硬编码)                         │
│  位置:apps/stock-admin-web/src/pages/{module}/                     │
│  形式:`const { getFormattedEnums } = useModel('enum');`             │
│        `const fetcherMap = getFormattedEnums('DataFetcherEnum');`    │
└─────────────────────────────────────────────────────────────────────┘
```

**铁律**:
1. **值字面量必须四层完全一致**(大小写 + 下划线 + 拼写)
2. **Python enum 的 `.name` ≡ Java enum 的 `name()` ≡ DTO 字段值 ≡ 前端 enum key**
3. 修改任一层 → 必须同步改其他三层 + 跑 parity 工具验证
4. Python 写库前用白名单校验(参考 `planning/four_w.py:_coerce_action`),防止脏值入 DB

---

## 4. 反例 / 正例对照

### 反例 1:前端硬编码业务标识 map(本次 N12)

```ts
// ❌ apps/stock-admin-web/src/pages/dataintegrity/utils.ts
const FETCHER_GROUP_MAP: Record<string, string> = {
  stock_quote_daily: 'quote',
  stock_fund_flow_daily: 'fund_flow',
};

const labelOf = (tableName: string) => FETCHER_GROUP_MAP[tableName] ?? 'unknown';
```

**问题**:Python 改 fetcher 名 / DB 新增表 → 前端 map 不感知 → UI 漂移。

### 反例 2:Python 端散落字符串(无 enum / registry)

```python
# ❌ python/stock-pipeline/stock_pipeline/fetchers/fetch_lhb.py
def write_lhb_event(items):
    for item in items:
        repo.upsert_lhb({
            "source": "lhb",  # ← 散落字符串,Java/前端不知道
            ...
        })

# ❌ 另一个 fetcher 又写
"source": "LHB"  # ← 大小写漂移,白名单校验都没有
```

**问题**:无单一真值源 → 跨 fetcher 拼写漂移 → Java 端无法可靠按 source 分组。

### 反例 3:后端 DTO 用未注册的字符串字段

```java
// ❌ DataHealthResponse.java
@Schema(description = "数据源")  // ← description 太笼统,无 example,前端不知道有哪些值
private String source;  // ← 字段类型是 String 但实际是枚举值,EnumMetadataService 找不到
```

**问题**:前端拿到字段没法走 `useModel('enum')` → 只能硬编码 → 回到反例 1。

### 反例 4:前端请求筛选 options 本地硬编码

```ts
// ❌ apps/stock-admin-web/src/pages/stockpools/components/utils.ts
export const LIQUIDITY_BUCKET_OPTIONS = [
  { label: '< 5000 万', value: 'LOW_LT_50M' },
  { label: '缺失', value: 'MISSING' },
];
```

**问题**:`liquidityBucket` 会进入 `StockPoolItemPageRequest`,后端需要识别这些值,因此它不是 UI 常量。必须上移为 `LiquidityBucketEnum` 并注册到 `EnumMetadataService`,前端通过 `useModel('enum')` 获取 options。

### 正例:DataFetcherEnum 完整四层链路

**Layer 1 — Python 真值源**:

```python
# python/stock-pipeline/stock_pipeline/fetchers/registry.py
class FetcherType(str, Enum):
    QUOTE = "QUOTE"
    FUND_FLOW = "FUND_FLOW"
    LHB = "LHB"
    # ...

FETCHER_TO_TABLES: dict[FetcherType, tuple[str, ...]] = {
    FetcherType.QUOTE: ("stock_quote_daily", "stock_valuation_daily"),
    FetcherType.FUND_FLOW: ("stock_fund_flow_daily",),
    # ...
}
```

**Layer 2 — Java 枚举**:

```java
// apps/stock-admin-api/src/main/java/.../enums/DataFetcherEnum.java
public enum DataFetcherEnum implements BaseEnum {
    QUOTE("行情"),
    FUND_FLOW("资金流"),
    LHB("龙虎榜");
    // ... 与 Python FetcherType 完全对齐

    private final String displayName;
    DataFetcherEnum(String displayName) { this.displayName = displayName; }
    public String getEnumType() { return "DataFetcherEnum"; }
    public String getEnumValue() { return name(); }
    public String getLocalLanguage() { return displayName; }
    public Integer getEnumOrder() { return ordinal() + 1; }
}

// EnumMetadataService 构造函数追加:
register(map, DataFetcherEnum.class);
```

**Layer 3 — DTO 字段**:

```java
@Schema(description = "数据源类型(DataFetcherEnum)", example = "QUOTE")
private String fetcherType;
```

**Layer 4 — 前端使用**:

```tsx
// apps/stock-admin-web/src/pages/dataintegrity/index.tsx
const { getFormattedEnums } = useModel('enum');
const fetcherMap = getFormattedEnums('DataFetcherEnum');
// → { QUOTE: '行情', FUND_FLOW: '资金流', LHB: '龙虎榜' }

return <Tag>{fetcherMap[record.fetcherType] ?? record.fetcherType}</Tag>;
```

---

## 5. 跨层对账测试模式

参考 `python/stock-pipeline/tools/check_entity_dataclass_parity.py` 现有模式,**每个新增跨层枚举**都建议配套写一个 parity 检查工具:

```python
# python/stock-pipeline/tools/audit_fetcher_registry_parity.py(范本)
"""断言 Python FetcherType.name 集合 ≡ Java DataFetcherEnum.values() 集合。

退出码:0 全通过 / 1 有 HIGH 违规(值不一致)
"""

def main() -> int:
    py_values = {t.name for t in FetcherType}
    java_values = parse_java_enum_values(
        Path("apps/stock-admin-api/src/main/java/.../enums/DataFetcherEnum.java")
    )

    only_py = py_values - java_values
    only_java = java_values - py_values

    if only_py or only_java:
        print(f"[HIGH] Python only: {only_py}, Java only: {only_java}")
        return 1
    return 0
```

并加单测 `tests/test_fetcher_registry_parity.py` 覆盖。

**推荐执行时机**:
- pre-push hook(参考 `verification-checklist.md §2.1` 的 3 项 gate 模式)
- 每周 EOD 跑批前手动跑一次

---

## 6. grep 自检命令

PR 提交前必跑下列 4 条命令,任一命中需评估是否违反本规则:

```bash
# 1. 前端业务 .tsx / utils.ts 硬编码业务字符串 map(本次 N12 反例的核心模式)
#    `Record<string, string>` 类型 + map 字面量同时含已知业务表名/标识 → 高度可疑
grep -rnE "Record<string, ?(string|number)>" apps/stock-admin-web/src/pages \
  | grep -vE "color|BADGE_|i18n|locale|theme"

# 1b. 前端请求筛选 / 下拉 options 本地硬编码(高危)
#     命中后检查 value 是否进入 convertParams / API request；进入则必须改后端枚举 + useModel('enum')
grep -rnE "(OPTIONS|options)\\s*=\\s*\\[|valueType:\\s*'select'|<Select|Radio\\.Group|Checkbox\\.Group|Segmented" \
  apps/stock-admin-web/src/pages apps/stock-admin-web/src/components \
  | grep -vE "color|BADGE_|i18n|locale|theme|pure UI"

# 2. Java DTO 含字符串字段但未在 @Schema description 引用枚举名
#    示例:`private String xxxType;` 但 Schema 没写 "(XxxEnum)" 注释
grep -rnE "private String \w+(Type|Status|Signal|Reason|Level|Mode);" \
  apps/stock-admin-api/src/main/java/com/openclaw/stock/admin/dto/ \
  | xargs -I {} sh -c 'echo "=== {} ==="; head -3 "{}"'

# 3. Python fetchers / models / recommenders 散落业务标识字符串
#    应在 registry.py / core.py 集中定义,而非 fetch_*.py 散落
grep -rnE "(source|fetcher|signal|action|reason) ?= ?[\"'][A-Z_]+[\"']" \
  python/stock-pipeline/stock_pipeline/fetchers/ \
  python/stock-pipeline/stock_pipeline/recommenders/ \
  | grep -v registry.py | grep -v models/core.py

# 4. 跨层值一致性快速核对(单一枚举名)
#    例:验证 FetcherType 在 Python / Java / DTO 三处的值字面量完全一致
ENUM_NAME="DataFetcherEnum"
echo "=== Python ==="; grep -rn "FetcherType\." python/stock-pipeline/stock_pipeline/fetchers/registry.py
echo "=== Java enum ==="; grep -A 20 "enum $ENUM_NAME" apps/stock-admin-api/src/main/java/
echo "=== DTO usage ==="; grep -rn "$ENUM_NAME" apps/stock-admin-api/src/main/java/com/openclaw/stock/admin/dto/
echo "=== Frontend ==="; grep -rn "$ENUM_NAME" apps/stock-admin-web/src/
```

---

## 7. PR 自检清单

新增 / 修改跨层枚举或业务标识字段时,逐条勾选:

- [ ] 新建枚举值 → 是否同时落地 Python registry + Java enum + DTO Schema + 前端 useModel 四层?
- [ ] 修改枚举值字面量(改名 / 大小写)→ 是否四层同步改 + grep 全栈引用?
- [ ] DTO 字符串字段(`xxxType` / `xxxStatus`)是否对应已注册的 Java 枚举?`@Schema` 是否标注枚举名?
- [ ] 前端新增筛选 / 下拉 / Radio / Segmented 的 value 是否进入 API request?若是,是否走 Java enum + `useModel('enum')`?
- [ ] 前端业务代码(.tsx / utils.ts)是否**零**硬编码业务标识 map?所有 label 走 `useModel('enum').getFormattedEnums(...)`?
- [ ] 是否补充了 parity 测试工具(参考 `check_entity_dataclass_parity.py`)?
- [ ] 是否跑过 `pnpm run api` + `pnpm run enums` 重新生成前端类型?
- [ ] Python 写库入口是否有白名单校验(参考 `_coerce_action` 模式)?

---

## 8. 何时不算枚举(允许前端 map 的例外)

并非所有 `Record<string, string>` 都违规。下列场景属**纯 UI 派生**,允许前端 map:

| 场景 | 例子 | 为什么允许 |
|---|---|---|
| **i18n 中文 label 映射** | `EXIT_REASON_LABEL: { STOP_LOSS: '止损', TAKE_PROFIT: '止盈' }` | 枚举值本身走真值源,这只是中文显示 |
| **纯 UI 颜色 / 图标映射** | `BADGE_STATUS: { Yes: 'success', No: 'default' }` | `'success'` 是 antd Badge prop,不是业务概念 |
| **当前页面专用配置** | tab 顺序 / 表格列顺序 / 默认排序方向 | 无跨层共享需求,改这个不影响后端 |
| **第三方库的固定枚举** | `Dayjs 的 weekday: ['日','一','二',...]` | 第三方契约,非项目业务概念 |

**经验法则**:
- map 的 **key** 来自真值源(已有 enum 值)→ 是 i18n,放行
- map 的 **key** 是自己发明的业务标识(如表名 / fetcher 名)→ 违规,必须上溯到真值源

详细 i18n vs 枚举的区分见 `apps/stock-admin-web/.claude/rules/architecture.md §4 UI 颜色映射` 节。

---

## 9. 关联规则

| 规则 | 关联点 |
|---|---|
| `api-contracts.md §3` | 本规则是其细化版,聚焦"跨语言一致性" |
| `model-field-consistency.md` | 字段名一致性(本规则关注**值**一致性) |
| `apps/stock-admin-api/.claude/rules/enum-patterns.md` | Java 端三步走(BaseEnum + EnumMetadataService + pnpm run enums) |
| `apps/stock-admin-web/.claude/rules/architecture.md §4` | 前端 useModel('enum') 使用规范 |
| `python/stock-pipeline/.claude/rules/pipeline-patterns.md` §任务总线 | Python 端 rule_code / job_type 同步 Java 枚举 |
| `/add-enum` skill | 标准 6 步流程(新增枚举走它,不要手撸) |

---

## 10. 已有 assertion 兜底

🟡 部分 assertion 化(可补 candidate 多)

已有:
- 工具:`python/stock-pipeline/tools/check_entity_dataclass_parity.py`(字段名维度的跨层 parity,本规则的姐妹工具)
- 测试:`apps/stock-admin-api/src/test/java/.../EnumMetadataServiceTest.java`(Java 枚举反射暴露完整性)
- 测试:`apps/stock-admin-api/src/test/java/.../dto/DtoDocumentationConsistencyTest.java`(DTO @Schema description + example 必填)
- 测试:`apps/stock-admin-api/src/test/java/.../common/validation/JobTypeValidatorTest.java`(JobType 跨层值白名单)
- 工具:`pnpm run api` / `pnpm run enums` 强制走 Java 真值源(生成层而非断言层)
- Python 写库白名单:`planning/four_w.py:_coerce_action` / `_coerce_timing`(入库前枚举值校验)

盲区(建议未来补):
- 缺:约定测试断言"Python `FetcherType.name` 集合 ≡ Java `DataFetcherEnum.values()` 集合"(N12 Phase 4 计划补 `audit_fetcher_registry_parity.py`)
- 缺:约定测试断言"前端 `apps/stock-admin-web/src/pages/**/*.{ts,tsx}` 不含 `Record<string, string>` 硬编码业务标识 map"(需 AST 扫 + 业务关键词黑名单)
- 缺:约定测试断言"Java DTO 含 `private String xxxType` 字段 → 必有对应 EnumMetadataService 注册的同名枚举"(反射 + 命名约定扫描)
- 缺:约定测试断言"Python `fetch_*.py` 内 hardcoded 字符串值必经 `FetcherType` 枚举映射"(grep `source\s*=\s*['"][A-Z_]+['"]` 反范例)
- 缺:pre-push hook 把跨层 enum parity 工具加入 3 项 gate(参考 `verification-checklist.md §2.1`)
