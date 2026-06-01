# demo — 自包含示例项目(无需真实代码仓即可试检索)

这个目录是一份 **可索引的示例数据**,让你在没有任何真实代码仓的情况下,把它当成一个名为 `demo` 的 project 索引进 Chroma,然后用 platform-docs 检索出东西,体验 codev-platform 的文档召回能力。

> `docs/` 下 4 篇文档(架构概览 / API 约定 / 一篇事故复盘 / 一条工程规则)讲的是一个**虚构电商项目 AcmeShop**,内容真实可检索,但服务名 / 表名全是编造,不对应任何真实系统。

## 目录结构

```
demo/
├── README.md                 # 本文件(也会被索引)
├── index_demo.py             # 一键把 demo/docs 索引成 project_id=demo
├── .claude/project.json      # project_id=demo 的最小登记(resolve_local 读它)
└── docs/
    ├── architecture-overview.md
    ├── api-conventions.md
    ├── incident-2026-05-20-oversold.md
    └── rule-inventory-deduction.md
```

`.claude/project.json` 是解析 project_id 的真值源(`resolve_local` 从目标目录向上找它)。这里已写好 `{"project_id": "demo"}`,**无需再跑 `codev-platform init`**。想自己体验 init 流程:在本目录跑 `codev-platform init demo --force` 会重写它。

## 三步试检索

下面命令在 **仓库根**执行,用平台 venv(路径按你的环境调整,下例用 `.venv/bin/python`)。

### 1. 把 demo/docs 索引成 `demo` 项目

```bash
.venv/bin/python demo/index_demo.py --force
```

- 写入 collection `demo__platform_docs`,数据落在 `data/chroma/`,按 project_id 与其它项目隔离、互不干扰。
- 默认 `device=cpu`,没有 GPU 的机器也能跑;有 GPU 可 `PLATFORM_EMBED_DEVICE=cuda .venv/bin/python demo/index_demo.py --force`。
- 只看会索引哪些文件、不入库:`.venv/bin/python demo/index_demo.py --dry-run`。

> `index_demo.py` 是对 `python -m codev_platform.chroma.indexer` 的一层薄封装:它把索引目标固定为 `demo/`(解析出 project_id=demo),并在一次性进程里关掉 indexer 的 WAL 切换 + 收尾 checkpoint,保证 demo collection 落盘后能被独立的 search 进程读到。脚本头部注释有说明。

### 2. 注册到项目清单(可选,让 list-projects 能看到)

```bash
.venv/bin/codev-platform register demo --display-name "AcmeShop Demo" --repo-path demo
.venv/bin/codev-platform list-projects   # 应能看到 demo 一行
```

### 3. 检索

索引进库后,通过 platform-docs MCP(daemon)用 `search_docs` 查询,project_id 传 `demo`(端点带 `?project_id=demo`)。已验证可命中的示例查询:

| 查询 | 命中文档 |
|---|---|
| `库存为什么会超卖` | `docs/incident-2026-05-20-oversold.md` |
| `订单状态有哪些枚举` | `docs/api-conventions.md` |
| `下单链路怎么走` | `docs/architecture-overview.md` |

> MCP 端点起法见仓库 `.claude/rules/ai-tools-mcp.md`(`codev-platform serve-mcp start`)。daemon 按 `?project_id=demo` 路由到 `demo__platform_docs` collection。

## 清理

demo 数据只是 `data/chroma/` 里的一个 collection,删掉重来即可:

```bash
# 删 collection(重跑步骤 1 的 --force 会重建)
.venv/bin/python -c "import chromadb; chromadb.PersistentClient(path='data/chroma').delete_collection('demo__platform_docs')"
# 彻底移除项目登记
rm -rf platform_meta/projects/demo
```
