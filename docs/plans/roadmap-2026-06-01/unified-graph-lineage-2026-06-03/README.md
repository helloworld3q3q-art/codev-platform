# 统一图谱·全栈血缘收敛 (2026-06-03)

> 专题目录:把"跨业务链路"支柱收敛成**单一实现** —— 一套数据(统一 store)、一套生产者
> (stack 插件)、一个 linker、一个页面。终态:删掉 `cross_link` 适配器,单系统四层血缘
> `上游数据 → SQL → 后端 → 前端` 全部由 stack 插件 + 核心 linker 产出。
>
> 所属迭代:[`../`](../) (roadmap-2026-06-01)。当日日报:[`../daily-summary-2026-06-03.md`](../daily-summary-2026-06-03.md)。

## 背景:三支柱里这一支重复了

平台知识层 = **三支柱(互补不重复)**:`chromadb`(语义检索)/ `codegraph`(代码符号·调用图)/ **跨业务链路**(架构级血缘)。本专题只动第三支柱。

它现在有**两套生产者**同时往统一 store 写同一批节点 —— `cross_link` 适配器(读老 `cross_layer.sqlite`)和 stack 插件(sql/fastapi/react)。实测重复(节点数对称):

| 项目 | frontend_api_call | db_table | 说明 |
|---|---|---|---|
| codev-platform | 47(react) + 47(xlink) | 23(sql) + 0 | 接口完全重 |
| openclaw-stock | 144(react) + 144(xlink) | 80(sql) + 69(xlink) | 接口/表都重 |

## 文件清单

| 文件 | 内容 | 状态 |
|---|---|---|
| [`design-2026-06-03.md`](design-2026-06-03.md) | 技术方案:目标架构 + 生产者归属 + linker pass + 已知 seam + 防复发 | ✅ |
| [`plan-2026-06-03.md`](plan-2026-06-03.md) | 分期 P1–P4 任务 + 验证 + parity 门控 | ✅ |

## 进度

| 期 | 内容 | 状态 |
|---|---|---|
| P1 | Spring 端点插件(Java 后端节点) | ✅ 已落地+真仓验证(stock-admin-api 131 端点 vs cross_link 132,近 parity) |
| P2 | sql 插件扩 DML → writes_table / reads_table(上游→表→后端) | ✅ Python DML(openclaw writes 172 / reads 641) |
| P2b | sql 插件扫 Java MyBatis 注解 SQL → 表读写(补 Java 后端读) | ✅ @Select/@Insert 等 |
| P2c | sql 插件解析 MyBatis-Plus BaseMapper<Entity>→@TableName→表(隐式 CRUD,粗粒度 conf=0.6) | ✅ 闭合最后 Java 表读 gap:openclaw Java mapper reads 248(cross_link 旧 236) |
| P3 | 核心 linker pass 接进 ingest(前端→后端 calls_api,单一 owner builtin.linker) | ✅ 前端可链 FastAPI+Spring(openclaw 529 calls_api) |
| P4 | 删 cross_link 适配器插件 + 生产者归属防复发闸 | ✅ store 单一来源(cross_link rows=0,每 kind 单 owner) |
| P4-余 | 前端 `跨层链路` 页并入统一图谱 | ✅ KindFilter 按层分组 + 整层切换;详情面板"关联节点"(用 store 边还原表引用/端点关联);删 /codegraph/crosslink 路由+菜单+页 |

## 收敛结果(2026-06-03 实测)

| 项目 | 删前(含 cross_link 重复) | 删后(stack+linker 单一源) |
|---|---|---|
| openclaw 节点 | ~6800(5441 cross_link + dup) | **2053 干净** |
| codev 节点 | ~378(102 cross_link + dup) | **361 干净** |
| 每 kind owner | 2 套(cross_link + stack)重复 | 单一 owner |

## ⚠️ 运维坑:删插件后须**重建 store**

`upsert_result` 只删改"本轮 ingest 到的插件"行;**已退场插件**(builtin.cross_link)的旧行
不会被清,成 orphan。删插件后必须 `rm data/graph_store/<pid>.sqlite*` 再 ingest 重建
(store 是派生缓存,可安全重建)。本轮已对 openclaw / codev 两库重建确认 cross_link rows=0。

## 铁律

1. **不先删 cross_link**:Spring 插件 + linker 跑出的节点/边 parity ≥ cross_link 当前覆盖,才删。openclaw 全程不空。
2. **生产者单一归属**:每个 `kind × 语言/框架` 只能有一个 owner 插件产,P4 落约定测试兜底。
3. **方法级调用链不进 store**:那是 codegraph 支柱的地盘,统一图谱只做架构级。
