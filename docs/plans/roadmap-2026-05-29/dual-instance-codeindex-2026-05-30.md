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

## 二、架构:一套软件,两处部署

```
┌───────────────────────────── 同一台开发机 ──────────────────────────────┐
│                                                                          │
│  Windows 主机 = 本地实例(活跃层)        WSL2 Linux = 平台基线实例(共享层) │
│  ┌────────────────────────┐             ┌────────────────────────────┐  │
│  │ chroma/codegraph/cross  │             │ chroma/codegraph/cross      │  │
│  │ 索引 = 你的 working tree │             │ 索引 = GitLab HEAD          │  │
│  │ data/ 在 Windows         │             │ data/ 在 WSL                │  │
│  │ 触发: post-commit +      │             │ 触发: GitLab webhook        │  │
│  │       post-merge(pull)   │             │       (push 后服务端 pull)  │  │
│  │ MCP SSE :1808x (本地)    │             │ MCP SSE :1909x (平台)       │  │
│  └────────────────────────┘             └────────────────────────────┘  │
│        ▲ 默认连这个                              ▲ 用户可切换连这个        │
│        └──────── Claude Code (.mcp.json URL 二选一 / 可切换) ─────────────┘
└──────────────────────────────────────────────────────────────────────────┘
   未来真有团队服务器: 平台基线实例原样搬到服务器, WSL 只是它的本机替身。
```

**两实例各有独立 `data/`,物理隔离,互不污染。** 本地写本地,平台只 webhook 写。

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

## 六、无服务器现实:WSL2 当平台基线实例(关键节)

**结论:能。** WSL2 跑得了整套(Python + config 驱动跨平台),且能直通 Windows NVIDIA 卡(CUDA-on-WSL2),无需独立显卡服务器。

**GPU 8GB 硬约束**(必须遵守,否则 OOM):
- chroma 的 Qwen embedding(~3GB)+ reranker → **不能 Windows 本地 + WSL 平台同时各加载一份**(总和超 8GB)。
- codegraph / cross-link = 纯 sqlite,**不吃 GPU**,WSL 里随便跑。

**落地策略(三选一,按需)**:
| 方案 | chroma(GPU) | 适用 |
|---|---|---|
| A. 时间错开 | 同一时刻只一个实例加载 Qwen | 单人验证 / 不同时用两边 docs |
| B. WSL chroma 走 CPU | WSL 平台实例 `embed_device=cpu`(慢但不占 GPU) | 平台 docs 召回不频繁时 |
| C. WSL 用 MiniLM fallback | 仓内 `paraphrase-multilingual-MiniLM`(小、CPU 可) | 基线 docs 够用即可 |

> 起步建议 **A 或 C**:WSL 平台实例先把 **codegraph + cross-link**(无 GPU)跑通验证整套部署 + webhook,chroma 基线用 CPU/MiniLM 或暂缓。验证完再谈 GPU 编排。

**WSL 接入要点**:
- `wsl --install`(Ubuntu);仓 clone 进 WSL 原生 fs(别走 `/mnt/d`,git/IO 慢)。
- WSL 装 NVIDIA CUDA-on-WSL(Windows 侧装好驱动即可),`torch.cuda.is_available()` 在 WSL 内应为 True。
- WSL 实例自己的 `~/.codev-platform/config.json`(Linux 路径、自己的 `data_root`、端口错开 Windows 的 :1808x → 用 :1909x)。
- 验证:`codev-platform setup --auto`(我们已做跨平台)在 WSL 内跑。

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
| **P5 chroma 基线 GPU 编排** | WSL chroma 走 CPU/MiniLM 或与 Windows 实例时间错开;`ai-health` 报双实例 GPU 占用 | 不 OOM;基线 docs 可召回 |
| **P6(团队上线前)单写者 + diff 兜底** | 平台实例禁本地写;dirty-check 基准切 origin/HEAD;规则补"多人禁本地写平台" | 多人不互相清洗 |

---

## 九、风险与"现在不做"

- **GPU 8GB 是硬天花板**:双实例 chroma 不能同时占卡。先 codegraph/cross-link 验证,chroma 基线后置(§六)。
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
- [ ] 8GB GPU 不 OOM(双实例 chroma 不同时占卡)。
- [ ] (团队前)平台实例不被本地 reindex 污染;本地分歧走 diff 兜底。

---

## 十一、与既有的关系

- **本地实例 = 现成**:你这台 Windows 跑的就是,本计划**不动它**(只加 pull 触发 hook)。
- **平台基线实例 = 同软件新部署**:WSL 是它"没服务器时"的本机替身;未来原样搬真服务器。
- 接 `mcp-service-ification` 的 SSE 服务化 + `codegraph link` 数据集中 —— 那两步让"实例可远程连 + 数据归实例 data/"成立,是本双实例方案的地基。
