# Phase 1 原子索引切换(atomic handoff)— 实现 plan(2026-06-14)

> 蓝图 Phase 1 重型项之一(`code-intelligence-platform-plan-2026-06-07.md` §Phase 1 "atomic handoff: 新索引构建成功后再切换 current pointer")。
> 三兄弟对抗面板(YAGNI / 架构纯度 / 多机 arc ROI)收敛后**纠正切片方向**落地。

## 〇、缘起:面板纠正了 Phase 1 的切片选择

原判("做 depends_on 声明式排序 / atomic handoff 缓做")被面板双向推翻:

- **YAGNI 兄弟(实读代码)**:reindex stage 仅 **4 个**,顺序集中在 `ops/reindex/commands.py` 单函数线性块,worker 纯串行不管序;code_vec 读新鲜 codegraph 的真依赖已被同子进程 `['--codegraph','--code-vec']` 纵深解决。→ `depends_on`+toposort 是**负收益重构**(把单处可读线性拆成两层),**砍掉**,触发=stage≥6 且真跨 job 序约束。
- **多机 arc 兄弟**:atomic handoff 才是主线(多机/多组织/多人连用)**直接触发的刚需** —— 多 reader 并发读共享索引,而 Stage C 的 PG 后端只挡"写撞写",**没覆盖"writer 重建时 reader 读半成品"的读侧路径**。

## 一、定位与消费方(为什么做)

**Phase 1 atomic handoff = 让 full rebuild 期间 reader 永不读到半成品索引。**

**真实痛点(已实证,非臆想)**:`daily-summary-2026-06-12 §十.1` —— full rebuild 原地 rmtree chroma 库,重建中途 SIGKILL 把库半写坏在 flush 中途,reader(serve-mcp daemon)读到 `database disk image is malformed`,**search_docs 全项目下线**。单机已踩;多机多 reader 并发放大此窗口。

**消费方 = 所有 chroma reader**:platform-docs daemon(search_docs)、code_vec 召回 lane(recall_code)。它们经 `chroma_docs_dir(pid)` / `_code_vec_persist_dir(pid)` 拿库路径。

**scope 边界(只做最痛的,必要才上)**:
| store | 撞读窗口 | 本轮 |
|---|---|---|
| chroma docs `docs/<pid>/` | **大**(rmtree 重建,实测半写坏)| ✅ 做 |
| chroma code_vec `code_vec/<pid>/` | **大**(同构 rmtree 全量重建)| ✅ 做(复用同模块)|
| codegraph `codegraph.db` | 小(sqlite WAL,reader 读快照)| ❌ 不做 |
| graph store `<pid>.sqlite` | 小(sqlite 单文件事务)| ❌ 不做 |

## 二、核心设计:blue-green 双缓冲 + 跨平台原子 pointer

writer 不再原地 rmtree+重建,而是**建到 side build 目录**,建成后**原子切 current pointer**;reader 读 pointer 指向的目录 → 全程读旧 build,绝不撞半成品。失败的 side build 不影响 current。

```
data/chroma/docs/<pid>/
├── current.json          # {"build": "<build_id>"} —— pointer(唯一真值)
├── builds/
│   ├── <old_build_id>/   # reader 持有的旧连接仍读这里(GC keep≥2 保命)
│   └── <new_build_id>/   # writer 建好后 commit,current 指向它
├── .reindex.lock         # 控制路径:落 base 根(不进 build dir)
└── .manifest.json 戳     # 控制路径:落 base 根
```

**为什么 pointer 文件 + `os.replace`,不用 symlink / 目录 rename**(关键决策,经得起证实):
- **symlink**:Windows 需开发者模式/管理员权限 → 不可移植(违反平台跨平台约束)。
- **rename 整个 build 目录覆盖 live**:目标非空时 POSIX 报 `ENOTEMPTY`、Windows 报错 → 不可跨平台原子。
- **pointer 文件 + `os.replace(tmp, current.json)`**:POSIX `rename(2)` + Windows `ReplaceFile` 都保证**同卷原子替换单文件** → 跨平台稳,且 pointer 是单一真值不会半写。

**向后兼容(零迁移)**:`resolve_current(base)` 无 `current.json` 时**退回 base 本身** → 现有所有原地库继续工作,直到第一次 full rebuild 才走新布局。无需迁移脚本、无需停机。

## 三、模块分层(轻量聚合 / 低耦合 / 可扩展)

```
core/index_handoff.py        纯核(functional core)—— 唯一新文件
  resolve_current(base) -> Path        reader 入口:current 指向 / 无则退 base
  begin_build(base, build_id) -> Path  writer 入口:side build 目录(同 id 先清)
  commit_build(base, build_id)         原子切 current(tmp + os.replace)
  gc_builds(base, keep=2) -> list[str] 删非 current 旧 build,保最近 keep
  纯路径 + os.replace 逻辑,不碰 chroma/sqlite,可脱服务单测(Windows 可验)。

core/paths.py                适配:chroma_docs_dir 数据路径走 resolve_current
recall/code_vector_store.py  适配:_code_vec_persist_dir 同构复用
chroma/indexer.py            编排:full rebuild → begin/commit/gc;增量 → 原地
chroma/_models.py 等 reader   零改(经 chroma_docs_dir 自动拿 current)
```

**设计模式**:blue-green 双缓冲(current pointer + versioned build dirs);functional core(纯函数无状态,确定性可测)。**刻意不上**:不引编排框架、不做事务管理器(单 writer 串行 + 原子 pointer 已够)。

**关键低耦合点**:reader 经 `chroma_docs_dir` 单一入口拿路径,内部改走 `resolve_current` → **reader 几乎零改**;writer 在 full/增量分支显式调 begin/commit。数据路径(走 current)与控制路径(`.reindex.lock` / 新鲜度戳 / manifest 落 base 根)严格区分。

## 四、关键决策

**① full rebuild 走 side+swap;增量原地改 _当前 build_(非 base 根)** —— atomic handoff 与增量索引(变更0→0.9s)冲突:每次 side full rebuild 会丧失增量价值。故只 full rebuild(`--force` / 参数指纹变 / 库损坏自愈)`begin_build` 到新 side;**增量走 `resolve_current(base)` 定位当前 build 原地改**(撞读窗口极小,chromadb WAL 下 reader 读快照)。
> 🔴 **风险兄弟纠正(2026-06-14)**:增量的"原地"是 **current 指向的 build 内原地**,**绝不是 base 根**。若 full 切到 `builds/<id>/` 后增量仍写 base 根 → reader 读 side build → **增量改动静默不可见**(不报错/不崩,search_docs 返陈旧结果)。平台 99% 流量是 post-commit 增量,这是最阴的炸点。`build_dir = begin_build(...) if full else resolve_current(base)` 是 writer 唯一库目录解析点。

**② build_id 由调用方传入,模块不自产** —— `index_handoff` 纯函数可测(不引 `time`/`random` 不确定源);indexer 传 `git_commit[:12]`,无 commit 时传调用方给的时间戳串。

**③ GC keep=2 不激进** —— commit 后,reader 的旧连接可能仍读旧 build;保最近 2 个(current + 前一个)给旧连接读完。不做基于引用计数的精确回收(过度设计),keep=2 + 下次 rebuild 时 GC 足够。

**④ base 根旧库残留** —— 首次切换后,旧的原地库文件(`chroma.sqlite3` 等)留在 base 根成残留(不能删:commit 前 reader 还在读它)。提示运维首次切换确认后手动清一次,或后续加一次性迁移(本轮不做,避免删正在读的库)。

## 五、分阶段交付(模块化 / 每阶段独立可验)

| 阶段 | 交付 | 验证 | 风险 |
|---|---|---|---|
| **1(本轮)** | `core/index_handoff.py` 纯模块 + 完整单测 | `pytest tests/test_index_handoff.py`(Windows 可跑)| **零**(不碰服务/索引)|
| 2 | chroma docs writer(full→side+swap)+ `chroma_docs_dir` 适配 + reader 验证 | WSL 真机 full rebuild + daemon 读不中断 | 中(改 writer/reader)|
| 3 | code_vec 复用同模块(`_code_vec_persist_dir` + `build_code_vector_index`)| WSL code_vec 重建 + recall_code 不中断 | 中 |
| 4(可选)| manifest 记 build_id + `index status` 显示当前 build | 单测 + CLI | 低 |

**阶段 1 是干净起点**:纯路径逻辑、Windows 可验、零服务风险,且是阶段 2/3 复用的单一真值核。

## 六、影响面 / 回退 / 禁改

- **影响消费方**:阶段 2/3 才触及 reader/writer;阶段 1 仅新增文件,零现有调用方影响。
- **回退**:`resolve_current` 无 pointer 退回 base = 老路径;阶段 2/3 若出问题,删 `current.json` 即回原地布局(reader 自动退回 base)。
- **禁改**:不动 `data/`/`.venv/` 运行态;不并发重建(写侧串行,workflow §8);真机重建走 reindex worker 非手动 ingest([[graph-rebuild-via-worker-not-manual-ingest]]);WSL 运维走 PowerShell([[codev-platform-ops-via-wsl]])。
- **禁止 AI 自动执行**(workflow §11):`serve-mcp start` / 全量 reindex —— 阶段 2/3 真机验证需用户授权或走单项目 enqueue。

## 七、不做(防过度设计)

- 不做 sqlite 系(codegraph/graph)handoff —— WAL 已给 reader 快照隔离,无实测痛点。
- 不做增量的 side build —— 破坏增量价值,撞读窗口本就极小。
- 不引 symlink / 目录 rename —— 不跨平台。
- 不做引用计数精确 GC —— keep=2 足够,过度设计。
- 不做 Phase 1 的 depends_on/toposort、dashboard、count 回填 —— 面板判 YAGNI / trigger-gated。

## 八、三兄弟评审纠正(2026-06-14,阶段 2 实现前)

派实现/YAGNI/风险三兄弟对抗评审阶段 2。结论:**blue-green 必要且已是最轻形态**,但纠正实现细节。

**YAGNI 兄弟 —— 更轻替代全证伪,blue-green 不可约**:
- 内存层 handoff:chromadb collection 是**懒句柄非内存快照**,rmtree 删文件后 `col.query()` 即崩。
- 目录 rename swap:目录 rename 跨平台**不原子**(两步,中间窗口)+ Windows reader 持 sqlite 句柄时 rename `PermissionError`。
- chromadb 原生:无 snapshot/事务;且它自己(1.5.9 多 flush compaction)就是损坏源。
- `.building` 标志:**SIGKILL 杀 writer 没机会清标志** → 永久残留。
- 不可约内核:故障 = SIGKILL + 旧 build 有活跃 reader 句柄 → 唯一出路 = 新库写**不同路径** + reader 经 **pointer 间接**。pointer 文件 + `os.replace` 已是该内核最小实现。

**实现兄弟 —— 最小侵入改法(净简化)**:
- `PERSIST_DIR`/`MANIFEST_PATH` 是 indexer **私有模块级常量、无外部 import** → 安全降为 `index()` 局部 `build_dir`。
- **3 处 `client.delete_collection` 全删**(side 库本就空,无需清旧)→ 净 **-18 行**;改完约 531~536 行,**稳 ≤600,无需抽 helper 文件**。
- 3 处 full 判定(force/bootstrap/params)统一成**单布尔 `full_rebuild`,只置位**;`begin_build` 在 params 检测**之后**、`get_or_create_collection` 之前**唯一调用一次**。
- `commit_build` + `gc` 放 `_save_manifest`+integrity 探针**全部成功之后、return 之前**;异常路径自然跳过 commit → side build 成孤儿但 current 不动(**不 try/except 吞异常**)。
- 早返回(无变更)/ 增量分支**绝不 `begin_build`/`commit`/`gc`**(否则建空目录留垃圾 / gc 误删)。

**风险兄弟 —— 上线失败模式 + 缓解(纳入实现)**:
| 失败模式 | 缓解(已纳入) |
|---|---|
| **🔴 增量退化(最阴)** | 增量走 `resolve_current(base)` 改当前 build,非 base 根(见 §四①纠正) |
| gc 删正被读的旧 build | **gc 延到下次 `begin_build` 前**(reader 那时已 reload),不在 commit 后立即 gc;keep=2 |
| 磁盘翻倍(大项目 ×2~3) | keep 可配 + 大项目建前预检 `df`(本切片先记,大项目触发再加) |
| pointer 丢失 fail-soft 退 base 读陈旧残留 | 首次 commit 后清 base 根残留库;pointer 退 base 是向后兼容代价,记风险 |
| reader reload 不看 pointer | commit 后写/touch base 根 `.last_build` 戳 → daemon `_maybe_reload_project` 探 mtime → evict → 下次 `_get_client` 经 `resolve_current` 拿新 build(天然串联,无需改 reload 逻辑) |

**真机验证清单(WSL,核心是 SIGKILL 模拟)**:full rebuild 建到 `builds/<id>/` 非根库 / 改 .md 增量后 search_docs 立即命中(验增量未退化)/ `kill -9` 重建中途的 worker 后 current 仍指旧 build、search_docs 不中断(验 handoff 生效)/ 连切两次 full 后 gc 不删正被读目录。

## 九、scope 扩到 code_vec + 第二轮兄弟评审(2026-06-14)

用户指出 chroma 不止 docs 一类库。核实:chroma 系**两类 per-project 库都要接**(覆盖完整 chroma 系)——
- `chroma/docs/<pid>/`(platform_docs,reader=`_models._get_client`,writer=`indexer.index`)
- `chroma/code_vec/<pid>/`(代码向量,reader=`query_code_vectors`,writer=`_build_locked`)
- `chroma/`(根,agent-memory)**不做**(主在 PG,本地 fallback,reindex 频率极低)。
codegraph/graph(sqlite WAL 已隔离)仍不做。

第二轮派三兄弟(抽象接缝/YAGNI/高可用)评审"两库怎么复用 handoff 核"。**裁决 + 修正**:

**① 放弃 context manager,用显式调核原语**(YAGNI+高可用兄弟):
- commit 是**发布主效果**,藏进 CM 退出路径 → 看不出"这里原子切库";`code_vec._build_locked` 已有 `try_acquire_reindex_lock` + try/finally,再套 CM = 两套生命周期交错。核已在 `index_handoff`(单一真值),显式调原语零抽象税,重复的是编排顺序非逻辑。
- **锁绝不吞进封装**(职责正交:锁=互斥/handoff=可见性);**锁继续挂 base 根**(下移 build dir → 两进程各拿各锁=互斥失效),handoff 在锁内。

**② 三个真漏洞(必须纳入,否则上线炸)**:
| 漏洞 | 后果 | 修法 |
|---|---|---|
| 🔴 reader 探活路径未改 | `query_code_vectors:352` 探 `persist/_MANIFEST_NAME` → handoff 后 base 根无 manifest → **code_vec lane 全项目静默空返**(fail-soft 无报错) | 探活 + client 走 `resolve_current(persist)`;同 PR 加断言测试 |
| 🔴 `shutil.rmtree(persist)`(code_vec:455) | persist 变 handoff base → rmtree 连 `builds/`+`current.json` 删掉 → reader 读空 | 删整行,写库改 `begin_build(persist, bid)` |
| 🔴 孤儿 side dir | writer 异常 → side 留盘,gc 按 mtime 删不掉最新失败 side → 堆积 | 异常路径显式 `rmtree(side)` 清,不靠 gc |

**③ 其余纳入**:keep 按库类型(docs=2 / code_vec 大库=1,side ×2~3 磁盘);R5 `_existing_chroma_healthy` 走 `resolve_current`(探当前 build,坏退 full→begin 新 side);code_vec reader 进程内单例 client 以 `resolve_current` 路径为 key(current 变→path 变→新 client,旧自然弃)。

**④ 显式编排形态(两库各自,调同一组核原语)**:
```
if full_rebuild:
    gc_builds(base, keep=K)                  # 建新前清旧(reader 已 reload)
    bid = new_build_id(commit)               # 确定性(失败重试复用同 id, begin 自清)
    build_dir = begin_build(base, bid)
    try: ...写 build_dir...
    except: rmtree(build_dir); raise         # 清孤儿
    commit_build(base, bid)                   # 发布主效果, 显式可见
else:
    build_dir = resolve_current(base)        # 增量原地, 不 commit
    ...写 build_dir...
```
`new_build_id` 是 index_handoff 新增的确定性 helper(调用方传 commit/时间戳,不内嵌不确定源)。

## 十、真机验证通过(2026-06-14,WSL 对标云生产)

阶段 2+3 实现(commit `821cb37`)后 WSL 真机验证,全过:

| 验证 | 方式 | 结果 |
|---|---|---|
| docs full handoff | 临时 `PLATFORM_DATA_DIR` 真跑 `indexer.index(force=True)`(141 docs / 2057 chunk CPU 真嵌入)| `builds/<821cb37...-ts>/` + `current.json` 建; `READER_COUNT 2057`(reader 经 `chroma_docs_data_dir`→`resolve_current` 真读到 side build); integrity `count==manifest` |
| docs 增量原地 | 同库再跑 `index(force=False)` | `INCR 0 0` + resolve 同一 build(不切库)|
| **SIGKILL 崩溃** | 真 chroma 库: build A commit → build B 写半 **不 commit**(模拟 kill)| `resolve_current==A` / `READER count==2`(读旧 A 不撞 B 半成品)/ B 孤儿在 builds 但 current≠B |
| gc keep=2 | build C commit 后 `gc_builds(keep=2)` | 删最老 A, 保 B+C, `current==C` |
| code_vec reader 探活修复 | build X commit(manifest 进 build dir)| `resolve_current(base)/_MANIFEST_NAME` 命中(修复); base 根/manifest 不存在(旧 bug 会恒空返, 证实漏洞)|
| WSL 环境 | `.venv` import + 63 单测 | 全过 |

writer code_vec 与 docs **同构**(同一套核 + 同样显式编排),已单测覆盖 + docs 真机证机制等价。

**待上线**(reader 新代码在线上 daemon 未生效, 向后兼容故无害): 重启 `codev-mcp-platform-docs`(docs reader)+ `codev-reindex`(worker 跑新 writer)+ `codev-agent`(code_vec reader)。现有库无 pointer → reader `resolve_current` 退 base = 现状行为; worker 下次 full rebuild 才产 pointer + builds/, handoff 自然生效。
