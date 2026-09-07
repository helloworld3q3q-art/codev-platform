# 前端 TS 工具链版本对齐修复 plan(2026-06-03)

> 闭合复核 `docs/audits/deep-audit-2026-06-03-review.md` 第三波技术债 #6(原 deep-audit P2#7/#12)。
> 本文件只做诊断 + 修复路线对比,**不改 package.json / 源码**(实施另开 PR)。

## 一、精确根因(已源码核实)

`pnpm tsc` 报 **TS1139 Type parameter declaration expected**,根因是 **TS 版本与依赖声明文件语法不兼容**:

- `web-ui/package.json` 钉 `typescript ^4.9.5`(实测 node_modules 装的就是 4.9.5)。
- 依赖 `@ant-design/pro-form@2.32.0`(经 `@ant-design/pro-components@3.1.11-0` 传递引入)的声明文件
  `node_modules/@ant-design/pro-form/{es,lib}/components/FormItemRender/index.d.ts` 第 24 / 27 行用了
  **TS 5.0 才有的 const 类型参数**语法:`useControlModel<const T extends readonly string[]>(...)`。
- TS 4.9 解析器不认 `const T`,在 parse 阶段就抛 TS1139。pro-form 2.32.0 自己的 `devDependencies.typescript` 写的是 `^5.0.4`,佐证它就是按 TS5 写的。

**关键:`skipLibCheck: true` 救不了。** tsconfig 已开 `skipLibCheck`(line 12),但它只跳过 `.d.ts` 的**类型检查**,TS 仍要**解析**所有被 import 的 `.d.ts`。TS1139 是**语法/解析错误**,不在 skipLibCheck 豁免范围 → 这就是为什么开了 skipLibCheck 仍然失败。

(同因牵出 `fetch.ts` 顶部 `@ts-nocheck` + `typings.d.ts` 大量 `any` — 是历史为绕过 tsc 留下的债,见 §五。)

## 二、两条修复路线对比

### 路线 A:升级 typescript → 5.x(推荐)

| 项 | 内容 |
|---|---|
| 改哪个 dep | `package.json` devDependencies `typescript: ^4.9.5` → `^5.4.0`(5.4 稳定且兼容 umi4 / pro v3) |
| 预期 fallout | TS5 解析器吃下 `const T`,TS1139 消失。但 TS5 比 4.9 更严:`useDefineForClassFields`、更严的 lib.d.ts、可能暴露**项目自身**此前被宽松放过的类型错(见 §五风险)。`@umijs/max@4.6.x` 官方支持 TS5,低风险。 |
| 连带 | 无需动任何 `@ant-design/pro-*` 版本;ts-node(scripts/ 用)与 TS5 兼容。 |

### 路线 B:把 @ant-design/pro-* 锁回兼容 TS4.9 的版本

| 项 | 内容 |
|---|---|
| 改哪个 dep | `package.json` dependencies `@ant-design/pro-components: ^3.1.11-0` → 降到引入 const 语法**之前**的 pro-form 对应版本。const 类型参数是 pro-form 2.32.x 引入,需降到 pro-form ≤ 2.31.x 对应的 pro-components(约 `2.8.x`,需逐版本试)。 |
| 预期 fallout | **高且发散**。① pro-components v3 是为 antd6 适配的线;本项目钉 `antd ^6.3.1`,降到 pro-components v2(适配 antd5)会撞 antd6 peer → 连锁要降 antd → 再撞 `@jlogi/ui` / `antd-style` / 一堆 antd6 适配代码(见 `.claude/rules/antd6-adapt.md` 已落地 60+ 处 antd6 改造,全部回退)。② v3 line 里 pro-* 子包是绑定打包的,单独锁 pro-form 难,pnpm overrides 易破坏内部一致性。 |
| 连带 | 触发 antd6 → antd5 全面回退,等于推翻已完成的 antd6 迁移,代价远超收益。 |

## 三、推荐路线 + 理由

**选路线 A(升 TS5)**。理由:① 改一个 devDependency 即可,改动面最小;② pro-components v3 + antd6 是项目既定方向(antd6 适配已大面积落地),路线 B 等于全面回退 UI 栈,代价巨大且发散;③ pro-form 作者本就按 TS5 写声明,顺势对齐工具链是正解;④ TS5 暴露的项目自身类型错是**真债**,正好借此清理(配合 §五解 @ts-nocheck)。

## 四、用户需手动执行的命令(AI 不跑 install / build,见 workflow §11)

```powershell
# 1. 用户手改 web-ui/package.json: devDependencies.typescript "^4.9.5" -> "^5.4.0"
#    (实施 PR 里改;本 plan 不动 package.json)

# 2. 装依赖(用户手动)
pnpm --dir C:\workspace\project install

# 3. 验证 TS1139 是否消失 + 暴露的自身类型错清单
pnpm --dir C:\workspace\project run tsc

# 4. 回归构建(确认 umi/max 在 TS5 下正常)
pnpm --dir C:\workspace\project run build
```

## 五、移除 @ts-nocheck 的后续步骤(TS1139 解决后单独做)

当前 2 处 `@ts-nocheck`:`src/utils/fetch/fetch.ts`、`src/components/Form/Select/DemoSelect.tsx`。

1. 先完成 §四,确认 `tsc` 在 TS5 下不再报 TS1139(全局阻塞先清掉)。
2. 逐个删 `@ts-nocheck`,跑 `pnpm run tsc` 看该文件暴露的真实类型错。
3. `fetch.ts`:多为 `any` / 泛型缺失 / `responseHandle` 返回类型 — 按 `api-service.md` 的 `BaseApiResponse<T>` 补类型,不要用 `as any` 糊。
4. `DemoSelect.tsx`:Demo 文件,评估是否直接删除(若无引用)而非补类型。
5. 收紧 `typings.d.ts` 的 `any`:这是 `pnpm run api` 生成物(`api-service.md` 标注禁手改),根因在 swagger 生成器 / 后端 DTO `@Schema` 缺失 → 应回到生成链路修,**不在前端手改 typings.d.ts**。
6. 全部清完后,评估给 `tsconfig` 加 `"noImplicitAny": true`(strict 已开,此项目前被 @ts-nocheck/any 掩盖)。

## 六、风险

- **R1(主):TS5 暴露项目自身新类型错。** strict 已开但 4.9 较宽松,升 5.4 后 `tsc` 大概率冒出一批此前隐藏的错(尤其被 @ts-nocheck/any 掩盖处)。**缓解**:§四第 3 步先拿到完整错误清单评估工作量;若量大,分批修,不阻塞主线(tsc 非 CI 必跑门禁)。
- **R2:umi/max 插件在 TS5 下行为差异。** `@umijs/max@4.6.37` 文档支持 TS5,风险低;`scripts/*.ts`(ts-node)需回归一遍(`pnpm run api` / `enums` / prebuild)。
- **R3:lint 工具链联动。** `@umijs/lint` / eslint 的 TS parser 版本需与 TS5 对齐,装完跑一次 `pnpm run lint` 确认无 parser 崩溃。
- **R4:`@jlogi/ui` 等内部包**若自带 TS4 风格声明,TS5 一般向后兼容,低风险;若报错按 §五同法处理。
