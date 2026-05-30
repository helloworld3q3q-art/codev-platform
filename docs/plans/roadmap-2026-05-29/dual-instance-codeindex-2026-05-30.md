# Plan — 双实例代码智能(本地活跃 + 平台基线)+ 无服务器 WSL 落地 2026-05-30

> **定位**:同一套平台软件、两处部署 —— **本地实例**索引开发者 working tree(含未提交,服务活跃编辑);**平台基线实例**由 GitLab 事件驱动索引 HEAD(服务跨项目 / onboarding / 团队共享真值)。用户可在两者间切换。
>
> **关联**:`mcp-service-ification-2026-05-30.md`(MCP 服务化,已落地 P0-P5)/ `codev_platform/ops/{reindex,codegraph}.py` / `serve-mcp` / gateway 认证。

---

## 一、目标(用户拍板 2026-05-30)

1. **平台服务器一套**:检测到 git 提交(push 到 GitLab)→ 更新平台索引(三库)。
2. **本地一套**:有提交(commit)**或**获取新的(pull)→ 更新本地索引。
3. **平台基线给 agent / 团队 / 跨项目用**;**本地服务开发者的活跃编辑**。
4. **用户可切换**:本地客户端可选连"本地实例"或"平台实例",由用户按场景定。
5. **物理约束**:目前**无独立服务器、无独立显卡** → 先用本机 **WSL2(Linux)** 当平台基线实例落地验证。

---

## 二、架构:吃 GPU 的共享一份,要分本地/平台的恰好不吃 GPU

关键洞察(2026-05-30):**模型是编码器,与编码哪份数据无关 → 一份模型(一个 daemon)能服务所有 collection。** 而真正需要"本地 vs 平台两份"的 codegraph/cross-link 是纯 sqlite,不吃 GPU。所以不是"双实例全套各一份",而是:

```
┌──────────────────────── 同一台开发机(8GB GPU)────────────────────────┐
│                                                                        │
│  ┌── chroma/docs daemon ── 只一个, 模型加载一次 ~3GB, 文档索引共享 ──┐ │
│  │   ▲ Windows + WSL 都连它(docs 本地/HEAD 差异小, 无需分两份)      │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                        │
│  Windows = 本地代码实例(无 GPU)       WSL2 = 平台基线代码实例(无 GPU) │
│  ┌────────────────────────┐           ┌────────────────────────────┐  │
│  │ codegraph + cross-link  │           │ codegraph + cross-link      │  │
│  │ = 你的 working tree     │           │ = GitLab HEAD               │  │
│  │ data/ 在 Windows        │           │ data/ 在 WSL                │  │
│  │ 触发: post-commit       │           │ 触发: GitLab webhook        │  │
│  │     + post-merge(pull)  │           │     (push 后服务端 pull)    │  │
│  │ SSE :1808x              │           │ SSE :1909x                  │  │
│  └────────────────────────┘           └────────────────────────────┘  │
│        ▲ 默认连本地                          ▲ 用户可切换连平台          │
│        └────── Claude Code (.mcp.json: docs 连共享 daemon;             │
│                 codegraph/cross-link 连本地或平台, 用户可切)───────────┘
└────────────────────────────────────────────────────────────────────────┘
   未来真有团队服务器: 平台基线实例(codegraph/cross-link + 可选 docs)原样搬服务器, WSL 只是本机替身。
```

- **chroma/docs**:单份共享 daemon(模型只加载一次)→ **8GB 卡永不双载, OOM 风险消失**。文档在 working tree 与 HEAD 间差异小,共享一份够用。
- **codegraph + cross-link**:本地(working tree)+ 平台(HEAD)两份,**纯 sqlite 零 GPU 成本**,各自独立 `data/`,互不污染。本地写本地,平台只 webhook 写。

---

## 三、四个更新触发器

| 实例 | 触发 | 机制 | 现状 |
|---|---|---|---|
| 本地 | commit | `post-commit` hook → reindex 三库 | ✅ 已有 (`ops/reindex.py post-commit`) |
| 本地 | **pull / merge / checkout** | **`post-merge` + `post-checkout` hook** → reindex | ⚠️ **新增**(复用 post-commit 的 scope-diff 逻辑) |
| 平台 | push 到 GitLab | **GitLab webhook → 服务端 `git pull` + reindex** | ⚠️ 新增(webhook 接收 + reindex 已有) |
| 平台 | 手动 / 定时 | `codev-platform reindex`(已有)+ cron | ✅ 已有 |

**唯一真·新技术点 = 本地 `post-merge`/`post-checkout` hook**(让本地索引在"拉到别人代码"后也更新,不只在自己提交时)。GitLab webhook 是新组件但 reindex 逻辑现成。

---

## 四、用户可切换源

`.mcp.json` 的 URL 决定连哪个实例:

```jsonc
// 连本地(活跃, 默认)
"codegraph": { "type": "sse", "url": "http://127.0.0.1:18091/sse" }
// 切平台(基线)
"codegraph": { "type": "sse", "url": "http://<平台或WSL>:19091/sse" }
```

落地:`codev-platform mcp-source local|platform`(改写业务仓 `.mcp.json` URL,或写 env)。用户按场景选:
- **写得多、未提交多** → 本地(看你的活)。
- **跨项目 / 不想本机跑 GPU / 要团队合并真值** → 平台。

进阶(以后):本地实例当唯一入口,按 project_id 路由 —— 本地有的本地答,没有的代理到平台。`.mcp.json` 永远一个 URL。

---

## 五、单写者纪律 + 本地分歧兜底(多人前必守)

1. **平台实例只由 webhook 写**(单写者);本地 reindex **绝不写平台 data/** —— 否则多人互相清洗(A 的本地态覆盖共享基线)。
2. **本地分歧 = `git diff`(working tree vs 已索引 ref)** → 命中文件走 grep+Read 兜底(扩 `dirty-index-check`:基准 ref 从本地 HEAD → 可配为 `origin/HEAD`)。
3. **"一致"的准确含义**:各实例跟住各自的源(本地=working tree,平台=GitLab HEAD);差值 = 你未提交+未 push 部分。全 push 干净时一致。

---

## 六、无服务器现实:WSL2 当平台基线实例(GPU 顾虑已化解)

**结论:能,且 8GB 卡够用** —— 因为吃 GPU 的 docs 只一份共享,要分两份的代码索引不吃 GPU(§二)。

**模型共用一份(两层都共用)**:
1. **磁盘文件**:WSL `embed_path` 指 `/mnt/d/models/Qwen3-Embedding-0.6B`(直读 Windows 那份),**不再下载/拷贝**。模型加载是启动一次性读,`/mnt` 慢点无所谓(不像索引 IO 频繁);想更快才拷进 WSL fs(那是第二份,放弃"共用")。
2. **显存模型**:**只跑一个 chroma/docs daemon**(模型加载一次 ~3GB),Windows 与 WSL 的客户端都连它。模型是编码器、与数据无关,一份能服务所有 collection → **永不双载,无 OOM**。

**docs daemon 跑哪边?** 二选一,都行(只起一个):
- 跑 **Windows**(沿用现状 :18083):WSL 平台实例的 docs 也连 Windows :18083。
- 跑 **WSL**(:18083 in WSL):Windows 客户端连 WSL。
- 起步沿用现状(Windows daemon),零改动。

**WSL 上只需起无 GPU 的 codegraph + cross-link 平台实例**(端口 :1909x),验证整套"双实例 + 切换 + webhook",完全不碰 GPU。

**WSL 接入要点**:
- `wsl --install`(Ubuntu);**代码仓 clone 进 WSL 原生 fs**(`~/...`,别放 `/mnt/d`——git/索引 IO 在 DrvFs 上慢);**模型文件反而走 `/mnt/d` 共用**(只读一次,可接受)。
- 若 WSL 也想跑 docs(一般不必,共用 Windows 的即可):装 CUDA-on-WSL(Windows 驱动装好即可),但**别和 Windows docs daemon 同时各起一个**(那才双载)。
- WSL 实例自己的 `~/.codev-platform/config.json`(Linux 路径、自己的 `data_root`、codegraph/cross-link 端口 :1909x;`platform.url` 的 docs 指向那个唯一 daemon)。
- 验证:`codev-platform setup --auto`(已跨平台)在 WSL 内跑。

### 6.1 两种部署形态(同软件,config 决定;别把"共用一份"写死)

"docs 共用一份"**只是本机开发期(WSL+Windows 同卡 8GB)的省卡做法**。真实部署时平台服务器有**自己的显卡** → 就该**跑自己的 docs daemon、加载自己的模型**,不再共用本地的。两形态靠 config 切换,**零改代码**:

| 维度 | 本机开发(WSL co-located) | 生产部署(平台独立服务器) |
|---|---|---|
| docs daemon | **一个**,跑 Windows :18083,WSL 共用 | 平台服务器**自己一个**(自己的 GPU);开发者本机各自一个 |
| GPU | 同一块 8GB,只加载一份模型 | 平台用**服务器自己的卡**;本机用本机卡,互不相干 |
| 模型文件 | 共用一份(WSL 走 /mnt/d) | 各机器各自一份(各自磁盘) |
| `embed_device` | `cuda`(共用)/ `cpu`(WSL 不抢卡时) | 平台服务器 `cuda`(自己的卡);无卡机器 `cpu`/`mps` |
| docs 来源 | `.mcp.json` 的 docs URL 指那个唯一 daemon | 各连各自的 docs daemon URL |

**必须预留的配置空间(全已 config 驱动,本计划只是显式确认 + 别写死)**:
- `models.embed_path` / `reranker_path` —— 每实例各自指(本机 D:\models / 服务器自己的路径)。
- `models.embed_device` —— 每实例各自定(`cuda`/`cpu`/`mps`),平台服务器走自己的卡。
- `search.gpu_concurrency` —— 按该机显存定(8GB=1;服务器大卡可调高)。
- `daemon.port` / `platform.url` / 各 MCP 端口 —— 每实例独立,客户端按 URL 连对应 daemon。
- `data.platform_data_dir` —— 每实例自己的 data 根。

> 红线:代码里**不得假设"全局只有一个 docs daemon"或"共用某台的卡"**。共用是开发期 config 的一种取值,不是写死的架构。生产部署改 config(平台 `embed_device=cuda` 指自己的卡 + 自己的 daemon)即成立。

---

## 七、复用 vs 新建

| 已有(直接复用) | 新建 |
|---|---|
| `ops/reindex.py`(三库 reindex + post-commit) | `post-merge`/`post-checkout` hook(本地 pull 触发) |
| `serve-mcp`(端点编排 + 常驻 + 健康) | GitLab webhook 接收端点 + 验签(挂 gateway auth) |
| gateway 认证(token/passthrough) | webhook → `git pull` + reindex 编排(服务端) |
| `codegraph link`(数据集中 junction) | `mcp-source local\|platform` 切换命令 |
| config 驱动 data_root / 端口 / platform.url | dirty-check 基准 ref 可配(本地 HEAD ↔ origin/HEAD) |
| 跨平台 ops(Win/Linux/Mac) | WSL 部署文档 + GPU 编排约定 |

---

## 八、分阶段

| 阶段 | 内容 | 验证 |
|---|---|---|
| **P0 本地 pull 触发** | 加 `post-merge`/`post-checkout` hook(复用 post-commit scope-diff),`install-git-hooks` 一并装 | pull 后本地索引自动更新 |
| **P1 WSL 平台实例(无 GPU 部分)** | WSL2 装环境;codegraph + cross-link 平台实例跑通(端口 :1909x);`serve-mcp` 在 WSL 起 | WSL 内 `serve-mcp status` 绿;Win 客户端连 WSL URL 取到 HEAD 基线 |
| **P2 GitLab webhook reindex** | 平台 HTTP 收 webhook(验签 via gateway)→ `git pull` + reindex;GitLab project→project_id 映射(config) | push → 平台基线自动更新;`health` 报 webhook 触发记录 |
| **P3 源码获取 + 凭据** | 平台侧持有/拉取业务仓源码(deploy token/SSH key 进 config secret) | 平台能 clone+pull 业务仓 |
| **P4 可切换源** | `mcp-source local\|platform` 切换 `.mcp.json`;文档化"何时用哪个" | 一键切换,两边都连得通 |
| **P5 docs 单份共享确认** | 全程只起**一个** docs daemon(沿用 Windows :18083),WSL 平台实例 docs 指向它;`ai-health` 确认模型只加载一份 | 只一个 docs daemon;8GB 不 OOM |
| **P6(团队上线前)单写者 + diff 兜底** | 平台实例禁本地写;dirty-check 基准切 origin/HEAD;规则补"多人禁本地写平台" | 多人不互相清洗 |

---

## 九、风险与"现在不做"

- **GPU 不再是阻塞**(原以为是):**同机同卡(开发期)**docs 共用一份、模型只加载一次 → 8GB 够,红线是别在同一块卡上起两个 docs daemon。**生产部署平台服务器用自己的卡、自己的 daemon**(不同机器不抢,§6.1)。代码不得写死"全局一个 daemon / 共用某卡"——共用是 config 取值非架构(§6.1 配置空间)。
- **平台必须能拿到源码**:webhook 只通知,索引要源码 → 平台 `git pull`,需凭据 + 磁盘。单机 WSL 阶段:直接 clone 一份到 WSL。
- **"一致"是收敛不是恒等**:本地永远比平台多"你未提交"的部分,这是 feature 不是 bug(§五)。
- **不做**:不上 k8s / 不分布式 / 不多 GPU 编排 —— 现阶段单机 WSL 验证架构即可,真服务器再谈伸缩。
- **不做**:平台实例不替开发者维护"未提交代码"(物理做不到,§铁律)。

---

## 十、验收

- [ ] 本地 `git pull` 后,本地索引自动更新(post-merge hook 生效)。
- [ ] WSL 平台实例 codegraph + cross-link 跑通;Windows 客户端切到 WSL URL 能查到 GitLab HEAD 基线。
- [ ] GitLab push → 平台基线实例自动 reindex(webhook 链路通)。
- [ ] `mcp-source local|platform` 一键切换,两实例数据各自独立(本地含未提交,平台=HEAD)。
- [ ] 全程只一个 docs daemon(模型加载一份),8GB GPU 不 OOM;模型文件 Windows/WSL 共用一份(WSL 走 /mnt/d)。
- [ ] (团队前)平台实例不被本地 reindex 污染;本地分歧走 diff 兜底。

---

## 十一、与既有的关系

- **本地实例 = 现成**:你这台 Windows 跑的就是,本计划**不动它**(只加 pull 触发 hook)。
- **平台基线实例 = 同软件新部署**:WSL 是它"没服务器时"的本机替身;未来原样搬真服务器。
- 接 `mcp-service-ification` 的 SSE 服务化 + `codegraph link` 数据集中 —— 那两步让"实例可远程连 + 数据归实例 data/"成立,是本双实例方案的地基。
